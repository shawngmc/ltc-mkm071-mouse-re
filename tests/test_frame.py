import pytest

from fakedevice import FakeMouse, NeverSendReceived
from mkm071.frame import (OP_GET_FNMATRIX, OP_GET_ID, READ_OPCODES, WRITE_OPCODES, UnsafeCommand,
                          build_command, build_frame, check_frame, checksum7, strip_report_id)
from mkm071.transport import send_command


@pytest.mark.parametrize("op", sorted(READ_OPCODES))
def test_query_frames_sum_to_ff(op):
    f = build_frame(op, 1, 2, 3)
    assert len(f) == 64 and (sum(f[:8]) & 0xFF) == 0xFF and f[1:4] == bytes([1, 2, 3])


def test_checksum_golden():
    assert build_frame(OP_GET_ID)[:8].hex() == "f10000000000000e"     # 0xFF - 0xF1
    assert build_frame(OP_GET_FNMATRIX)[0] == 0xD1
    assert checksum7(bytes([0x50, 0, 7])) == 0xA8
    assert checksum7(bytes([0xFF, 0xFF])) == 0x01                     # sum wraps mod 256


@pytest.mark.parametrize("op", [0x01, 0x53, 0x7F, 0xBA])
def test_build_frame_is_query_only(op):
    with pytest.raises(ValueError):
        build_frame(op)


def test_build_frame_arg_limit():
    with pytest.raises(ValueError):
        build_frame(OP_GET_ID, *range(7))


@pytest.mark.parametrize("op", sorted(WRITE_OPCODES))
def test_build_command(op):
    f = build_command(op, 1, 2, payload=b"\xAA" * 56)
    assert len(f) == 64 and (sum(f[:8]) & 0xFF) == 0xFF and f[8:] == b"\xAA" * 56
    with pytest.raises(ValueError):
        build_command(op, payload=bytes(57))


@pytest.mark.parametrize("frame", [
    bytes([0x01]) + bytes(63),                               # factory reset
    bytes.fromhex("7f55aa55aa000082") + bytes(56),           # FEA_CMD_SET_BOOTLOATER
    bytes.fromhex("baff") + bytes(62),
    bytes.fromhex("bac0") + bytes(62),
    bytes([0x0A]) + bytes(63),                               # full-matrix write: not in the allow-list
])
def test_unsafe_frames_refused_before_sending(frame):
    with pytest.raises(UnsafeCommand):
        check_frame(frame)
    mouse = FakeMouse()
    with pytest.raises(UnsafeCommand):
        send_command(mouse, frame, wait=0)
    assert mouse.sent == []
    if frame[0] in (0x01, 0x7F, 0xBA):
        with pytest.raises(UnsafeCommand):
            build_command(frame[0])


def test_fake_mouse_catches_raw_never_send():
    """Scripts that bypass send_command still cannot get one past the test double."""
    with pytest.raises(NeverSendReceived):
        FakeMouse().send_feature_report(b"\x00\x01" + bytes(63))


def test_strip_report_id():
    assert strip_report_id(b"\x00" + bytes(64)) == bytes(64)
    assert strip_report_id(b"\x05" + bytes(64), 5) == bytes(64)
    assert strip_report_id(bytes(64)) == bytes(64)              # already stripped
