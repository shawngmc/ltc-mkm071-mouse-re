"""Macro encode/decode against the byte strings in docs/protocol.md section 5.6."""
import pytest

from mkm071 import Macro
from mkm071.macro import (MACRO_LIMIT, build_macro_buffer, macro_frames, macro_norm, macro_text_events,
                          parse_macro, parse_macro_spec)

FOX = "the quick brown fox jumps over the lazy dog"


def kinds(events):
    return [(e["type"], e.get("action"), e.get("usage", e.get("code")), e.get("value")) for e in events]


def test_hi_golden():
    # repeat 1, h down 10, h up 10, i down 10, i up 10 (typed "hi" on hardware)
    b = build_macro_buffer(1, macro_text_events("hi", 10))
    assert b[:14].hex() == "01000b8a0b0a0c8a0c0a" "00000000"
    assert len(b) == 256 and not any(b[10:])
    assert kinds(parse_macro(b)["events"]) == [
        ("key", "down", 11, None), ("delay", None, None, 10),
        ("key", "up", 11, None), ("delay", None, None, 10),
        ("key", "down", 12, None), ("delay", None, None, 10),
        ("key", "up", 12, None), ("delay", None, None, 10)]


def test_short_and_long_delay_forms_golden():
    # delays <= 127 ride in the flag byte; longer ones take the 4-byte [usage][flags][u16 LE] form
    ev = parse_macro_spec("down:a,delay:100,up:a,delay:1000,down:b,delay:5000,up:b")
    b = build_macro_buffer(1, ev)
    assert b[:20].hex() == "0100" "04e4" "0400e803" "05808813" "0500" + "00" * 6
    d = parse_macro(b)
    assert [e["value"] for e in d["events"] if e["type"] == "delay"] == [100, 1000, 5000]
    # the final "05 00" (up:b, no delay) is read as the 4-byte form, so the terminator starts at 16
    assert d["used_bytes"] == 16


def test_boundary_delays():
    for ms in (1, 127, 128, 0xFFFF):
        d = parse_macro(build_macro_buffer(1, parse_macro_spec("down:a,delay:%d,up:a" % ms)))
        assert [e["value"] for e in d["events"] if e["type"] == "delay"] == [ms]


def test_repeat_count():
    ev = parse_macro_spec("tap:a,delay:300,tap:b", 200)
    d = parse_macro(build_macro_buffer(3, ev))
    assert d["repeat"] == 3
    assert [e["value"] for e in d["events"] if e["type"] == "delay"] == [200, 200, 300, 200, 200]


def test_mouse_events_golden():
    # the hardware-verified sequence; F9 moves carry dx, dy as signed bytes (dy > 0 moves the cursor UP)
    spec = "move:20:0,delay:200,move:0:-20,delay:200,mdown:forward,delay:300,mup:forward,delay:200,click:back"
    b = build_macro_buffer(1, parse_macro_spec(spec, 10))
    assert b[:14].hex() == "0100" "f9001400c800" "f90000ecc800"
    ev = parse_macro(b)["events"]
    assert ev[0] == {"type": "mouse_move", "dx": 20, "dy": 0} and ev[2]["dy"] == -20
    assert kinds(ev[4:]) == [("mouse_button", "down", 0xF4, None), ("delay", None, None, 300),
                             ("mouse_button", "up", 0xF4, None), ("delay", None, None, 200),
                             ("mouse_button", "down", 0xF3, None), ("delay", None, None, 10),
                             ("mouse_button", "up", 0xF3, None), ("delay", None, None, 10)]


@pytest.mark.xfail(strict=True, reason="Short (<=127 ms) delay after a move: the encoder stores the "
                   "value as is, the decoder halves it (n[1] >> 1). Both are GearMaster ports and only the "
                   "u16 form is hardware-verified; settle it on hardware (roadmap Phase 5).")
def test_move_short_delay_round_trip():
    d = parse_macro(build_macro_buffer(1, parse_macro_spec("move:5:0,delay:50")))
    assert [e["value"] for e in d["events"] if e["type"] == "delay"] == [50]


def test_shifted_text():
    ev = macro_text_events("Hi", 5)
    assert [e["usage"] for e in ev if e["type"] == "key"] == [0xE1, 0x0B, 0x0B, 0xE1, 0x0C, 0x0C]


def test_spec_validation():
    for bad in ("tap:€", "move:200:0", "delay:70000", "click:tilt", "wiggle:1"):
        with pytest.raises((ValueError, KeyError)):
            parse_macro_spec(bad)


# ------------------------------------------------------------ pages and size limit
def test_hi_single_page_frame():
    fr = macro_frames(0, build_macro_buffer(1, macro_text_events("hi", 10)))
    assert len(fr) == 1
    assert fr[0][:18].hex() == "1600000a010000de" "01000b8a0b0a0c8a0c0a"   # total_len 10, last page


def test_clear_frame():
    fr = macro_frames(3, bytes(256))
    assert len(fr) == 1 and fr[0].hex() == "16030000010000e5" + "00" * 56


def test_fox_four_pages():
    # verified on hardware: 174 bytes in 4 pages, read back identically
    b = build_macro_buffer(1, macro_text_events(FOX, 5))
    assert parse_macro(b)["used_bytes"] == 174
    fr = macro_frames(0, b)
    assert [f[:8].hex() for f in fr] == ["160000ae0000003b", "160001ae0000003a",
                                        "160002ae00000039", "160003ae01000037"]
    assert b"".join(f[8:] for f in fr)[:174] == b[:174]


def test_size_limit_keeps_terminator_in_written_pages():
    # content + 4-byte terminator must fit in the 4 x 56 = 224 written bytes
    assert MACRO_LIMIT == 220
    fits = build_macro_buffer(1, macro_text_events("a" * 54, 10))        # 2 + 54 * 4 = 218 bytes
    assert parse_macro(fits)["used_bytes"] == 218 and len(macro_frames(0, fits)) == 4
    with pytest.raises(ValueError):
        build_macro_buffer(1, macro_text_events("a" * 55, 10))           # 222 bytes


def test_terminator_page_is_sent_on_a_page_boundary():
    # repeat (2) + 13 taps (52) + down:b with a 10 ms delay (2) = 56 bytes: the terminator starts page 1
    b = build_macro_buffer(1, parse_macro_spec("text:aaaaaaaaaaaaa,down:b,delay:10", 10))
    assert parse_macro(b)["used_bytes"] == 56
    fr = macro_frames(0, b)
    assert len(fr) == 2 and fr[0][4] == 0 and fr[1][4] == 1 and fr[1][8:] == bytes(56)
    assert fr[0][3] == fr[1][3] == 56


def test_macro_norm():
    b = build_macro_buffer(1, macro_text_events("hi", 10))
    after_write = b[:14] + b"\xff" * 242                     # what the mouse reads back
    assert macro_norm(after_write) == macro_norm(b) == b[:10]
    assert macro_norm(b"\xff" * 256) == macro_norm(bytes(256)) == b""


def test_macro_dataclass_round_trip():
    m = Macro(2, macro_text_events(FOX, 5))
    assert Macro.from_bytes(m.to_bytes()) == m
