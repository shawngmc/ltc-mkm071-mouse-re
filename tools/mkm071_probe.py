#!/usr/bin/env python3
"""Read-only probe for Rongyuan-family mice (LTC MKM071 / GM025 / ...).

Protocol notes: see docs/protocol.md (derived from static analysis of GearMaster 470.1.80).
Nothing here has been verified against a command reply from hardware yet.

Safety: this tool only sends opcodes from READ_OPCODES (device ID, firmware,
profile, report rate, option blocks, button matrix). It has no write path, and it
never sends bootloader, reset or firmware commands.

Usage:
  python3 tools/mkm071_probe.py --selftest      # offline parser/checksum tests
  python3 tools/mkm071_probe.py --desc          # read HID report descriptors from sysfs (no device access)
  python3 tools/mkm071_probe.py --list          # list hidapi interfaces for VID 3151
  python3 tools/mkm071_probe.py --dump          # query via hidapi/hidraw
  sudo python3 tools/mkm071_probe.py --dump --usb [--pid 0x4027] [--iface 2] [--rid 0]
                                           # query via libusb control transfers

--usb is needed when the config interface has no hidraw node (the 2.4G dongle
binds interface 2 as hiddev only). It temporarily detaches the kernel driver from
that one interface and re-attaches it on exit.
Needs `pip install hidapi` (hidapi mode) or `pip install pyusb` + libusb (--usb).
"""
import argparse
import glob
import json
import os
import sys
import time

VID = 0x3151
USAGE_PAGE = 0xFFFF
USAGE = 0x02
REPORT_LEN = 64  # payload bytes; the HID layer adds the report-ID byte

# Whitelist of query-only opcodes (see docs/protocol.md section 4)
OP_GET_REV = 0x8F
OP_GET_REPORT = 0x84   # report rate for the profile given in byte 1 (mouse class)
OP_GET_PROFILE = 0x85  # current profile in reply byte 1 (0x84 only echoes the profile you ask about)
OP_GET_OPT0 = 0xD3
OP_GET_OPT1 = 0xD4
OP_GET_MATRIX = 0xD0
OP_GET_ID = 0xF1
OP_GET_FNMATRIX = 0xD1   # FN-layer matrix, same layout as 0xD0 (read-only)
OP_GET_BATTERY = 0x82    # keyboard battery query; untested on mice (read-only)
OP_GET_MACRO = 0x96      # GET_MACRO_SIMPLE: [0x96, macro index, page 0..3], 64 raw bytes per page
MACRO_PAGES = 4
READ_OPCODES = {OP_GET_REV, OP_GET_REPORT, OP_GET_PROFILE, OP_GET_OPT0,
                OP_GET_OPT1, OP_GET_MATRIX, OP_GET_ID, OP_GET_FNMATRIX,
                OP_GET_BATTERY, OP_GET_MACRO}

RATE_CODES = {0x01: 1000, 0x02: 500, 0x04: 250, 0x08: 125,
              0x81: 8000, 0x82: 4000, 0x84: 2000}
LIGHT_TYPES = {0: "off", 1: "always_on", 2: "breathe", 3: "neon", 4: "wave",
               5: "dazzling", 6: "laser", 7: "music_follow",
               8: "screen_color", 9: "music_follow_2"}
KNOWN_IDS = {1833: "MKM071 (g29_yj01z_3104)", 1847: "GM025 (g51_gm025_3311)",
             1975: "GM024", 1829: "GM023", 1771: "GW24"}


# ---------------------------------------------------------------- framing
def checksum7(frame):
    """byte7 = 0xFF - (sum(byte0..byte6) & 0xFF)"""
    return (0xFF - (sum(frame[:7]) & 0xFF)) & 0xFF


def build_frame(opcode, *args):
    """64-byte command: [opcode, args(<=6), chk, zeros...]"""
    if opcode not in READ_OPCODES:
        raise ValueError("refusing to build non-query opcode 0x%02X" % opcode)
    if len(args) > 6:
        raise ValueError("at most 6 argument bytes")
    f = bytearray(REPORT_LEN)
    f[0] = opcode
    for i, a in enumerate(args):
        f[1 + i] = a & 0xFF
    f[7] = checksum7(f)
    return bytes(f)


def strip_report_id(buf, rid=0):
    """Drop the leading report-ID byte (hidapi and numbered reports include it)."""
    buf = bytes(buf)
    if len(buf) == REPORT_LEN + 1 and buf[0] == rid:
        return buf[1:]
    return buf


# ---------------------------------------------------------------- HID report descriptor
def parse_report_descriptor(d):
    """Minimal HID report-descriptor walker. Returns top-level application
    collections with their input/output/feature reports (bits per report ID)."""
    d = bytes(d)
    i = 0
    g = {"page": 0, "size": 0, "count": 0, "rid": 0}
    stack, depth, apps, cur = [], 0, [], None
    usage = None
    while i < len(d):
        b = d[i]
        if b == 0xFE:  # long item, skip
            i += 3 + d[i + 1]
            continue
        size = b & 3
        size = 4 if size == 3 else size
        typ, tag = (b >> 2) & 3, b >> 4
        val = int.from_bytes(d[i + 1:i + 1 + size], "little") if size else 0
        i += 1 + size
        if typ == 1:  # global
            if tag == 0:
                g["page"] = val
            elif tag == 7:
                g["size"] = val
            elif tag == 8:
                g["rid"] = val
            elif tag == 9:
                g["count"] = val
            elif tag == 10:
                stack.append(dict(g))
            elif tag == 11 and stack:
                g = stack.pop()
        elif typ == 2:  # local
            if tag == 0 and usage is None:
                usage = val
        elif typ == 0:  # main
            if tag == 10:  # collection
                if depth == 0:
                    cur = {"usage_page": g["page"], "usage": usage, "reports": {}}
                    apps.append(cur)
                depth += 1
            elif tag == 12:
                depth -= 1
            elif tag in (8, 9, 11) and cur is not None:
                kind = {8: "input", 9: "output", 11: "feature"}[tag]
                key = "%s id=%d" % (kind, g["rid"])
                cur["reports"][key] = cur["reports"].get(key, 0) + g["size"] * g["count"]
            usage = None
    return apps


def sysfs_hid():
    """Descriptors for every HID interface of VID 3151, from sysfs (no device access)."""
    out = []
    for p in sorted(glob.glob("/sys/bus/hid/devices/0003:%04X:*" % VID)):
        try:
            data = open(os.path.join(p, "report_descriptor"), "rb").read()
        except OSError:
            continue
        real = os.path.realpath(p)
        try:
            iface = int(open(os.path.join(os.path.dirname(real),
                                          "bInterfaceNumber")).read().strip(), 16)
        except (OSError, ValueError):
            iface = None
        out.append({"name": os.path.basename(p), "iface": iface,
                    "bytes": len(data), "raw_hex": data.hex(),
                    "collections": parse_report_descriptor(data)})
    return out


# ---------------------------------------------------------------- parsers
def rgb(b, o):
    return "#%02X%02X%02X" % (b[o], b[o + 1], b[o + 2])


def parse_light(rec):
    return {"type": LIGHT_TYPES.get(rec[0], "unknown(%d)" % rec[0]),
            "speed": 4 - rec[1], "value": rec[2], "flags": rec[3],
            "rgb": rgb(rec, 4)}


def parse_opt0(b):
    flags = b[12] | (b[13] << 8)
    return {
        "profile": b[8],
        "report_rate_hz": RATE_CODES.get(b[9], "unknown(0x%02X)" % b[9]),
        "debounce": b[10],
        "flags": {"light_off": bool(flags & 1), "wheel_light_off": bool(flags & 2),
                  "smooth": bool(flags & 4), "led_select": bool(flags & 8),
                  "power_save": bool(flags & 16)},
        "button_change": b[14], "wheel_to_button": b[15], "button_to_wheel": b[16],
        "light": parse_light(b[24:32]),
        "sleep_s": {"bt": b[40] | b[41] << 8, "bt_deep": b[42] | b[43] << 8,
                    "dongle_2g4": b[44] | b[45] << 8,
                    "dongle_2g4_deep": b[46] | b[47] << 8},
        "x_sensitivity": b[50], "y_sensitivity": b[51],
        "lift_cutoff": b[52], "angle_snap": b[53],
        "battery_colors": [rgb(b, 54), rgb(b, 57)],
        "charging_switch": bool(b[60]),
    }


def parse_opt1(b):
    xs = [b[8 + 2 * i] | b[9 + 2 * i] << 8 for i in range(8)]
    ys = [b[24 + 2 * i] | b[25 + 2 * i] << 8 for i in range(8)]
    return {"current_stage": b[2], "stage_count": b[3],
            "current_dpi": xs[b[2]] if b[2] < 8 else None,
            "dpi_x": xs, "dpi_y": ys,
            "stage_colors": [rgb(b, 40 + 3 * i) for i in range(8)]}


def describe_slot(s):
    s = tuple(s)
    if s == (0, 0, 0, 0):
        return "disabled"
    if s[0] == 0:
        return "keyboard mods=0x%02X key=0x%02X key2=0x%02X" % (s[1], s[2], s[3])
    if s[0] == 1:
        names = {0xF0: "left", 0xF1: "right", 0xF2: "middle", 0xF3: "back",
                 0xF4: "forward", 0xF5: "wheel_left/scroll", 0xF6: "wheel_right",
                 0xF7: "wheel_fwd", 0xF8: "wheel_back"}
        return "mouse " + names.get(s[2], "0x%02X" % s[2]) + (
            " arg=%d" % s[3] if s[3] else "")
    if s[0] == 20:
        return {0: "dpi_cycle", 1: "dpi_up", 2: "dpi_down", 4: "dpi_shift"}.get(
            s[2], "dpi_function %s" % (list(s),))
    if s[0] == 8:  # profile functions, from GearMaster's function table
        return {(0, 0): "profile_value", (0, 1): "profile_up", (0, 2): "profile_down",
                (0, 3): "profile_cycle"}.get((s[1], s[2]),
               "profile_exchange %d" % s[3] if s[2] == 4 else "profile fn %s" % (list(s),))
    if s[0] == 9:
        return "macro #%d mode=%d" % (s[2], s[1])
    if s[0] == 3:
        return "consumer usage=0x%04X" % (s[2] | s[3] << 8)
    if s[0] == 2:
        return "system power key 0x%02X" % s[1]
    if s[0] == 10:
        return "fn key (%s)" % (list(s),)
    if s[0] == 11:
        return "fire key"
    if s[0] == 18:
        return "open website/siri"
    return "function/unknown %s" % (list(s),)


def parse_matrix(b):
    return [{"slot": i, "raw": list(b[4 * i:4 * i + 4]),
             "meaning": describe_slot(b[4 * i:4 * i + 4])} for i in range(16)]


# ---------------------------------------------------------------- transports
class UsbLink:
    """HID feature reports over libusb control transfers (no hidraw needed).
    SET_REPORT = 0x09, GET_REPORT = 0x01, report type 3 (feature)."""

    def __init__(self, pid=None, iface=2):
        import usb.core
        import usb.util
        self.usb = usb
        devs = [d for d in usb.core.find(find_all=True, idVendor=VID)
                if pid is None or d.idProduct == pid]
        if not devs:
            raise IOError("no USB device 3151:%s found" %
                          ("%04x" % pid if pid else "????"))
        self.dev, self.iface, self.detached = devs[0], iface, False
        if self.dev.is_kernel_driver_active(iface):
            self.dev.detach_kernel_driver(iface)
            self.detached = True
        usb.util.claim_interface(self.dev, iface)

    def send_feature_report(self, data):  # data[0] = report ID, as in hidapi
        rid = data[0]
        payload = bytes(data) if rid else bytes(data[1:])
        self.dev.ctrl_transfer(0x21, 0x09, (3 << 8) | rid, self.iface,
                               payload, timeout=1000)

    def get_feature_report(self, rid, n):  # n includes the report-ID byte
        length = n if rid else n - 1
        got = bytes(self.dev.ctrl_transfer(0xA1, 0x01, (3 << 8) | rid,
                                           self.iface, length, timeout=1000))
        return got if rid else b"\x00" + got

    def close(self):
        try:
            self.usb.util.release_interface(self.dev, self.iface)
        finally:
            if self.detached:
                try:
                    self.dev.attach_kernel_driver(self.iface)
                except Exception:
                    pass


def all_interfaces():
    import hid
    return list(hid.enumerate(VID, 0))


def find_paths():
    ifs = all_interfaces()
    out = [d for d in ifs
           if d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE]
    if not out:
        out = [d for d in ifs if d.get("interface_number") == 2]
    return out


WAIT = 0.01  # seconds between SET_REPORT and GET_REPORT (--wait to change)


def query(link, opcode, *args, rid=0, wait=None, echo=True, allow_zero=False):
    """echo=False for replies that carry raw payload from byte 0 (button matrix)."""
    wait = WAIT if wait is None else wait
    link.send_feature_report(bytes([rid]) + build_frame(opcode, *args))
    time.sleep(wait)
    raw = link.get_feature_report(rid, REPORT_LEN + 1)
    reply = strip_report_id(raw, rid)
    if not any(reply) and not allow_zero:  # dongle mode answers with all zeros: that is "no data", not a value
        raise IOError("all-zero reply for 0x%02X (no data; dongle or sleeping mouse?)" % opcode)
    if echo and (not reply or reply[0] != opcode):
        raise IOError("unexpected reply for 0x%02X: %s" %
                      (opcode, reply[:16].hex()))
    return reply


def read_macro(link, idx, rid=0, wait=None):
    """Raw 256-byte macro buffer (4 pages of 64 bytes). All-zero pages are valid (empty macro)."""
    return b"".join(bytes(query(link, OP_GET_MACRO, idx, page, rid=rid, wait=wait,
                                echo=False, allow_zero=True)[:64]) for page in range(MACRO_PAGES))


def parse_macro(buf):
    """Port of GearMaster's buffToMacroEvents. Returns {repeat, events:[...], used_bytes}."""
    buf = bytes(buf)
    repeat = buf[0] | buf[1] << 8
    pos, n, events = 2, buf[2:6], []
    pos += 4
    while pos <= len(buf):
        if len(n) < 4:
            break
        if n[0] == 0xF9:
            events.append({"type": "mouse_move", "dx": n[2] - 256 if n[2] > 127 else n[2],
                           "dy": n[3] - 256 if n[3] > 127 else n[3]})
            if n[1]:
                delay = n[1] >> 1
            else:
                delay = buf[pos] | buf[pos + 1] << 8
                pos += 2
            events.append({"type": "delay", "value": delay})
        else:
            if 4 <= n[0] <= 239:
                events.append({"type": "key", "action": "down" if n[1] & 0x80 else "up",
                               "usage": n[0]})
            else:
                events.append({"type": "mouse_button", "action": "down" if n[1] & 0x80 else "up",
                               "code": n[0]})
            if n[1] & 0x7F:
                delay = n[1] & 0x7F
                pos -= 2
            else:
                delay = n[2] | n[3] << 8
            events.append({"type": "delay", "value": delay})
        n = buf[pos:pos + 4]
        if n == bytes(4) or len(n) < 4:
            break
        pos += 4
    return {"repeat": repeat, "events": [e for e in events if not (e["type"] == "delay" and e["value"] == 0)],
            "used_bytes": pos}


def pick_feature_interface():
    """From sysfs descriptors: the interface carrying a 64-byte feature report."""
    for h in sysfs_hid():
        for c in h["collections"]:
            for key, bits in c["reports"].items():
                if key.startswith("feature") and bits == REPORT_LEN * 8:
                    return h["iface"], int(key.split("=")[1])
    return None, None


def dump(args):
    iface, rid = args.iface, args.rid
    if args.usb:
        if iface is None:
            iface, auto_rid = pick_feature_interface()
            iface = 2 if iface is None else iface
            rid = auto_rid if (rid is None and auto_rid is not None) else rid
        rid = 0 if rid is None else rid
        link = UsbLink(pid=args.pid, iface=iface)
        label = {"transport": "libusb", "iface": iface, "report_id": rid}
    else:
        import hid
        cands = find_paths()
        if not cands:
            sys.exit("no vendor interface found via hidapi; try --desc, then --usb")
        link = hid.device()
        link.open_path(args.path.encode() if args.path else cands[0]["path"])
        rid = 0 if rid is None else rid
        label = {"transport": "hidapi", "path": cands[0]["path"].decode()}
    info = dict(label)
    errors = {}

    def attempt(name, fn):
        try:
            info[name] = fn()
        except Exception as e:  # keep going, report everything we could read
            errors[name] = "%s: %s" % (type(e).__name__, e)

    try:
        # On the wired MKM071, GET_REV (0x8F) returns the device-table ID (1833 = MKM071),
        # not a firmware version.
        rev = query(link, OP_GET_REV, rid=rid)
        info["model_id"] = rev[1] | rev[2] << 8
        info["model_guess"] = KNOWN_IDS.get(info["model_id"], "unknown")
        info["rev_reply_hex"] = rev[:8].hex()
    except Exception as e:
        errors["model_id"] = "%s: %s" % (type(e).__name__, e)
    try:
        if args.f1:  # 0xF1 only echoes the request on the application firmware
            attempt("f1_reply_hex", lambda: query(link, OP_GET_ID, rid=rid)[:8].hex())
        attempt("profile", lambda: query(link, OP_GET_PROFILE, rid=rid)[1])
        attempt("opt0", lambda: parse_opt0(query(link, OP_GET_OPT0, rid=rid)))
        attempt("opt1", lambda: parse_opt1(
            query(link, OP_GET_OPT1, info.get("profile", 0), rid=rid)))
        attempt("matrix", lambda: parse_matrix(
            query(link, OP_GET_MATRIX, info.get("profile", 0), rid=rid, echo=False)))
        if args.profiles:
            profs = {}
            for p in range(args.profile_count):
                try:
                    m = bytes(query(link, OP_GET_MATRIX, p, rid=rid, echo=False)[:64])
                    o1 = bytes(query(link, OP_GET_OPT1, p, rid=rid)[:64])
                    rr = bytes(query(link, OP_GET_REPORT, p, rid=rid)[:8])
                    profs[p] = {"matrix_hex": m.hex(), "opt1_hex": o1.hex(),
                                "rate_reply_hex": rr.hex(),
                                "dpi_x": parse_opt1(o1)["dpi_x"],
                                "current_stage": o1[2],
                                "stage_colors": parse_opt1(o1)["stage_colors"][:6],
                                "buttons": [d["meaning"] for d in parse_matrix(m)]}
                except Exception as e:
                    profs[p] = {"error": "%s: %s" % (type(e).__name__, e)}
            same = {p: [q for q in profs if q != p and "error" not in profs[q] and
                        "error" not in profs[p] and
                        profs[q]["matrix_hex"] == profs[p]["matrix_hex"] and
                        profs[q]["opt1_hex"] == profs[p]["opt1_hex"]] for p in profs}
            info["profiles"] = profs
            info["identical_to"] = same
        if args.macros:
            macros = {}
            for m in range(args.macros):
                try:
                    raw = read_macro(link, m, rid=rid)
                    macros[m] = ({"empty": True} if not any(raw) else
                                 {"erased_flash": True} if raw == b"\xff" * len(raw) else
                                 {"hex": raw.rstrip(b"\0").hex(), **parse_macro(raw)})
                except Exception as e:
                    macros[m] = {"error": "%s: %s" % (type(e).__name__, e)}
                    break
            info["macros"] = macros
        if args.fn:
            attempt("fn_matrix", lambda: parse_matrix(
                query(link, OP_GET_FNMATRIX, info.get("profile", 0), rid=rid, echo=False)))
        if args.battery:
            # Raw reply only; layout unknown. The mouse may echo, return zeros, or stall.
            attempt("battery_raw_hex", lambda: query(
                link, OP_GET_BATTERY, rid=rid, echo=False)[:16].hex())
    finally:
        link.close()
    if errors:
        info["errors"] = errors
    print(json.dumps(info, indent=2))


# ---------------------------------------------------------------- self-test
IF1_DESC = bytes.fromhex(
    "050c0901a1018503" "1900" "2a3c03" "150026" "3c03" "950175108100c0"
    "050109" "80a1018502" "050119" "8129831500250195037501" "81029501750581" "01c0"
    "0501" "0906a1018507" "050719" "e029e715002501750195088102"
    "1900" "2aff00" "150026ff00" "750895068100c0"
    "06ffff0901a1018505" "0901" "150026ff00" "7508950381" "02c0")
IF0_DESC = bytes.fromhex(
    "05010902a1010901a10005091500250119012905750195058102950381010501"
    "16018026ff7f093009317510950281061581257f09387508950181 06"
    "050c0a3802950181 06c0c0".replace(" ", ""))


def selftest():
    # checksum property: bytes 0..7 sum to 0xFF (mod 256)
    for op in READ_OPCODES:
        f = build_frame(op, 1, 2, 3)
        assert len(f) == 64 and (sum(f[:8]) & 0xFF) == 0xFF, hex(op)
    assert build_frame(OP_GET_ID)[7] == 0x0E  # 0xFF - 0xF1
    pm = parse_macro(bytes([1, 0, 4, 0x8A, 4, 0x0A, 0, 0, 0, 0]) + bytes(246))
    assert pm["repeat"] == 1 and [e.get("action") or e.get("value") for e in pm["events"]] == \
        ["down", 10, "up", 10], pm
    pm = parse_macro(bytes([2, 0, 5, 0x80, 0x2C, 1, 5, 0, 0, 0, 0, 0]) + bytes(244))  # 'b' down, 300 ms; 'b' up, 0
    assert pm["repeat"] == 2 and pm["events"][1] == {"type": "delay", "value": 300}, pm
    for bad in (0x01, 0x53, 0x7F, 0xBA):
        try:
            build_frame(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("allowed 0x%02X" % bad)
    assert strip_report_id(b"\x00" + bytes(64)) == bytes(64)
    assert strip_report_id(b"\x05" + bytes(64), 5) == bytes(64)

    # descriptor parser against the real dumps from the 2.4G dongle (3151:4027)
    c = {(a["usage_page"], a["usage"]): a["reports"]
         for a in parse_report_descriptor(IF1_DESC)}
    assert c[(0x0C, 1)] == {"input id=3": 16}, c
    assert c[(0x01, 0x80)] == {"input id=2": 8}, c
    assert c[(0x01, 6)] == {"input id=7": 56}, c
    assert c[(0xFFFF, 1)] == {"input id=5": 24}, c      # vendor event report
    m = parse_report_descriptor(IF0_DESC)
    assert len(m) == 1 and (m[0]["usage_page"], m[0]["usage"]) == (1, 2)
    assert m[0]["reports"] == {"input id=0": 56}, m

    # option block 0 parse against a synthetic buffer
    b = bytearray(64)
    b[8] = 2; b[9] = 0x01; b[10] = 10
    b[12] = 0b00010001; b[14] = 1; b[15] = 10; b[16] = 10
    b[24:32] = bytes([2, 1, 3, 8, 255, 0, 0, 0])
    b[40:48] = (1800).to_bytes(2, "little") + (3600).to_bytes(2, "little") * 3
    b[50] = b[51] = 100
    o = parse_opt0(bytes(b))
    assert o["profile"] == 2 and o["report_rate_hz"] == 1000
    assert o["flags"]["light_off"] and o["flags"]["power_save"]
    assert o["light"]["type"] == "breathe" and o["light"]["speed"] == 3
    assert o["sleep_s"]["bt"] == 1800
    # option block 1 parse
    b = bytearray(64)
    b[2] = 1; b[3] = 6
    for i, v in enumerate([800, 1600, 2400, 3200, 4000, 4800, 0, 0]):
        b[8 + 2 * i:10 + 2 * i] = v.to_bytes(2, "little")
        b[24 + 2 * i:26 + 2 * i] = v.to_bytes(2, "little")
    b[40:43] = bytes([255, 0, 0])
    o = parse_opt1(bytes(b))
    assert o["dpi_x"][:6] == [800, 1600, 2400, 3200, 4000, 4800]
    assert o["stage_colors"][0] == "#FF0000"
    default = [1, 0, 240, 0, 1, 0, 241, 0, 1, 0, 242, 0, 1, 0, 244, 0,
               1, 0, 243, 0, 10, 3, 0, 0, 20, 0, 0, 0] + [0] * 36
    m = parse_matrix(bytes(default))
    assert m[0]["meaning"].startswith("mouse left")
    assert m[6]["meaning"] == "dpi_cycle"
    # first 16 bytes of the real MKM071 matrix (wired, 2026-10-14)
    real = bytes.fromhex("0100f000" "08000300" "0100f200" "14000000") + bytes(48)
    m = parse_matrix(real)
    assert m[0]["meaning"].startswith("mouse left")
    assert m[2]["meaning"].startswith("mouse middle")
    assert m[3]["meaning"] == "dpi_cycle" and m[1]["raw"] == [8, 0, 3, 0]
    assert m[1]["meaning"] == "profile_cycle"
    assert describe_slot([20, 0, 1, 0]) == "dpi_up"
    assert OP_GET_BATTERY in READ_OPCODES and build_frame(OP_GET_FNMATRIX)[0] == 0xD1
    # matrix replies have no echoed opcode: echo=False must not raise
    class Fake:
        def send_feature_report(self, d): pass
        def get_feature_report(self, rid, n): return b"\x00" + real
    assert query(Fake(), OP_GET_MATRIX, 0, echo=False, wait=0)[:4] == real[:4]
    try:
        query(Fake(), OP_GET_MATRIX, 0, wait=0)
    except IOError:
        pass
    else:
        raise AssertionError("echo check should fail")
    print("selftest ok")


def auto_int(s):
    return int(s, 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--desc", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--usb", action="store_true")
    ap.add_argument("--pid", type=auto_int)
    ap.add_argument("--iface", type=int)
    ap.add_argument("--rid", type=int)
    ap.add_argument("--path")
    ap.add_argument("--f1", action="store_true", help="also send the 0xF1 query")
    ap.add_argument("--profiles", action="store_true", help="read matrix, DPI block and rate for each profile index")
    ap.add_argument("--profile-count", type=int, default=5)
    ap.add_argument("--macros", type=int, nargs="?", const=20, default=0,
                    help="read macro slots 0..N-1 (default 20) with opcode 0x96")
    ap.add_argument("--fn", action="store_true", help="also read the FN-layer matrix (0xD1)")
    ap.add_argument("--battery", action="store_true", help="try the 0x82 battery query")
    ap.add_argument("--wait", type=float, default=0.01,
                    help="seconds to wait before reading the reply (try 0.2 on 2.4G)")
    a = ap.parse_args()
    WAIT = a.wait
    if a.selftest:
        selftest()
    elif a.desc:
        for h in sysfs_hid():
            print("%s  iface=%s  %d-byte descriptor" % (h["name"], h["iface"], h["bytes"]))
            for c in h["collections"]:
                print("   usage_page=0x%04X usage=%s  %s" % (
                    c["usage_page"], c["usage"], c["reports"] or "(no reports)"))
            print("   raw: " + h["raw_hex"])
    elif a.list:
        ifs = all_interfaces()
        if not ifs:
            print("hidapi sees no VID 3151 devices (wrong `hid` module? you need `pip install hidapi`)")
        for d in ifs:
            print("if=%s usage_page=0x%04X usage=0x%02X %04x:%04x %s %s" % (
                d.get("interface_number"), d["usage_page"], d["usage"],
                d["vendor_id"], d["product_id"], d.get("product_string"), d["path"]))
    elif a.dump:
        dump(a)
    else:
        ap.print_help()
