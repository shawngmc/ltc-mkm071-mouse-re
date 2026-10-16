import pytest

from mkm071 import decode_event


@pytest.mark.parametrize("hexdata,text", [
    ("050c0200", "dongle dpi_stage=2"),        # DPI button on 2.4G (protocol.md 2.9)
    ("05010100", "dongle profile=1"),
    ("050f0000", "dongle indicator=0"),
    ("660c04" + "00" * 62, "bluetooth dpi_stage=4"),   # BLE notification (2.10)
    ("660102" + "00" * 62, "bluetooth profile=2"),
    ("660f01" + "00" * 62, "bluetooth indicator=1"),
    ("05420000", "dongle unknown(0x42)=0"),
])
def test_decode(hexdata, text):
    e = decode_event(bytes.fromhex(hexdata))
    assert str(e) == text and e.raw == bytes.fromhex(hexdata)


@pytest.mark.parametrize("hexdata", ["07001e00000000", "0500", "", "05" + "00" * 63])
def test_not_an_event(hexdata):
    # keyboard report ID 7, too short, and a 64-byte frame starting with 05 (a profile command echo)
    assert decode_event(bytes.fromhex(hexdata)) is None
