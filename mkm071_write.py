#!/usr/bin/env python3
"""MKM071 gated button-slot writer (companion to mkm071_probe.py, wired mode).

Only three write opcodes are ever sent: 0x50 (set one button-matrix slot), 0x54 (DPI
option block) and 0x53 (option block 0, used only for the light setting here).
Both option-block writes are read-modify-write: the whole block is read, one field is
changed, everything else is echoed back. No firmware, bootloader or reset commands exist here.

Safety flow for every write:
  1. Verify model id (0x8F) is 1833 (MKM071) unless --force-model.
  2. Save a full backup JSON (matrix, option blocks, profile) before touching anything.
  3. Dry run by default; nothing is sent without --yes.
  4. After writing, re-read the matrix and report whether the slot really changed.
  5. --restore BACKUP.json writes back every slot that differs from the backup.

Usage (mouse wired, same venv as the probe):
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --backup-only
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --set-slot 7 --action 0,0,34,0          # dry run
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --set-slot 7 --action 0,0,34,0 --yes
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --restore backup-XXXX.json --yes
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --set-dpi 0,1000 --yes     # stage 0 -> 1000 DPI
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --set-stage-color 0,FF8000 --yes   # DPI stage colour
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --set-stage 2 --yes                 # active stage
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --profile 2 --set-slot 7 --action 0,0,34,0 --yes   # edit profile 2 without switching
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --restore backup-XXXX.json --yes   # all five profiles + option block 0
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --set-opt0 rate=500,debounce=4 --yes
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --profile 0 --set-report-rate 500 --yes
  sudo .venv/bin/python mkm071_write.py --pid 0x4026 --set-light always,FF0000,4 --yes
Light spec: MODE[,RRGGBB[,BRIGHTNESS 0-4[,SPEED 0-4]]]; MODE = off|always|breath|neon|wave|dazzle|laser.
Action bytes are [type, 0, code, arg]; e.g. 0,0,34,0 = keyboard key '5' (HID 0x22).
"""
import argparse
import datetime
import json
import sys
import time

import mkm071_probe as P

OP_SET_SLOT = 0x50
OP_SET_OPT1 = 0x54
OP_SET_OPT0 = 0x53
OP_SET_PROFILE = 0x05
OP_GET_PROFILE = 0x85
MAX_PROFILE = 4  # the listing says 5 profiles (0..4)
LIGHT_MODES = {'off': 0, 'always': 1, 'breath': 2, 'neon': 3, 'wave': 4,
               'dazzle': 5, 'laser': 6}
MAXSPEED = 4
OP_SET_REPORT = 0x04     # per-profile report rate: [4, profile, code]
RATE_TO_CODE = {1000: 1, 500: 2, 250: 4, 125: 8}
# Fields of option block 0 this tool will change (offset, width, min, max); ranges follow
# GearMaster's settings for this model (debounce max 10, sleep 0..60 min, no deep-sleep UI).
OPT0_FIELDS = {"debounce": (10, 1, 0, 10), "sleep_bt": (40, 2, 0, 3600),
               "sleep_24": (44, 2, 0, 3600), "x_sens": (50, 1, 10, 200),
               "y_sens": (51, 1, 10, 200), "lift": (52, 1, 0, 3),
               "angle_snap": (53, 1, 0, 1), "charging": (60, 1, 0, 1)}
MAX_DPI = 12000
OP_SET_MACRO = 0x16      # SET_MACRO_SIMPLE: pages of 56 bytes after an 8-byte header
MACRO_COUNT = 20         # MACROMAX in GearMaster
MACRO_SIZE = 256
MACRO_PAGE = 56
MACRO_LIMIT = 248        # leave room for the 4-byte zero terminator
MACRO_BUTTONS = {"left": 0xF0, "right": 0xF1, "middle": 0xF2, "back": 0xF3, "forward": 0xF4}
MKM071_ID = 1833


def build_set_slot(profile, slot, action):
    action = bytes(action)
    if not 0 <= slot < 16:
        raise ValueError("slot must be 0..15")
    if len(action) != 4:
        raise ValueError("action must be 4 bytes")
    frame = bytearray(64)
    frame[0], frame[1], frame[2] = OP_SET_SLOT, profile, slot
    frame[7] = P.checksum7(frame)
    frame[8:12] = action
    return bytes(frame)


def build_set_opt1(profile, reply, stage=None, dpi=None, color_stage=None,
                   rgb=None, active=None):
    """Rebuild the 0x54 write from a 0xD4 read so nothing else in the block changes.
    GearMaster writes [0x54, profile, current_stage, stage_count, 0,0,0,chk] + 56 bytes
    (8 X dpi u16, 8 Y dpi u16, 8 RGB colours). Colours MUST be echoed back or they are blanked."""
    reply = bytes(reply)
    frame = bytearray(64)
    frame[0], frame[1], frame[2], frame[3] = OP_SET_OPT1, profile, reply[2], reply[3]
    frame[8:64] = reply[8:64]
    if stage is not None:
        if not 0 <= stage < reply[3]:
            raise ValueError("stage must be 0..%d" % (reply[3] - 1))
        if dpi % 50 or not 50 <= dpi <= MAX_DPI:
            raise ValueError("dpi must be a multiple of 50 in 50..%d" % MAX_DPI)
        for base in (8, 24):  # X table, then Y table (same value, like GearMaster's banXY)
            frame[base + 2 * stage] = dpi & 0xFF
            frame[base + 2 * stage + 1] = dpi >> 8
    if color_stage is not None:  # stage indicator colour (what the wheel LED shows)
        if not 0 <= color_stage < reply[3]:
            raise ValueError("stage must be 0..%d" % (reply[3] - 1))
        if not 0 <= rgb <= 0xFFFFFF:
            raise ValueError("rgb must be 6 hex digits")
        o = 40 + 3 * color_stage
        frame[o:o + 3] = bytes([rgb >> 16, rgb >> 8 & 255, rgb & 255])
    if active is not None:  # make this the active stage (header byte 2)
        if not 0 <= active < reply[3]:
            raise ValueError("stage must be 0..%d" % (reply[3] - 1))
        frame[2] = active
    frame[7] = P.checksum7(frame)
    return bytes(frame)


def build_set_profile(n):
    if not 0 <= n <= MAX_PROFILE:
        raise ValueError("profile must be 0..%d" % MAX_PROFILE)
    frame = bytearray(64)
    frame[0], frame[1] = OP_SET_PROFILE, n
    frame[7] = P.checksum7(frame)
    return bytes(frame)


def switch_profile(link, n, rid, wait):
    link.send_feature_report(bytes([rid]) + build_set_profile(n))
    time.sleep(wait)
    try:
        link.get_feature_report(rid, P.REPORT_LEN + 1)
    except Exception:
        pass
    time.sleep(wait)
    return P.query(link, OP_GET_PROFILE, rid=rid, wait=wait)[1]


def parse_light_spec(spec):
    parts = spec.split(",")
    mode = parts[0].lower()
    if mode not in LIGHT_MODES:
        raise ValueError("mode must be one of %s" % "|".join(LIGHT_MODES))
    rgb = int(parts[1], 16) if len(parts) > 1 and parts[1] else None
    bright = int(parts[2]) if len(parts) > 2 and parts[2] else 4
    speed = int(parts[3]) if len(parts) > 3 and parts[3] else 0
    if rgb is not None and not 0 <= rgb <= 0xFFFFFF:
        raise ValueError("rgb must be 6 hex digits")
    if not 0 <= bright <= 4 or not 0 <= speed <= 4:
        raise ValueError("brightness and speed must be 0..4")
    return {"mode": mode, "rgb": rgb, "bright": bright, "speed": speed}


def build_set_opt0(reply, light=None, fields=None):
    """0x53 write built from a 0xD3 read. Only bytes 24..31 (light) change; the sleep
    timers, sensitivity, rate, debounce, flags, battery colours are echoed."""
    reply = bytes(reply)
    frame = bytearray(64)
    frame[0] = OP_SET_OPT0
    frame[8:64] = reply[8:64]
    if light is not None:
        code = LIGHT_MODES[light["mode"]]
        buf = bytearray(8)
        if code:
            buf[0] = code
            buf[1] = MAXSPEED - light["speed"]
            buf[2] = light["bright"]
            buf[3] = 7  # "normal" colour mode (GearMaster NORMAL)
            if light["rgb"] is not None:
                c = 0xFAFAFA if light["rgb"] == 0xFFFFFF else light["rgb"]  # GearMaster quirk
                buf[4:7] = bytes([c >> 16, c >> 8 & 255, c & 255])
            else:
                buf[4:7] = reply[28:31]
        frame[24:32] = buf
    for name, val in (fields or {}).items():
        if name == "rate":
            frame[9] = RATE_TO_CODE[val]
            continue
        off, width, lo, hi = OPT0_FIELDS[name]
        frame[off:off + width] = val.to_bytes(width, "little")
    frame[7] = P.checksum7(frame)
    return bytes(frame)


def parse_opt0_spec(spec):
    """'rate=500,debounce=4,sleep=60' -> dict. sleep sets both the Bluetooth and 2.4G timers."""
    out = {}
    for part in spec.split(","):
        k, v = part.split("=")
        k, v = k.strip().lower(), int(v, 0)
        if k == "sleep":
            out["sleep_bt"] = out["sleep_24"] = v
            k = None
        if k == "rate":
            if v not in RATE_TO_CODE:
                raise ValueError("rate must be one of %s" % sorted(RATE_TO_CODE))
        elif k is not None:
            if k not in OPT0_FIELDS:
                raise ValueError("unknown field %r; use rate, sleep, %s" % (
                    k, ", ".join(OPT0_FIELDS)))
            if not OPT0_FIELDS[k][2] <= v <= OPT0_FIELDS[k][3]:
                raise ValueError("%s must be %d..%d" % (k, OPT0_FIELDS[k][2], OPT0_FIELDS[k][3]))
        if k is not None:
            out[k] = v
    for k in ("sleep_bt", "sleep_24"):
        if k in out and not OPT0_FIELDS[k][2] <= out[k] <= OPT0_FIELDS[k][3]:
            raise ValueError("sleep must be 0..3600 seconds")
    return out


def build_set_rate(profile, hz):
    if hz not in RATE_TO_CODE:
        raise ValueError("rate must be one of %s" % sorted(RATE_TO_CODE))
    if not 0 <= profile <= MAX_PROFILE:
        raise ValueError("profile must be 0..%d" % MAX_PROFILE)
    frame = bytearray(64)
    frame[0], frame[1], frame[2] = OP_SET_REPORT, profile, RATE_TO_CODE[hz]
    frame[7] = P.checksum7(frame)
    return bytes(frame)


def write_opt0(link, frame, rid, wait):
    link.send_feature_report(bytes([rid]) + frame)
    time.sleep(wait)
    try:
        link.get_feature_report(rid, P.REPORT_LEN + 1)
    except Exception:
        pass
    time.sleep(wait)
    return bytes(P.query(link, P.OP_GET_OPT0, rid=rid, wait=wait))


def write_opt1(link, frame, profile, rid, wait):
    link.send_feature_report(bytes([rid]) + frame)
    time.sleep(wait)
    try:
        link.get_feature_report(rid, P.REPORT_LEN + 1)
    except Exception:
        pass
    time.sleep(wait)
    return bytes(P.query(link, P.OP_GET_OPT1, profile, rid=rid, wait=wait))


def read_matrix(link, profile, rid, wait):
    return bytes(P.query(link, P.OP_GET_MATRIX, profile, rid=rid, wait=wait,
                         echo=False)[:64])


# ------------------------------------------------------------------ macros
def hid_usage(name):
    """HID keyboard usage for a key name: a-z, 0-9, space, enter, esc, backspace, tab, f1-f12, shift."""
    n = name.lower()
    if len(n) == 1 and "a" <= n <= "z":
        return 4 + ord(n) - ord("a")
    if len(n) == 1 and "1" <= n <= "9":
        return 30 + ord(n) - ord("1")
    named = {"0": 39, "enter": 40, "esc": 41, "backspace": 42, "tab": 43, "space": 44,
             "shift": 225, "ctrl": 224, "alt": 226}
    if n in named:
        return named[n]
    if n.startswith("f") and n[1:].isdigit() and 1 <= int(n[1:]) <= 12:
        return 57 + int(n[1:])
    raise ValueError("unknown key %r" % name)


def macro_text_events(text, delay=10):
    ev = []
    for ch in text:
        key = "space" if ch == " " else ch
        shift = ch.isalpha() and ch.isupper()
        if shift:
            ev += [{"type": "key", "action": "down", "usage": hid_usage("shift")},
                   {"type": "delay", "value": delay}]
        ev += [{"type": "key", "action": "down", "usage": hid_usage(key)},
               {"type": "delay", "value": delay},
               {"type": "key", "action": "up", "usage": hid_usage(key)},
               {"type": "delay", "value": delay}]
        if shift:
            ev += [{"type": "key", "action": "up", "usage": hid_usage("shift")},
                   {"type": "delay", "value": delay}]
    return ev


def parse_macro_spec(spec, delay=10):
    """tokens: tap:KEY down:KEY up:KEY delay:MS click:left|right|middle|back|forward
    mdown:BUTTON mup:BUTTON move:DX:DY text:STRING (comma separated; text may not contain commas)."""
    ev = []
    for tok in spec.split(","):
        k, _, rest = tok.strip().partition(":")
        k = k.lower()
        if k == "text":
            ev += macro_text_events(rest, delay)
        elif k == "tap":
            ev += [{"type": "key", "action": "down", "usage": hid_usage(rest)},
                   {"type": "delay", "value": delay},
                   {"type": "key", "action": "up", "usage": hid_usage(rest)},
                   {"type": "delay", "value": delay}]
        elif k in ("down", "up"):
            ev.append({"type": "key", "action": k, "usage": hid_usage(rest)})
        elif k == "delay":
            ev.append({"type": "delay", "value": int(rest, 0)})
        elif k in ("click", "mdown", "mup"):
            code = MACRO_BUTTONS[rest.lower()]
            acts = ["down", "up"] if k == "click" else ["down" if k == "mdown" else "up"]
            for act in acts:
                ev += [{"type": "mouse_button", "action": act, "code": code}]
                if k == "click":
                    ev.append({"type": "delay", "value": delay})
        elif k == "move":
            dx, dy = [int(x) for x in rest.split(":")]
            ev.append({"type": "mouse_move", "dx": dx, "dy": dy})
        else:
            raise ValueError("unknown macro token %r" % tok)
    for e in ev:
        if e["type"] == "delay" and not 0 <= e["value"] <= 0xFFFF:
            raise ValueError("delay must be 0..65535")
        if e["type"] == "mouse_move" and not (-128 <= e["dx"] <= 127 and -128 <= e["dy"] <= 127):
            raise ValueError("move dx/dy must be -128..127")
    return ev


def build_macro_buffer(repeat, events):
    """Port of GearMaster's setMacro. Returns the 256-byte macro buffer."""
    if not 0 <= repeat <= 0xFFFF:
        raise ValueError("repeat must be 0..65535")
    o = bytearray(MACRO_SIZE)
    o[0:2] = repeat.to_bytes(2, "little")
    u = 2
    pending_delay = False  # GearMaster's flag "i": the last event still owes a u16 delay
    t = 0

    def put(*bs):
        nonlocal u
        if u + len(bs) > MACRO_LIMIT:
            raise ValueError("macro too long (limit %d bytes)" % MACRO_LIMIT)
        for b in bs:
            o[u] = b & 0xFF
            u += 1

    while t < len(events):
        a = events[t]
        if a["type"] != "delay" and pending_delay:
            put(0, 0)
        consumed = False

        def short_delay(is_move):
            nonlocal pending_delay, consumed
            r = 0
            if t + 1 < len(events) and events[t + 1]["type"] == "delay" and events[t + 1]["value"] <= 127:
                r = events[t + 1]["value"]
            if not is_move:
                r = (r | 0x80) if a["action"] == "down" else (r & 0x7F)
            put(r)
            pending_delay = True
            if r & 0x7F:
                pending_delay = False
                consumed = True

        if a["type"] == "delay":
            if pending_delay:
                put(a["value"] & 0xFF, a["value"] >> 8)
            else:
                put(0, 0, a["value"] & 0xFF, a["value"] >> 8)
            pending_delay = False
        elif a["type"] == "key":
            put(a["usage"])
            pending_delay = True
            short_delay(False)
        elif a["type"] == "mouse_button":
            put(a["code"])
            short_delay(False)
        elif a["type"] == "mouse_move":
            put(0xF9)
            short_delay(True)
            put(a["dx"], a["dy"])
        t += 2 if consumed else 1
    return bytes(o)


def macro_frames(idx, buf):
    """Frames GearMaster sends (`_setMacro`): header + 56-byte pages up to the last used page."""
    if not 0 <= idx < MACRO_COUNT:
        raise ValueError("macro index must be 0..%d" % (MACRO_COUNT - 1))
    buf = bytes(buf).ljust(MACRO_PAGE * 5, b"\0")
    pages = [buf[i * MACRO_PAGE:(i + 1) * MACRO_PAGE] for i in range(5)]
    used = 1
    for i, pg in enumerate(pages):
        if any(pg):
            used = i + 1
    last = pages[used - 1]
    tail = max([k + 1 for k, b in enumerate(last) if b] or [0])
    total = MACRO_PAGE * (used - 1) + tail
    frames = []
    for f in range(used):
        fr = bytearray(64)
        fr[0], fr[1], fr[2], fr[3] = OP_SET_MACRO, idx, f, total
        fr[4] = 1 if f == used - 1 else 0
        fr[8:64] = pages[f]
        fr[7] = P.checksum7(fr)
        frames.append(bytes(fr))
    return frames


def write_macro(link, idx, buf, rid, wait):
    for fr in macro_frames(idx, buf):
        link.send_feature_report(bytes([rid]) + fr)
        time.sleep(wait)
        try:
            link.get_feature_report(rid, P.REPORT_LEN + 1)
        except Exception:
            pass
        time.sleep(wait)
    time.sleep(0.1)
    return P.read_macro(link, idx, rid=rid, wait=wait)


def macro_norm(raw):
    """The meaningful part of a macro buffer. The mouse erases a slot to 0xFF before writing, so
    bytes after the terminator read back as 0xFF; they are not part of the macro."""
    raw = bytes(raw)
    if not any(raw[2:6]) or raw == b"\xff" * len(raw):
        return b""
    return raw[:P.parse_macro(raw)["used_bytes"]]


def macro_is_empty(raw):
    return macro_norm(raw) == b""


def make_backup(link, rid, wait):
    rev = P.query(link, P.OP_GET_REV, rid=rid, wait=wait)
    profile = P.query(link, P.OP_GET_PROFILE, rid=rid, wait=wait)[1]
    profiles = {}
    for p in range(MAX_PROFILE + 1):  # the mouse has 5 profiles (verified: all five read back)
        try:
            profiles[str(p)] = {
                "matrix_hex": read_matrix(link, p, rid, wait).hex(),
                "opt1_hex": bytes(P.query(link, P.OP_GET_OPT1, p, rid=rid,
                                          wait=wait)).hex()}
        except Exception as e:
            profiles[str(p)] = {"error": "%s: %s" % (type(e).__name__, e)}
    macros = {}
    for m in range(MACRO_COUNT):
        try:
            macros[str(m)] = P.read_macro(link, m, rid=rid, wait=wait).hex()
        except Exception as e:
            macros[str(m)] = {"error": "%s: %s" % (type(e).__name__, e)}
            break
    return {
        "saved": datetime.datetime.now().isoformat(timespec="seconds"),
        "model_id": rev[1] | rev[2] << 8,
        "profile": profile,
        "matrix_hex": read_matrix(link, profile, rid, wait).hex(),
        "opt0_hex": bytes(P.query(link, P.OP_GET_OPT0, rid=rid, wait=wait)).hex(),
        "opt1_hex": bytes(P.query(link, P.OP_GET_OPT1, profile, rid=rid,
                                  wait=wait)).hex(),
        "profiles": profiles,
        "macros": macros,
    }


def write_slot(link, profile, slot, action, rid, wait):
    link.send_feature_report(bytes([rid]) + build_set_slot(profile, slot, action))
    time.sleep(wait)
    try:
        link.get_feature_report(rid, P.REPORT_LEN + 1)  # drain the ack, content unknown
    except Exception:
        pass
    time.sleep(wait)
    return read_matrix(link, profile, rid, wait)


def slot_of(matrix, i):
    return list(matrix[4 * i:4 * i + 4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=P.auto_int, default=0x4026)
    ap.add_argument("--iface", type=int, default=2)
    ap.add_argument("--rid", type=int, default=0)
    ap.add_argument("--wait", type=float, default=0.02)
    ap.add_argument("--backup-only", action="store_true")
    ap.add_argument("--profile", type=int, metavar="N",
                    help="profile to edit (default: the active one); no switching happens")
    ap.add_argument("--set-slot", type=int)
    ap.add_argument("--action", help="four comma-separated bytes, e.g. 0,0,34,0")
    ap.add_argument("--set-dpi", metavar="STAGE,DPI", help="e.g. 0,1000")
    ap.add_argument("--set-stage-color", metavar="STAGE,RRGGBB", help="e.g. 0,FF8000")
    ap.add_argument("--set-stage", type=int, metavar="N", help="make stage N active")
    ap.add_argument("--set-profile", type=int, metavar="N", help="switch the active profile (0-4)")
    ap.add_argument("--set-opt0", metavar="SPEC",
                    help="e.g. rate=500,debounce=4,sleep=60,x_sens=100,lift=1,angle_snap=0")
    ap.add_argument("--set-report-rate", type=int, metavar="HZ",
                    help="per-profile report rate via opcode 0x04 (use --profile N)")
    ap.add_argument("--set-macro", type=int, metavar="IDX", help="write macro slot IDX (0-19)")
    ap.add_argument("--macro-text", metavar="TEXT", help="type TEXT (letters, digits, space)")
    ap.add_argument("--macro-spec", metavar="SPEC",
                    help="e.g. tap:h,delay:50,click:left,move:10:0,text:ab (see parse_macro_spec)")
    ap.add_argument("--macro-repeat", type=int, default=1)
    ap.add_argument("--macro-delay", type=int, default=10, help="delay value used by text/tap/click")
    ap.add_argument("--clear-macro", type=int, metavar="IDX", help="write an empty macro to slot IDX")
    ap.add_argument("--set-light", metavar="SPEC", help="e.g. always,FF0000,4")
    ap.add_argument("--restore", metavar="BACKUP.json")
    ap.add_argument("--yes", action="store_true", help="actually send writes")
    ap.add_argument("--force-model", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    link = P.UsbLink(pid=a.pid, iface=a.iface)
    try:
        bk = make_backup(link, a.rid, a.wait)
        if bk["model_id"] != MKM071_ID and not a.force_model:
            sys.exit("model id %d is not MKM071 (%d); refusing (use --force-model)"
                     % (bk["model_id"], MKM071_ID))
        name = "backup-%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        with open(name, "w") as f:
            json.dump(bk, f, indent=2)
        print("backup saved:", name)
        if a.backup_only:
            return
        target = a.profile if a.profile is not None else bk["profile"]
        if a.set_profile is None and not a.restore and not 0 <= target <= MAX_PROFILE:
            sys.exit("the mouse reports active profile %d, outside 0..%d; use --profile N to "
                     "pick one, or --set-profile N to switch to a valid profile first"
                     % (target, MAX_PROFILE))
        state = {int(p): d for p, d in bk["profiles"].items() if "error" not in d}
        slot_plan = []   # (profile, slot, action)
        opt1_plan = []   # (profile, frame)
        opt0_frame = None
        macro_plan = []  # (idx, buffer)
        cur_opt0 = bytes.fromhex(bk["opt0_hex"])
        cur_macros = {int(k): bytes.fromhex(v) for k, v in bk.get("macros", {}).items()
                      if isinstance(v, str)}

        def need(p):
            if p not in state:
                sys.exit("could not read profile %d from the mouse" % p)
            return (bytes.fromhex(state[p]["matrix_hex"]),
                    bytes.fromhex(state[p]["opt1_hex"]))

        if a.restore:
            with open(a.restore) as f:
                old = json.load(f)
            if old["model_id"] != bk["model_id"]:
                sys.exit("backup is for a different model")
            oldp = {int(p): d for p, d in old.get("profiles", {}).items()
                    if "error" not in d}
            if not oldp:  # backup from an older version of this tool: one profile only
                oldp = {old["profile"]: {"matrix_hex": old["matrix_hex"],
                                         "opt1_hex": old["opt1_hex"]}}
            if a.profile is not None:
                oldp = {p: d for p, d in oldp.items() if p == a.profile}
            for p, d in sorted(oldp.items()):
                have_m, have_o = need(p)
                want_m, want_o = bytes.fromhex(d["matrix_hex"]), bytes.fromhex(d["opt1_hex"])
                slot_plan += [(p, i, slot_of(want_m, i)) for i in range(16)
                              if slot_of(want_m, i) != slot_of(have_m, i)]
                if want_o[2:4] != have_o[2:4] or want_o[8:64] != have_o[8:64]:
                    opt1_plan.append((p, build_set_opt1(p, want_o)))
            old_opt0 = bytes.fromhex(old["opt0_hex"])
            if a.profile is None and old_opt0[8:64] != cur_opt0[8:64]:
                opt0_frame = build_set_opt0(old_opt0)
            if a.profile is None:
                for k, v in old.get("macros", {}).items():
                    if not isinstance(v, str) or int(k) not in cur_macros:
                        continue
                    want_n = macro_norm(bytes.fromhex(v))
                    if want_n != macro_norm(cur_macros[int(k)]):
                        macro_plan.append((int(k), want_n.ljust(MACRO_SIZE, b"\0")))
        elif a.set_slot is not None:
            if not a.action:
                sys.exit("--action required")
            slot_plan = [(target, a.set_slot, [int(x, 0) for x in a.action.split(",")])]
        elif a.set_dpi:
            st, val = [int(x, 0) for x in a.set_dpi.split(",")]
            opt1_plan = [(target, build_set_opt1(target, need(target)[1], st, val))]
        elif a.set_stage_color:
            st, col = a.set_stage_color.split(",")
            opt1_plan = [(target, build_set_opt1(target, need(target)[1],
                                                 color_stage=int(st, 0), rgb=int(col, 16)))]
        elif a.set_stage is not None:
            opt1_plan = [(target, build_set_opt1(target, need(target)[1], active=a.set_stage))]
        elif a.set_profile is not None:
            build_set_profile(a.set_profile)  # validates
            print("profile: %d -> %d   frame=%s" % (
                bk["profile"], a.set_profile, build_set_profile(a.set_profile)[:8].hex()))
            if not a.yes:
                print("dry run: add --yes to send")
                return
            now = switch_profile(link, a.set_profile, a.rid, a.wait)
            print("active profile now %d: %s" % (
                now, "OK" if now == a.set_profile else "NOT APPLIED"))
            return
        elif a.set_report_rate is not None:
            fr = build_set_rate(target, a.set_report_rate)
            have = P.query(link, P.OP_GET_REPORT, target, rid=a.rid, wait=a.wait)[2]
            print("profile %d report rate code %d -> %d   frame=%s" % (
                target, have, fr[2], fr[:8].hex()))
            if not a.yes:
                print("dry run: add --yes to send")
                return
            link.send_feature_report(bytes([a.rid]) + fr)
            time.sleep(a.wait)
            try:
                link.get_feature_report(a.rid, P.REPORT_LEN + 1)
            except Exception:
                pass
            time.sleep(a.wait)
            now = P.query(link, P.OP_GET_REPORT, target, rid=a.rid, wait=a.wait)[2]
            print("profile %d report rate code now %d: %s" % (
                target, now, "OK" if now == fr[2] else "NOT APPLIED"))
            return
        elif a.set_macro is not None or a.clear_macro is not None:
            if a.clear_macro is not None:
                idx, buf = a.clear_macro, bytes(MACRO_SIZE)
            else:
                idx = a.set_macro
                if bool(a.macro_text) == bool(a.macro_spec):
                    sys.exit("give exactly one of --macro-text / --macro-spec")
                ev = (macro_text_events(a.macro_text, a.macro_delay) if a.macro_text
                      else parse_macro_spec(a.macro_spec, a.macro_delay))
                buf = build_macro_buffer(a.macro_repeat, ev)
            have = cur_macros.get(idx)
            if have is None:
                sys.exit("could not read macro %d from the mouse" % idx)
            if macro_norm(have) != macro_norm(buf):
                macro_plan = [(idx, buf)]
        elif a.set_opt0:
            opt0_frame = build_set_opt0(cur_opt0, fields=parse_opt0_spec(a.set_opt0))
        elif a.set_light:
            opt0_frame = build_set_opt0(cur_opt0, parse_light_spec(a.set_light))
        else:
            sys.exit("nothing to do: use --backup-only, --set-slot, --set-dpi, "
                     "--set-stage-color, --set-stage, --set-profile, --set-opt0, "
                     "--set-report-rate, --set-macro, --clear-macro, --set-light or --restore")

        if not slot_plan and not opt1_plan and opt0_frame is None and not macro_plan:
            print("nothing differs; no writes needed")
            return
        for p, slot, act in slot_plan:
            print("profile %d slot %2d: %s -> %s   frame=%s" % (
                p, slot, slot_of(need(p)[0], slot), act,
                build_set_slot(p, slot, act)[:12].hex()))
        for p, fr in opt1_plan:
            cur = need(p)[1]
            before, after_p = P.parse_opt1(cur), P.parse_opt1(fr)
            print("profile %d dpi_x: %s -> %s\n  stage: %s -> %s   frame=%s" % (
                p, before["dpi_x"][:before["stage_count"]],
                after_p["dpi_x"][:after_p["stage_count"]],
                before["current_stage"], after_p["current_stage"], fr[:12].hex()))
            print("  stage colours: %s -> %s" % (
                ["%06X" % int.from_bytes(cur[40 + 3 * i:43 + 3 * i], "big")
                 for i in range(before["stage_count"])],
                ["%06X" % int.from_bytes(fr[40 + 3 * i:43 + 3 * i], "big")
                 for i in range(before["stage_count"])]))
        if opt0_frame is not None:
            diffs = ["byte %d: %d -> %d" % (i, cur_opt0[i], opt0_frame[i])
                     for i in range(8, 64) if cur_opt0[i] != opt0_frame[i]]
            print("option block 0 changes: %s   frame=%s" % (
                "; ".join(diffs) or "none", opt0_frame[:12].hex()))
        for idx, buf in macro_plan:
            have = cur_macros.get(idx, b"")
            state = "empty" if macro_is_empty(have) else "in use"
            if any(buf):
                dec = P.parse_macro(buf)
                print("macro %d (%s now): repeat %d, %d bytes, events: %s" % (
                    idx, state, dec["repeat"], dec["used_bytes"],
                    " ".join(("%s%s" % (e.get("action", e["type"])[:1], e.get("usage", e.get("code", "")))
                              if e["type"] != "delay" else "%dms" % e["value"])
                             if e["type"] != "mouse_move" else "move(%d,%d)" % (e["dx"], e["dy"])
                             for e in dec["events"])))
            else:
                print("macro %d (%s now): -> empty" % (idx, state))
            for fr in macro_frames(idx, buf):
                print("  frame page %d total_len %d last %d  %s..." % (fr[2], fr[3], fr[4], fr[:16].hex()))
        if not a.yes:
            print("dry run: add --yes to send")
            return
        for idx, buf in macro_plan:
            rb = write_macro(link, idx, buf, a.rid, a.wait)
            ok = macro_norm(rb) == macro_norm(buf)
            print("macro %d now %s: %s" % (idx, macro_norm(rb).hex()[:60] or "(empty)",
                                         "OK" if ok else "NOT APPLIED"))
        for p, slot, act in slot_plan:
            after = write_slot(link, p, slot, act, a.rid, a.wait)
            ok = slot_of(after, slot) == act
            print("profile %d slot %2d now %s: %s" % (p, slot, slot_of(after, slot),
                                                      "OK" if ok else "NOT APPLIED"))
        for p, fr in opt1_plan:
            rb = write_opt1(link, fr, p, a.rid, a.wait)
            same = (rb[8:64] == fr[8:64] and rb[2] == fr[2])
            print("profile %d dpi_x now %s, colours/Y table %s: %s" % (
                p, P.parse_opt1(rb)["dpi_x"][:rb[3]], "kept" if same else "CHANGED",
                "OK" if same else "NOT APPLIED"))
        if opt0_frame is not None:
            rb = write_opt0(link, opt0_frame, a.rid, a.wait)
            ok = rb[8:64] == opt0_frame[8:64]
            parsed = P.parse_opt0(rb)
            print("option block 0 now: rate %s Hz, debounce %s, sleep %s, sens %s/%s, lift %s,"
                  " angle_snap %s, charging %s, light %s: %s" % (
                      parsed["report_rate_hz"], parsed["debounce"], parsed["sleep_s"],
                      parsed["x_sensitivity"], parsed["y_sensitivity"], parsed["lift_cutoff"],
                      parsed["angle_snap"], parsed["charging_switch"], parsed["light"]["type"],
                      "OK" if ok else "NOT APPLIED"))
    finally:
        link.close()


def selftest():
    f = build_set_slot(0, 7, [0, 0, 34, 0])
    assert f[:3] == bytes([0x50, 0, 7]) and f[8:12] == bytes([0, 0, 34, 0])
    assert f[7] == (0xFF - (0x50 + 0 + 7)) & 0xFF and len(f) == 64
    for bad in ((0, 16, [0, 0, 0, 0]), (0, 0, [1, 2, 3])):
        try:
            build_set_slot(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError("bad input accepted")

    class Fake:
        def __init__(self):
            self.m = bytearray.fromhex("0100f000" "08000300" "0100f200" "14000000") + bytearray(48)
            self.reply = b""

        def send_feature_report(self, d):
            fr = d[1:]
            op = fr[0]
            assert op in (0x8F, 0x85, 0x05, 0xD3, 0xD4, 0xD0, 0x50), "unexpected opcode %x" % op
            if op == 0x50:
                self.m[4 * fr[2]:4 * fr[2] + 4] = fr[8:12]
                self.reply = bytes([0x50]) + bytes(63)
            elif op == 0xD0:
                self.reply = bytes(self.m)
            elif op == 0x8F:
                self.reply = bytes([0x8F, 0x29, 0x07]) + bytes(61)
            elif op == 0x05:
                self.prof = fr[1]
                self.reply = bytes([0x05]) + bytes(63)
            elif op == 0x85:
                self.reply = bytes([0x85, getattr(self, "prof", 0)]) + bytes(62)
            else:
                self.reply = bytes([op]) + bytes([0, 1]) + bytes(61)

        def get_feature_report(self, rid, n):
            return b"\x00" + self.reply

        def close(self):
            pass

    link = Fake()
    bk = make_backup(link, 0, 0)
    assert bk["model_id"] == 1833 and bk["matrix_hex"].startswith("0100f000")
    assert switch_profile(link, 2, 0, 0) == 2 and switch_profile(link, 0, 0, 0) == 0
    after = write_slot(link, 0, 7, [0, 0, 34, 0], 0, 0)
    assert slot_of(after, 7) == [0, 0, 34, 0] and slot_of(after, 0) == [1, 0, 240, 0]
    # option block: only the requested stage changes, colours and header preserved
    real = bytearray(64)
    real[0], real[2], real[3] = 0xD4, 4, 6
    for i, v in enumerate([800, 2400, 3200, 6400, 9600, 12000]):
        for base in (8, 24):
            real[base + 2 * i], real[base + 2 * i + 1] = v & 255, v >> 8
    real[40:46] = bytes([255, 0, 0, 0, 255, 0])
    fr = build_set_opt1(0, real, 0, 1000)
    assert fr[0] == 0x54 and fr[2] == 4 and fr[3] == 6 and fr[40:46] == real[40:46]
    assert fr[8] | fr[9] << 8 == 1000 and fr[24] | fr[25] << 8 == 1000
    assert fr[10:24] == real[10:24] and fr[26:64] == real[26:64]
    assert fr[7] == (0xFF - sum(fr[:7])) & 0xFF
    for bad in ((6, 1000), (0, 1025), (0, 12050)):
        try:
            build_set_opt1(0, real, *bad)
        except ValueError:
            pass
        else:
            raise AssertionError("bad dpi accepted %r" % (bad,))
    assert build_set_opt1(0, real)[8:64] == bytes(real[8:64])  # restore path is a pure echo
    fr = build_set_opt1(0, real, color_stage=2, rgb=0xFF8000)
    assert fr[40 + 6:40 + 9] == bytes([255, 128, 0]) and fr[8:46] == real[8:46] and fr[2] == 4
    assert fr[49:64] == real[49:64] and fr[7] == (0xFF - sum(fr[:7])) & 0xFF
    fr = build_set_opt1(0, real, active=2)
    assert fr[2] == 2 and fr[3] == 6 and fr[8:64] == bytes(real[8:64])
    for bad in (dict(color_stage=6, rgb=1), dict(color_stage=0, rgb=0x1000000), dict(active=6)):
        try:
            build_set_opt1(0, real, **bad)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted %r" % bad)
    f = build_set_profile(3)
    assert f[:2] == bytes([5, 3]) and f[7] == (0xFF - 5 - 3) & 0xFF and len(f) == 64
    for bad in (-1, 5):
        try:
            build_set_profile(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("bad profile accepted")
    o = bytearray(range(64)); o[0] = 0xD3
    fr = build_set_opt0(o, fields=parse_opt0_spec("rate=500,debounce=4,sleep=60,x_sens=120,lift=1"))
    assert fr[9] == 2 and fr[10] == 4 and fr[40:42] == bytes([60, 0]) and fr[44:46] == bytes([60, 0])
    assert fr[50] == 120 and fr[52] == 1 and fr[51] == o[51] and fr[24:32] == bytes(o[24:32])
    assert fr[42:44] == bytes(o[42:44]) and fr[46:50] == bytes(o[46:50]) and fr[53:64] == bytes(o[53:64]) and fr[11:40] == bytes(o[11:40])
    for bad in ("rate=900", "debounce=11", "sleep=4000", "bogus=1", "angle_snap=2", "x_sens=5"):
        try:
            parse_opt0_spec(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted %r" % bad)
    f = build_set_rate(2, 500)
    assert f[:3] == bytes([4, 2, 2]) and f[7] == (0xFF - 4 - 2 - 2) & 0xFF
    ev = macro_text_events("hi", 10)
    b = build_macro_buffer(1, ev)
    dec = P.parse_macro(b)
    assert dec["repeat"] == 1
    assert [(e["type"], e.get("action"), e.get("usage"), e.get("value")) for e in dec["events"]] == \
        [("key", "down", 11, None), ("delay", None, None, 10), ("key", "up", 11, None), ("delay", None, None, 10),
         ("key", "down", 12, None), ("delay", None, None, 10), ("key", "up", 12, None), ("delay", None, None, 10)], dec
    assert b[:10].hex() == "01000b8a0b0a0c8a0c0a", b[:10].hex()
    long_ev = parse_macro_spec("tap:a,delay:300,tap:b", 200)           # >127 ms needs the 4-byte form
    dl = P.parse_macro(build_macro_buffer(3, long_ev))
    assert [e["value"] for e in dl["events"] if e["type"] == "delay"] == [200, 200, 300, 200, 200], dl
    mv = P.parse_macro(build_macro_buffer(1, parse_macro_spec("move:5:-3,click:left")))
    assert mv["events"][0] == {"type": "mouse_move", "dx": 5, "dy": -3}, mv
    fr = macro_frames(0, b)
    assert len(fr) == 1 and fr[0][:5].hex() == "1600000a01" and fr[0][3] == len(b.rstrip(b"\0")), fr[0][:8].hex()
    assert (sum(fr[0][:8]) & 0xFF) == 0xFF and fr[0][8:8 + len(b.rstrip(b"\0"))] == b.rstrip(b"\0")
    big = build_macro_buffer(1, macro_text_events("abcdefghijklmnopqrst", 10))
    assert len(macro_frames(5, big)) >= 2 and macro_frames(5, big)[-1][4] == 1 and macro_frames(5, big)[0][4] == 0
    try:
        build_macro_buffer(1, macro_text_events("a" * 70, 10))
    except ValueError:
        pass
    else:
        raise AssertionError("accepted oversize macro")
    # option block 0: only the light bytes change
    o0 = bytearray(range(64))
    o0[0] = 0xD3
    fr = build_set_opt0(o0, parse_light_spec("always,FF0000,4"))
    assert fr[0] == 0x53 and fr[8:24] == o0[8:24] and fr[32:64] == o0[32:64]
    assert fr[24:32] == bytes([1, 4, 4, 7, 255, 0, 0, 0])
    assert fr[7] == (0xFF - sum(fr[:7])) & 0xFF
    assert build_set_opt0(o0, parse_light_spec("always,FFFFFF"))[28:31] == bytes([250, 250, 250])
    assert build_set_opt0(o0, parse_light_spec("breath,00FF00,2,3"))[24:28] == bytes([2, 1, 2, 7])
    assert build_set_opt0(o0, parse_light_spec("off"))[24:32] == bytes(8)
    assert build_set_opt0(o0)[8:64] == bytes(o0[8:64])  # restore path is a pure echo
    for bad in ("sparkle", "always,GG0000", "always,FF0000,5", "always,FF0000,1,9"):
        try:
            parse_light_spec(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("bad light spec accepted %r" % bad)
    print("selftest ok")


if __name__ == "__main__":
    main()
