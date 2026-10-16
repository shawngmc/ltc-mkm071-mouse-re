import pytest

from fakedevice import FACTORY_MATRIX, FakeMouse
from mkm071.frame import OP_GET_BATTERY, OP_GET_ID, OP_GET_MATRIX, OP_GET_REV
from mkm071.transport import query, read_macro, send_command
from mkm071.commands import build_set_profile


def test_matrix_reply_has_no_echo():
    m = FakeMouse()
    assert query(m, OP_GET_MATRIX, 0, echo=False, wait=0)[:16] == FACTORY_MATRIX[:16]
    with pytest.raises(IOError, match="unexpected reply"):
        query(m, OP_GET_MATRIX, 0, wait=0)


def test_echo_only_replies():
    m = FakeMouse()
    assert query(m, OP_GET_ID, wait=0)[:8].hex() == "f10000000000000e"
    assert query(m, OP_GET_BATTERY, wait=0, echo=False)[:8].hex() == "820000000000007d"


def test_dongle_zero_replies():
    m = FakeMouse(mode="dongle")
    with pytest.raises(IOError, match="all-zero"):
        query(m, OP_GET_REV, wait=0)
    assert query(m, OP_GET_REV, wait=0, allow_zero=True, echo=False) == bytes(64)


def test_report_id_framing():
    m = FakeMouse()
    query(m, OP_GET_REV, wait=0)
    send_command(m, build_set_profile(2), wait=0)
    assert [f[:2].hex() for f in m.sent] == ["8f00", "0502"] and m.profile == 2


def test_read_macro_pages():
    m = FakeMouse()
    raw = read_macro(m, 0, wait=0)
    assert len(raw) == 256 and raw[:10].hex() == "01000b8a0b0a0c8a0c0a"
    assert [f[:3].hex() for f in m.sent] == ["960000", "960001", "960002", "960003"]
    assert read_macro(m, 5, wait=0) == b"\xff" * 256          # never written
