import pytest

from fakedevice import FACTORY_MATRIX
from mkm071 import ButtonAction, Matrix, keys
from mkm071.actions import format_action, parse_action, parse_slot, slot_label
from mkm071.macro import format_macro_events, hid_usage, parse_macro_spec


# ------------------------------------------------------------ keys
def test_key_table():
    assert keys.usage("a") == 0x04 and keys.usage("Z") == 0x1D and keys.usage("1") == 0x1E
    assert keys.usage("0") == 0x27 and keys.usage("f12") == 0x45 and keys.usage("f13") == 0x68
    assert keys.usage("return") == keys.usage("enter") == 0x28 and keys.usage("-") == 0x2D
    assert keys.usage("win") == keys.usage("super") == 0xE3 and keys.usage("0x2d") == 0x2D
    assert keys.name(0x2D) == "minus" and keys.name(0xE3) == "win" and keys.name(0xA5) == "0xa5"
    for bad in ("€", "0x100", "0x", "ctrlx"):
        with pytest.raises(ValueError):
            keys.usage(bad)


def test_macro_key_names_unchanged():
    # the names the old writer knew keep their usages
    old = {"a": 4, "z": 29, "1": 30, "9": 38, "0": 39, "enter": 40, "esc": 41, "backspace": 42,
           "tab": 43, "space": 44, "shift": 225, "ctrl": 224, "alt": 226, "f1": 58, "f12": 69}
    assert {k: hid_usage(k) for k in old} == old


def test_combos():
    assert keys.parse_combo("c") == (0, 0x06, 0)
    assert keys.parse_combo("ctrl+c") == (0x01, 0x06, 0)
    assert keys.parse_combo("ctrl+shift+esc") == (0x03, 0x29, 0)
    assert keys.parse_combo("rwin+a+b") == (0x80, 0x04, 0x05)
    assert keys.parse_combo("shift") == (0, 0xE1, 0)
    assert keys.format_combo(0x05, 0x06) == "ctrl+alt+c"
    with pytest.raises(ValueError):
        keys.parse_combo("a+b+c")


# ------------------------------------------------------------ actions
@pytest.mark.parametrize("text,raw,warns", [
    ("left", "0100f000", 0), ("forward", "0100f400", 0), ("scroll:up", "0100f901", 1),
    ("scroll:down", "0100f9ff", 1), ("key:5", "00002200", 0), ("key:ctrl+c", "00010600", 1),
    ("key:a+b", "00000405", 1), ("macro:3", "09000300", 0), ("macro:3:toggle", "09010300", 0),
    ("macro:19:hold", "09021300", 0), ("dpi:cycle", "14000000", 0), ("dpi:shift", "14000400", 1),
    ("profile:cycle", "08000300", 1), ("profile:next", "08000100", 1), ("profile:prev", "08000200", 0),
    ("media:volup", "0300e900", 1), ("consumer:0x0123", "03002301", 1), ("media:calc", "03009201", 1), ("disabled", "00000000", 1),
    ("raw:0a,03,00,00", "0a030000", 0),
])
def test_parse_and_format(text, raw, warns):
    action, w = parse_action(text)
    assert bytes(action).hex() == raw and len(w) == warns
    assert format_action(action) == text


def test_aliases():
    assert parse_action("profile:up")[0] == parse_action("profile:next")[0]
    assert parse_action("profile:previous")[0] == parse_action("profile:prev")[0]
    assert parse_action("none")[0] == ButtonAction.disabled()
    assert parse_action("  KEY:Ctrl+C ")[0] == parse_action("key:ctrl+c")[0]


@pytest.mark.parametrize("text", ["tilt", "macro:20", "macro:x", "macro:1:loop", "dpi:max", "profile:4",
                                  "key:", "key:a+b+c", "raw:01,02", "raw:zz,00,00,00", "scroll:left",
                                  "media:eject", "consumer:0x10000", "left:x"])
def test_parse_errors(text):
    with pytest.raises(ValueError):
        parse_action(text)


def test_every_factory_slot_formats_and_round_trips():
    for s in Matrix.from_bytes(FACTORY_MATRIX).slots:
        assert parse_action(format_action(s))[0] == s


def test_unknown_bytes_format_as_raw():
    for raw in (b"\x01\x00\xf5\x01", b"\x0b\x00\x00\x00", b"\x08\x00\x04\x04", b"\x09\x05\x00\x00"):
        assert format_action(ButtonAction(raw)).startswith("raw:")
        assert parse_action(format_action(ButtonAction(raw)))[0].raw == raw


def test_slots():
    assert parse_slot("thumb_1") == 7 and parse_slot("RIGHT") == 9 and parse_slot("0x0f") == 15
    assert slot_label(3) == "dpi_button (slot 3)"
    for bad in ("16", "-1", "thumb_9"):
        with pytest.raises(ValueError):
            parse_slot(bad)


def test_macro_spec_formatter_round_trips():
    spec = ("down:ctrl,delay:10,tap:c,up:ctrl,delay:200,move:5:-3,delay:300,mdown:back,delay:10,"
            "mup:back,down:minus,delay:1000,up:0x2d")
    ev = parse_macro_spec(spec)
    assert parse_macro_spec(format_macro_events(ev)) == ev
    assert format_macro_events(ev).startswith("down:ctrl,delay:10,down:c,delay:10,up:c")
