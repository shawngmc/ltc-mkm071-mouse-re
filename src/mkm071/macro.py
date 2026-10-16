"""Macro buffers: encode/decode (ports of GearMaster's setMacro / buffToMacroEvents), spec parsing,
and the paged 0x16 write frames (docs/protocol.md section 5.6).

Events are dicts: {"type": "key", "action": "down"|"up", "usage": HID usage},
{"type": "mouse_button", "action": ..., "code": 0xF0..0xF4}, {"type": "mouse_move", "dx", "dy"},
{"type": "delay", "value": ms}.
"""
from . import keys
from .frame import OP_SET_MACRO, checksum7

MACRO_COUNT = 20         # MACROMAX in GearMaster
MACRO_SIZE = 256
MACRO_PAGE = 56
MACRO_PAGES_WRITTEN = 4  # 4 x 56 = 224 bytes; a fifth page has never been tried on hardware
# Content limit: content plus the 4-byte zero terminator must fit in the 224 written bytes, because
# bytes 224..255 are never written and read back as stale junk. (GearMaster allows 248.)
MACRO_LIMIT = MACRO_PAGE * MACRO_PAGES_WRITTEN - 4
MACRO_BUTTONS = {"left": 0xF0, "right": 0xF1, "middle": 0xF2, "back": 0xF3, "forward": 0xF4}
MACRO_MODES = {"repeat": 0, "toggle": 1, "hold": 2}  # slot action [9, mode, index, 0]


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


def hid_usage(name):
    """HID keyboard usage for a key name (see mkm071.keys: a-z, 0-9, f1-f24, enter, space, ctrl, ...)."""
    return keys.usage(name)


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
            r = rest.lower()
            if r not in MACRO_BUTTONS and not r.startswith("0x"):
                raise ValueError("mouse button must be one of %s" % "|".join(MACRO_BUTTONS))
            code = MACRO_BUTTONS[r] if r in MACRO_BUTTONS else int(r, 16)
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


def format_macro_events(events):
    """Events as a spec string that parse_macro_spec reads back (delays are explicit)."""
    names = {v: k for k, v in MACRO_BUTTONS.items()}
    out = []
    for e in events:
        if e["type"] == "key":
            out.append("%s:%s" % (e["action"], keys.name(e["usage"])))
        elif e["type"] == "mouse_button":
            out.append("m%s:%s" % (e["action"], names.get(e["code"], "0x%02x" % e["code"])))
        elif e["type"] == "mouse_move":
            out.append("move:%d:%d" % (e["dx"], e["dy"]))
        else:
            out.append("delay:%d" % e["value"])
    return ",".join(out)


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
    if pending_delay:
        # The last event owes its u16 delay; the decoder reads the event in its 4-byte form, so the
        # two zero bytes count as content (they are zero already, this only checks the limit).
        put(0, 0)
    return bytes(o)


def macro_frames(idx, buf):
    """Frames GearMaster sends (`_setMacro`): header + 56-byte pages up to the last used page.

    One difference from GearMaster: the page holding the 4-byte zero terminator is always sent.
    The mouse erases the slot to 0xFF before writing, so when the content ends exactly on a page
    boundary GearMaster's frames would leave the terminator reading FF FF FF FF."""
    if not 0 <= idx < MACRO_COUNT:
        raise ValueError("macro index must be 0..%d" % (MACRO_COUNT - 1))
    buf = bytes(buf).ljust(MACRO_PAGE * 5, b"\0")
    pages = [buf[i * MACRO_PAGE:(i + 1) * MACRO_PAGE] for i in range(5)]
    used = 1
    for i, pg in enumerate(pages):
        if any(pg):
            used = i + 1
    if any(buf[2:6]):
        terminator_end = parse_macro(buf[:MACRO_SIZE])["used_bytes"] + 4
        used = max(used, min(5, (terminator_end - 1) // MACRO_PAGE + 1))
    last = pages[used - 1]
    tail = max([k + 1 for k, b in enumerate(last) if b] or [0])
    total = MACRO_PAGE * (used - 1) + tail
    frames = []
    for f in range(used):
        fr = bytearray(64)
        fr[0], fr[1], fr[2], fr[3] = OP_SET_MACRO, idx, f, total
        fr[4] = 1 if f == used - 1 else 0
        fr[8:64] = pages[f]
        fr[7] = checksum7(fr)
        frames.append(bytes(fr))
    return frames


def macro_norm(raw):
    """The meaningful part of a macro buffer. The mouse erases a slot to 0xFF before writing, so
    bytes after the terminator read back as 0xFF; they are not part of the macro."""
    raw = bytes(raw)
    if not any(raw[2:6]) or raw == b"\xff" * len(raw):
        return b""
    return raw[:parse_macro(raw)["used_bytes"]]


def macro_is_empty(raw):
    return macro_norm(raw) == b""
