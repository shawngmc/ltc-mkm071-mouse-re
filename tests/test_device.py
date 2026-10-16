import pytest

from fakedevice import FACTORY_MATRIX, FakeMouse
from mkm071 import ButtonAction, Macro, Mkm071, WriteNotApplied
from mkm071.macro import macro_text_events, parse_macro_spec

FOX = "the quick brown fox jumps over the lazy dog"


def dev(mouse=None):
    d = Mkm071(mouse or FakeMouse(), wait=0)
    d.check_model()
    return d


def test_read_all():
    st = dev().read_all()
    assert st.model_id == 1833 and st.model_name.startswith("MKM071") and st.active_profile == 0
    assert set(st.profiles) == {0, 1, 2, 3, 4}
    p = st.profiles[0]
    assert [x for x, _, _ in p.dpi.stages] == [800, 2400, 3200, 6400, 9600, 12000]
    assert p.dpi.current_stage == 4
    assert p.matrix.to_bytes() == FACTORY_MATRIX and p.rate_hz == 1000
    assert st.opt0.report_rate_hz == 1000 and st.opt0.light.type_name == "neon"
    assert st.macros[0].events[0]["usage"] == 0x0B and all(st.macros[i].empty for i in range(1, 20))


def test_reads_do_not_write():
    m = FakeMouse()
    dev(m).read_all()
    assert {f[0] for f in m.sent} <= {0x8F, 0x85, 0xD0, 0xD3, 0xD4, 0x84, 0x96}


def test_model_check():
    d = Mkm071(FakeMouse(model_id=1847), wait=0)
    with pytest.raises(IOError, match="check_model"):
        d.set_slot(7, ButtonAction.key(0x22))
    with pytest.raises(IOError, match="not MKM071"):
        d.check_model()
    assert d.check_model(force=True) == 1847


def test_dongle_reads_fail_loudly():
    with pytest.raises(IOError, match="all-zero"):
        Mkm071(FakeMouse(mode="dongle"), wait=0).check_model()


def test_set_slot_targets_profile_without_switching():
    m = FakeMouse()
    after = dev(m).set_slot(7, ButtonAction.key(0x22), profile=2)
    assert after[7].raw.hex() == "00002200" and m.matrix[0] == bytearray(FACTORY_MATRIX) and m.profile == 0
    assert m.frames(0x50)[0][:12].hex() == "50020700000000a6" "00002200"


def test_set_slot_defaults_to_active_profile():
    m = FakeMouse()
    m.profile = 3
    dev(m).set_slot(11, ButtonAction.profile("up"))
    assert m.matrix[3][44:48].hex() == "08000100"


def test_profile_guard():
    m = FakeMouse()
    m.profile = 5                                   # stranded by a "next" press (protocol.md 2.2)
    d = dev(m)
    for write in (lambda: d.set_slot(7, ButtonAction.key(0x22)), lambda: d.set_dpi(0, 1000),
                  lambda: d.switch_profile(5), lambda: d.set_rate(500)):
        with pytest.raises(ValueError):
            write()
    assert d.switch_profile(0) == 0                  # the way back
    assert not m.frames(0x50) and not m.frames(0x54)


def test_dpi_writes_keep_the_rest_of_the_block():
    m = FakeMouse()
    d = dev(m)
    before = bytes(m.opt1[0])
    blk = d.set_dpi(0, 1000)
    assert blk.dpi_x[0] == blk.dpi_y[0] == 1000 and bytes(m.opt1[0])[10:24] == before[10:24]
    assert bytes(m.opt1[0])[40:] == before[40:]
    assert d.set_stage(2).current_stage == 2
    assert d.set_stage_color(1, 0xFF8000).colors[1] == 0xFF8000
    blk = d.read_dpi(1)
    blk.dpi_x[5] = blk.dpi_y[5] = 8000
    assert d.write_dpi_block(blk).dpi_x[5] == 8000 and m.opt1[0][18:20] == before[18:20]


def test_opt0_writes():
    m = FakeMouse()
    d = dev(m)
    o = d.set_opt0(rate=125, x_sens=50)
    assert o.report_rate_hz == 125 and o.x_sensitivity == 50 and o.y_sensitivity == 100
    assert o.light.type_name == "neon"
    assert d.set_light("always,FF0000,4").light.to_bytes().hex() == "01040407ff000000"
    with pytest.raises(ValueError):
        d.set_opt0(debounce=11)
    with pytest.raises(ValueError):
        d.set_opt0(sleep=60)                         # only sleep_bt / sleep_24 here


def test_charging_switch_not_applied():
    # option block 0 byte 60 ignored a write on hardware; the fake does the same
    with pytest.raises(WriteNotApplied) as e:
        dev().set_opt0(charging=1)
    assert e.value.readback.charging_switch == 0


def test_rate_and_profile():
    d = dev()
    assert d.set_rate(500, profile=1) == 500 and d.read_rate(1) == 500 and d.read_rate(0) == 1000
    assert d.switch_profile(3) == 3 and d.active_profile() == 3


def test_write_not_applied_carries_readback():
    m = FakeMouse()
    m.ignore.add(0x50)
    with pytest.raises(WriteNotApplied) as e:
        dev(m).set_slot(7, ButtonAction.key(0x22))
    assert e.value.readback[7].raw.hex() == "00001e00"


def test_macro_write_read_clear():
    m = FakeMouse()
    d = dev(m)
    fox = Macro(1, macro_text_events(FOX, 5))
    assert d.write_macro(4, fox) == fox and len(m.frames(0x16)) == 4
    assert m.macros[4][224:230].hex() == "ff22090212ba"          # stale bytes behind the 4 pages
    assert d.read_macro(4) == fox
    assert d.write_macro(4, None).empty and m.macros[4][:56] == bytes(56)


def test_macro_ending_on_a_page_boundary_reads_back():
    m = Macro(1, parse_macro_spec("text:aaaaaaaaaaaaa,down:b,delay:10", 10))
    assert dev().write_macro(1, m) == m


def test_macro_assignment_end_to_end():
    m = FakeMouse()
    d = dev(m)
    d.write_macro(0, Macro(1, macro_text_events("hi")))
    d.set_slot(10, ButtonAction.macro(0, "toggle"))
    assert d.read_matrix(0)[10].describe() == "macro #0 mode=1"


def test_open_closes_on_model_mismatch(monkeypatch):
    m = FakeMouse(model_id=1847)
    monkeypatch.setattr("mkm071.device.UsbTransport", lambda **kw: m)
    with pytest.raises(IOError):
        Mkm071.open(wait=0)
    assert m.closed
    m2 = FakeMouse()
    monkeypatch.setattr("mkm071.device.UsbTransport", lambda **kw: m2)
    with Mkm071.open(wait=0) as d:
        assert d.model_id() == 1833
    assert m2.closed
