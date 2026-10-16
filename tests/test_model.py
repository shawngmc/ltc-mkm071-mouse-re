import random

import pytest

from fakedevice import FACTORY_MATRIX, default_opt0, default_opt1
from mkm071 import ButtonAction, DpiBlock, LightRecord, Macro, Matrix, Opt0
from mkm071.model import SLOT_NAMES, describe_slot, parse_matrix, parse_opt0, parse_opt1


def rand64(seed):
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(64))


# ------------------------------------------------------------ dict parsers (ported selftest)
def test_parse_opt0_synthetic():
    b = bytearray(64)
    b[8] = 2; b[9] = 0x01; b[10] = 10                                 # noqa: E702
    b[12] = 0b00010001; b[14] = 1; b[15] = 10; b[16] = 10             # noqa: E702
    b[24:32] = bytes([2, 1, 3, 8, 255, 0, 0, 0])
    b[40:48] = (1800).to_bytes(2, "little") + (3600).to_bytes(2, "little") * 3
    b[50] = b[51] = 100
    o = parse_opt0(bytes(b))
    assert o["profile"] == 2 and o["report_rate_hz"] == 1000
    assert o["flags"]["light_off"] and o["flags"]["power_save"] and not o["flags"]["smooth"]
    assert o["light"]["type"] == "breathe" and o["light"]["speed"] == 3 and o["light"]["rgb"] == "#FF0000"
    assert o["sleep_s"] == {"bt": 1800, "bt_deep": 3600, "dongle_2g4": 3600, "dongle_2g4_deep": 3600}


def test_parse_opt0_real_values():
    o = parse_opt0(bytes(default_opt0()))
    assert (o["report_rate_hz"], o["debounce"], o["x_sensitivity"], o["angle_snap"]) == (1000, 1, 100, 1)
    assert o["light"] == {"type": "neon", "speed": 1, "value": 4, "flags": 0, "rgb": "#FFFFFF"}
    assert o["sleep_s"]["bt"] == 30 and o["sleep_s"]["bt_deep"] == 5
    assert parse_opt0(bytes(9) + b"\x42" + bytes(54))["report_rate_hz"] == "unknown(0x42)"


def test_parse_opt1():
    b = bytearray(64)
    b[2] = 1; b[3] = 6                                                # noqa: E702
    for i, v in enumerate([800, 1600, 2400, 3200, 4000, 4800, 0, 0]):
        b[8 + 2 * i:10 + 2 * i] = v.to_bytes(2, "little")
        b[24 + 2 * i:26 + 2 * i] = v.to_bytes(2, "little")
    b[40:43] = bytes([255, 0, 0])
    o = parse_opt1(bytes(b))
    assert o["dpi_x"][:6] == [800, 1600, 2400, 3200, 4000, 4800] and o["current_dpi"] == 1600
    assert o["stage_colors"][0] == "#FF0000"
    b[2] = 9
    assert parse_opt1(bytes(b))["current_dpi"] is None


def test_parse_matrix_generic_default():
    default = [1, 0, 240, 0, 1, 0, 241, 0, 1, 0, 242, 0, 1, 0, 244, 0,
               1, 0, 243, 0, 10, 3, 0, 0, 20, 0, 0, 0] + [0] * 36
    m = parse_matrix(bytes(default))
    assert m[0]["meaning"].startswith("mouse left") and m[6]["meaning"] == "dpi_cycle"
    assert m[7]["meaning"] == "disabled"


def test_parse_matrix_real_mkm071():
    m = [d["meaning"] for d in parse_matrix(FACTORY_MATRIX)]
    named = dict(zip(SLOT_NAMES.values(), m))
    assert named["left"] == "mouse left" and named["right"] == "mouse right"
    assert named["profile_button"] == "profile_cycle" and named["dpi_button"] == "dpi_cycle"
    assert named["forward"] == "mouse forward" and named["back"] == "mouse back"
    assert named["wheel_click"] == "mouse middle"
    assert named["thumb_1"] == "keyboard mods=0x00 key=0x1E key2=0x00"   # KEY_1
    assert named["wheel_up"] == "mouse 0xF9 arg=1"


@pytest.mark.parametrize("raw,meaning", [
    ([20, 0, 1, 0], "dpi_up"), ([20, 0, 2, 0], "dpi_down"), ([20, 0, 4, 0], "dpi_shift"),
    ([8, 0, 1, 0], "profile_up"), ([8, 0, 2, 0], "profile_down"), ([8, 0, 4, 4], "profile_exchange 4"),
    ([9, 2, 3, 0], "macro #3 mode=2"), ([3, 0, 0xE9, 0], "consumer usage=0x00E9"),
    ([0, 0xE0, 6, 0], "keyboard mods=0xE0 key=0x06 key2=0x00"),
    ([0x63, 1, 2, 3], "function/unknown [99, 1, 2, 3]"),
])
def test_describe_slot(raw, meaning):
    assert describe_slot(raw) == meaning


# ------------------------------------------------------------ typed model
@pytest.mark.parametrize("seed", range(25))
def test_blocks_round_trip_byte_exact(seed):
    b = rand64(seed)
    assert Opt0.from_bytes(b).to_bytes() == b
    assert DpiBlock.from_bytes(b).to_bytes() == b
    assert Matrix.from_bytes(b).to_bytes() == b
    assert LightRecord.from_bytes(b[:8]).to_bytes() == b[:8]


@pytest.mark.parametrize("seed", range(10))
def test_typed_and_dict_parsers_agree(seed):
    b = rand64(seed)
    o, d = Opt0.from_bytes(b), parse_opt0(b)
    assert o.profile == d["profile"] and o.debounce == d["debounce"]
    assert (o.sleep_bt, o.deep_sleep_bt, o.sleep_24, o.deep_sleep_24) == tuple(d["sleep_s"].values())
    assert (o.x_sensitivity, o.y_sensitivity, o.lift_cutoff, o.angle_snap) == (
        d["x_sensitivity"], d["y_sensitivity"], d["lift_cutoff"], d["angle_snap"])
    assert o.light.speed == d["light"]["speed"] and "#%06X" % o.light.rgb == d["light"]["rgb"]
    assert ["#%06X" % c for c in o.battery_colors] == d["battery_colors"]
    p, q = DpiBlock.from_bytes(b), parse_opt1(b)
    assert (p.dpi_x, p.dpi_y, p.current_dpi) == (q["dpi_x"], q["dpi_y"], q["current_dpi"])
    assert ["#%06X" % c for c in p.colors] == q["stage_colors"]
    assert [s.describe() for s in Matrix.from_bytes(b).slots] == [x["meaning"] for x in parse_matrix(b)]


def test_editing_a_field_changes_only_that_field():
    raw = bytes(default_opt0())
    o = Opt0.from_bytes(raw)
    o.y_sensitivity = 50
    out = o.to_bytes()
    assert out[51] == 50 and out[:51] == raw[:51] and out[52:] == raw[52:]
    blk = DpiBlock.from_bytes(bytes(default_opt1(0)))
    blk.dpi_x[0] = blk.dpi_y[0] = 1000
    frame = blk.to_frame()
    assert frame[:4].hex() == "54000406" and frame[8:10] == frame[24:26] == (1000).to_bytes(2, "little")
    assert frame[40:64] == bytes(default_opt1(0))[40:64]


def test_dpi_block_real_values():
    blk = DpiBlock.from_bytes(bytes(default_opt1(0)))
    assert [x for x, _, _ in blk.stages] == [800, 2400, 3200, 6400, 9600, 12000]
    assert blk.current_stage == 4 and blk.current_dpi == 9600 and blk.colors[1] == 0xFF8000


@pytest.mark.parametrize("action,hexbytes,kind", [
    (ButtonAction.mouse("left"), "0100f000", "mouse"), (ButtonAction.mouse("forward"), "0100f400", "mouse"),
    (ButtonAction.key(0x22), "00002200", "key"), (ButtonAction.key(0x06, mods=0xE0), "00e00600", "key"),
    (ButtonAction.macro(3, "hold"), "09020300", "macro"), (ButtonAction.macro(0), "09000000", "macro"),
    (ButtonAction.dpi("cycle"), "14000000", "dpi"), (ButtonAction.dpi("shift"), "14000400", "dpi"),
    (ButtonAction.profile("cycle"), "08000300", "profile"),
    (ButtonAction.profile("up"), "08000100", "profile"),
    (ButtonAction.disabled(), "00000000", "disabled"),
])
def test_button_actions(action, hexbytes, kind):
    assert bytes(action).hex() == hexbytes and action.kind == kind


def test_button_action_validation():
    for bad in (lambda: ButtonAction.macro(20), lambda: ButtonAction.mouse("tilt"),
                lambda: ButtonAction(b"\x01\x02\x03")):
        with pytest.raises((ValueError, KeyError)):
            bad()


def test_short_buffers_rejected():
    for cls in (Opt0, DpiBlock, Matrix):
        with pytest.raises(ValueError):
            cls.from_bytes(bytes(63))


def test_macro_slot_states():
    assert Macro.from_bytes(b"\xff" * 256).empty                      # never written (erased flash)
    assert Macro.from_bytes(bytes(56) + b"\xff" * 200).empty          # cleared
    assert Macro().to_bytes() == bytes(256)
