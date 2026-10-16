"""Golden frames for every write builder. Expected bytes are written out from docs/protocol.md
(sections 2.3, 2.7, 5.1-5.5), not computed with the code under test."""
import pytest

from fakedevice import default_opt0, default_opt1
from mkm071.commands import (build_set_opt0, build_set_opt1, build_set_profile, build_set_rate,
                             build_set_slot, parse_light_spec, parse_opt0_spec)


def hdr(frame):
    return frame[:8].hex()


def test_set_slot_golden():
    # 0x50 [profile, slot] chk + action at byte 8; thumb key 1 -> KEY_5 (HID 0x22), the verified write
    f = build_set_slot(0, 7, [0, 0, 34, 0])
    assert f.hex() == "50000700000000a8" "00002200" + "00" * 52
    assert hdr(build_set_slot(2, 11, [9, 2, 3, 0])) == "50020b00000000a2"


@pytest.mark.parametrize("args", [(0, 16, [0, 0, 0, 0]), (0, -1, [0, 0, 0, 0]), (0, 0, [1, 2, 3])])
def test_set_slot_validation(args):
    with pytest.raises(ValueError):
        build_set_slot(*args)


def test_set_profile_golden():
    assert build_set_profile(3).hex() == "05030000000000f7" + "00" * 56
    assert hdr(build_set_profile(0)) == "05000000000000fa"


@pytest.mark.parametrize("n", [-1, 5, 7])
def test_set_profile_validation(n):
    with pytest.raises(ValueError):
        build_set_profile(n)


def test_set_rate_golden():
    assert hdr(build_set_rate(2, 500)) == "04020200000000f7"
    assert hdr(build_set_rate(0, 125)) == "04000800000000f3"
    for bad in ((0, 900), (5, 500)):
        with pytest.raises(ValueError):
            build_set_rate(*bad)


# ------------------------------------------------------------ DPI block (0x54)
def test_set_opt1_echo_and_header():
    cur = bytes(default_opt1(0))                       # active stage 4, 6 stages
    f = build_set_opt1(0, cur)
    assert hdr(f) == "54000406000000a1" and f[8:] == cur[8:]
    assert hdr(build_set_opt1(3, cur)) == "540304060000009e"


def test_set_dpi_changes_both_tables_only():
    cur = bytes(default_opt1(0))
    f = build_set_opt1(0, cur, 0, 1000)
    assert f[8:10].hex() == f[24:26].hex() == "e803"
    assert f[10:24] == cur[10:24] and f[26:] == cur[26:]   # other stages and all colours echoed
    assert f[2] == 4 and (sum(f[:8]) & 0xFF) == 0xFF


def test_set_stage_color_and_active():
    cur = bytes(default_opt1(0))
    f = build_set_opt1(0, cur, color_stage=2, rgb=0xFF8000)
    assert f[46:49].hex() == "ff8000" and f[8:46] == cur[8:46] and f[49:] == cur[49:] and f[2] == 4
    f = build_set_opt1(0, cur, active=2)
    assert hdr(f) == "54000206000000a3" and f[8:] == cur[8:]


@pytest.mark.parametrize("kw", [dict(stage=6, dpi=1000), dict(stage=0, dpi=1025), dict(stage=0, dpi=12050),
                                dict(stage=0, dpi=0), dict(color_stage=6, rgb=1),
                                dict(color_stage=0, rgb=0x1000000), dict(active=6)])
def test_set_opt1_validation(kw):
    with pytest.raises(ValueError):
        build_set_opt1(0, bytes(default_opt1(0)), **kw)


# ------------------------------------------------------------ option block 0 (0x53)
def test_set_opt0_echo():
    cur = bytes(default_opt0())
    f = build_set_opt0(cur)
    assert hdr(f) == "53000000000000ac" and f[8:] == cur[8:]


def test_set_opt0_fields_golden():
    cur = bytes(default_opt0())
    f = build_set_opt0(cur, fields=parse_opt0_spec("rate=125,debounce=6,sleep=60,x_sens=50,y_sens=50,lift=3"))
    changed = {i: f[i] for i in range(8, 64) if f[i] != cur[i]}
    assert changed == {9: 8, 10: 6, 40: 60, 44: 60, 50: 50, 51: 50, 52: 3}
    assert f[42:44] == cur[42:44] and f[46:48] == cur[46:48]           # deep-sleep timers untouched


def test_opt0_spec_parsing():
    assert parse_opt0_spec("sleep=60") == {"sleep_bt": 60, "sleep_24": 60}
    assert parse_opt0_spec("sleep_bt=0x10, angle_snap=0") == {"sleep_bt": 16, "angle_snap": 0}
    assert build_set_opt0(bytes(64), fields={"sleep_bt": 3600})[40:42].hex() == "100e"


@pytest.mark.parametrize("spec", ["rate=900", "debounce=11", "sleep=4000", "sleep_bt=3601", "bogus=1",
                                  "angle_snap=2", "x_sens=5", "y_sens=201", "lift=4", "charging=2"])
def test_opt0_spec_validation(spec):
    with pytest.raises(ValueError):
        parse_opt0_spec(spec)


@pytest.mark.parametrize("spec,record", [
    ("always,FF0000,4", "01040407ff000000"),
    ("always,FFFFFF", "01040407fafafa00"),          # GearMaster writes pure white as FAFAFA
    ("breath,00FF00,2,3", "0201020700ff0000"),
    ("neon", "03040407ffffff00"),                   # no colour given: the current one is kept
    ("off", "0000000000000000"),
])
def test_light_record_golden(spec, record):
    cur = bytes(default_opt0())
    f = build_set_opt0(cur, parse_light_spec(spec))
    assert f[24:32].hex() == record and f[8:24] == cur[8:24] and f[32:] == cur[32:]


@pytest.mark.parametrize("spec", ["sparkle", "always,GG0000", "always,FF0000,5", "always,FF0000,1,9",
                                  "always,1000000"])
def test_light_spec_validation(spec):
    with pytest.raises(ValueError):
        parse_light_spec(spec)
