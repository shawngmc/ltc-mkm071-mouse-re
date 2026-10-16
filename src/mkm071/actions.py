"""Readable button actions and slot names.

    left | right | middle | back | forward       mouse buttons            [1, 0, F0..F4, 0]
    scroll:up | scroll:down                      wheel (MKM071 slots 12/13) [1, 0, F9, +1/-1]
    key:5 | key:ctrl+c | key:a+b                 keyboard                 [0, mods, key, key2]
    macro:3 | macro:3:toggle | macro:3:hold      macro slot 0-19          [9, mode, index, 0]
    dpi:cycle | dpi:up | dpi:down | dpi:shift    DPI functions            [20, 0, fn, 0]
    profile:cycle | profile:next | profile:prev  profile functions        [8, 0, fn, 0]
    media:playpause | consumer:0x00e9            consumer usage           [3, 0, lo, hi]
    disabled                                     no action                [0, 0, 0, 0]
    raw:01,00,f0,00                              any 4 bytes (hex)

`parse_action` returns the action and a list of warnings for encodings that were decoded from
GearMaster but never written on hardware, or that have side effects worth knowing.
"""
from . import keys
from .macro import MACRO_BUTTONS, MACRO_COUNT, MACRO_MODES
from .model import DPI_FUNCS, SLOT_NAMES, ButtonAction

SLOTS = {v: k for k, v in SLOT_NAMES.items()}
PROFILE_FNS = {"next": 1, "prev": 2, "cycle": 3}
PROFILE_ALIASES = {"up": "next", "previous": "prev", "down": "prev"}
MEDIA = {"playpause": 0xCD, "play": 0xCD, "next": 0xB5, "prev": 0xB6, "stop": 0xB7, "mute": 0xE2,
         "volup": 0xE9, "voldown": 0xEA, "calc": 0x192, "browser": 0x196, "mail": 0x18A}
MEDIA_NAMES = {0xCD: "playpause", 0xB5: "next", 0xB6: "prev", 0xB7: "stop", 0xE2: "mute",
               0xE9: "volup", 0xEA: "voldown", 0x192: "calc", 0x196: "browser", 0x18A: "mail"}

UNTESTED = "not yet written on hardware (decoded from GearMaster); check it after writing"


def parse_slot(text):
    """Slot number 0-15, or a name from SLOT_NAMES (thumb_1, dpi_button, ...)."""
    t = text.strip().lower()
    if t in SLOTS:
        return SLOTS[t]
    try:
        n = int(t, 0)
    except ValueError:
        raise ValueError("unknown slot %r; use 0-15 or one of %s" % (text, ", ".join(SLOTS))) from None
    if not 0 <= n <= 15:
        raise ValueError("slot must be 0-15")
    return n


def slot_label(n):
    return "%s (slot %d)" % (SLOT_NAMES.get(n, "slot_%d" % n), n)


def parse_action(text):
    """Text -> (ButtonAction, [warnings])."""
    t = text.strip()
    kind, _, rest = t.partition(":")
    kind = kind.lower()
    rest_l = rest.lower()
    warn = []
    if kind in MACRO_BUTTONS and not rest:
        return ButtonAction.mouse(kind), warn
    if kind in ("disabled", "none") and not rest:
        warn.append("disabled: " + UNTESTED)
        return ButtonAction.disabled(), warn
    if kind == "scroll":
        if rest_l not in ("up", "down"):
            raise ValueError("scroll:up or scroll:down")
        warn.append("scroll: read from the factory layout, " + UNTESTED.split(" (")[0])
        return ButtonAction(bytes([1, 0, 0xF9, 1 if rest_l == "up" else 0xFF])), warn
    if kind == "key":
        mods, k1, k2 = keys.parse_combo(rest)
        if mods:
            warn.append("modifier byte assumed to be the HID bitmask (0x%02X); "
                        "not verified on hardware" % mods)
        if k2:
            warn.append("second key: " + UNTESTED)
        return ButtonAction.key(k1, mods, k2), warn
    if kind == "macro":
        idx, _, mode = rest_l.partition(":")
        mode = mode or "repeat"
        if mode not in MACRO_MODES:
            raise ValueError("macro mode must be one of %s" % "|".join(MACRO_MODES))
        try:
            n = int(idx, 0)
        except ValueError:
            raise ValueError("macro:INDEX[:MODE], INDEX 0-%d" % (MACRO_COUNT - 1)) from None
        return ButtonAction.macro(n, mode), warn
    if kind == "dpi":
        if rest_l not in DPI_FUNCS:
            raise ValueError("dpi function must be one of %s" % "|".join(DPI_FUNCS))
        if rest_l != "cycle":
            warn.append("dpi:%s: %s" % (rest_l, UNTESTED))
        return ButtonAction.dpi(rest_l), warn
    if kind == "profile":
        fn = PROFILE_ALIASES.get(rest_l, rest_l)
        if fn not in PROFILE_FNS:
            raise ValueError("profile function must be one of %s" % "|".join(PROFILE_FNS))
        if fn == "next":
            warn.append("profile:next does not wrap: from profile 4 it moves to 5, outside the backed-up "
                        "range (get back with `mkm071 profile set 0`)")
        if fn == "cycle":
            warn.append("profile:cycle only visits profiles 0-2")
        return ButtonAction(bytes([8, 0, PROFILE_FNS[fn], 0])), warn
    if kind in ("media", "consumer"):
        if kind == "media":
            if rest_l not in MEDIA:
                raise ValueError("media key must be one of %s" % "|".join(sorted(MEDIA)))
            u = MEDIA[rest_l]
        else:
            u = int(rest_l, 16)
            if not 0 < u <= 0xFFFF:
                raise ValueError("consumer usage must be 0x0001-0xFFFF")
        warn.append("media/consumer: " + UNTESTED)
        return ButtonAction(bytes([3, 0, u & 0xFF, u >> 8])), warn
    if kind == "raw":
        try:
            b = bytes(int(x, 16) for x in rest.replace(" ", "").split(","))
        except ValueError:
            raise ValueError("raw:B0,B1,B2,B3 in hex") from None
        if len(b) != 4:
            raise ValueError("raw needs exactly 4 bytes")
        return ButtonAction(b), warn
    raise ValueError("unknown action %r (see `mkm071 button --help`)" % text)


def format_action(action):
    """ButtonAction -> the text form parse_action reads back."""
    b = bytes(action)
    t = b[0]
    if b == bytes(4):
        return "disabled"
    if t == 1 and b[3] == 0:
        names = {v: k for k, v in MACRO_BUTTONS.items()}
        if b[2] in names and b[1] == 0:
            return names[b[2]]
    if t == 1 and b[1] == 0 and b[2] == 0xF9 and b[3] in (1, 0xFF):
        return "scroll:up" if b[3] == 1 else "scroll:down"
    if t == 0:
        return "key:" + keys.format_combo(b[1], b[2], b[3])
    if t == 9 and b[3] == 0 and b[1] in MACRO_MODES.values():
        mode = {v: k for k, v in MACRO_MODES.items()}[b[1]]
        return "macro:%d" % b[2] + ("" if mode == "repeat" else ":" + mode)
    if t == 20 and b[1] == b[3] == 0 and b[2] in DPI_FUNCS.values():
        return "dpi:" + {v: k for k, v in DPI_FUNCS.items()}[b[2]]
    if t == 8 and b[1] == b[3] == 0 and b[2] in PROFILE_FNS.values():
        return "profile:" + {v: k for k, v in PROFILE_FNS.items()}[b[2]]
    if t == 3 and b[1] == 0:
        u = b[2] | b[3] << 8
        return "media:" + MEDIA_NAMES[u] if u in MEDIA_NAMES else "consumer:0x%04x" % u
    return "raw:" + ",".join("%02x" % x for x in b)
