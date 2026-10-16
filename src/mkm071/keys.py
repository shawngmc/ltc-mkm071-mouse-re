"""HID keyboard usages (page 0x07) by name, and the modifier byte of a key slot.

The first name listed for a usage is the one used when printing.
"""
_NAMES = [
    (0x28, "enter", "return"), (0x29, "esc", "escape"), (0x2A, "backspace", "bksp"), (0x2B, "tab"),
    (0x2C, "space"), (0x2D, "minus", "-"), (0x2E, "equal", "="), (0x2F, "leftbracket", "["),
    (0x30, "rightbracket", "]"), (0x31, "backslash", "\\"), (0x32, "nonus_hash"), (0x33, "semicolon", ";"),
    (0x34, "apostrophe", "'"), (0x35, "grave", "`"), (0x36, "comma", ","), (0x37, "dot", "."),
    (0x38, "slash", "/"), (0x39, "capslock", "caps"), (0x46, "printscreen", "prtsc"),
    (0x47, "scrolllock"), (0x48, "pause"), (0x49, "insert", "ins"), (0x4A, "home"),
    (0x4B, "pageup", "pgup"), (0x4C, "delete", "del"), (0x4D, "end"), (0x4E, "pagedown", "pgdn"),
    (0x4F, "right"), (0x50, "left"), (0x51, "down"), (0x52, "up"), (0x53, "numlock"),
    (0x54, "kp_slash"), (0x55, "kp_asterisk"), (0x56, "kp_minus"), (0x57, "kp_plus"),
    (0x58, "kp_enter"), (0x63, "kp_dot"), (0x64, "nonus_backslash"), (0x65, "menu", "application"),
    (0xE0, "ctrl", "lctrl"), (0xE1, "shift", "lshift"), (0xE2, "alt", "lalt"),
    (0xE3, "win", "lwin", "gui", "super", "meta"), (0xE4, "rctrl"), (0xE5, "rshift"), (0xE6, "ralt", "altgr"),
    (0xE7, "rwin"),
]
_NAMES += [(0x04 + i, chr(ord("a") + i)) for i in range(26)]
_NAMES += [(0x1E + i, str(i + 1)) for i in range(9)] + [(0x27, "0")]
_NAMES += [(0x3A + i, "f%d" % (i + 1)) for i in range(12)] + [(0x68 + i, "f%d" % (i + 13)) for i in range(12)]
_NAMES += [(0x59 + i, "kp%d" % (i + 1)) for i in range(9)] + [(0x62, "kp0")]

USAGES = {}
NAMES = {}
for usage, *names in _NAMES:
    NAMES.setdefault(usage, names[0])
    for n in names:
        USAGES[n] = usage

MODIFIER_USAGES = range(0xE0, 0xE8)
# Slot modifier byte, assumed to be the standard HID modifier bitmask (bit n = usage 0xE0 + n).
# Not verified on hardware: protocol.md only says "modifier bit-code (alt E2, ctrl E0, shift E1, win E3)".
MODIFIER_BITS = {u: 1 << (u - 0xE0) for u in MODIFIER_USAGES}


def usage(name):
    """HID usage for a key name ('a', 'f5', 'enter', 'ctrl', ...) or a number ('0x2d')."""
    n = name.strip().lower()
    if n in USAGES:
        return USAGES[n]
    if n.startswith("0x"):
        try:
            v = int(n, 16)
        except ValueError:
            v = -1
        if 0 < v <= 0xFF:
            return v
    raise ValueError("unknown key %r" % name)


def name(u):
    return NAMES.get(u, "0x%02x" % u)


def parse_combo(spec):
    """'ctrl+shift+a' -> (modifier byte, usage, usage2). At most two non-modifier keys; a lone
    modifier ('shift') becomes the key itself."""
    parts = [usage(p) for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key")
    keys = [u for u in parts if u not in MODIFIER_USAGES]
    mods = [u for u in parts if u in MODIFIER_USAGES]
    if not keys:
        keys, mods = [mods[-1]], mods[:-1]
    if len(keys) > 2:
        raise ValueError("a slot holds at most two keys plus modifiers: %r" % spec)
    mod_byte = 0
    for m in mods:
        mod_byte |= MODIFIER_BITS[m]
    return mod_byte, keys[0], keys[1] if len(keys) > 1 else 0


def format_combo(mods, key, key2=0):
    parts = [name(0xE0 + i) for i in range(8) if mods & (1 << i)] + [name(key)]
    if key2:
        parts.append(name(key2))
    return "+".join(parts)
