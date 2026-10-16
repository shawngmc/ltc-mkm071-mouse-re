from mkm071.descriptor import DONGLE_IF0_DESC, DONGLE_IF1_DESC, parse_report_descriptor


def test_dongle_keyboard_interface():
    c = {(a["usage_page"], a["usage"]): a["reports"] for a in parse_report_descriptor(DONGLE_IF1_DESC)}
    assert c[(0x0C, 1)] == {"input id=3": 16}           # consumer
    assert c[(0x01, 0x80)] == {"input id=2": 8}         # system control
    assert c[(0x01, 6)] == {"input id=7": 56}           # keyboard
    assert c[(0xFFFF, 1)] == {"input id=5": 24}         # vendor event report


def test_dongle_boot_mouse_interface():
    m = parse_report_descriptor(DONGLE_IF0_DESC)
    assert len(m) == 1 and (m[0]["usage_page"], m[0]["usage"]) == (1, 2)
    assert m[0]["reports"] == {"input id=0": 56}


def test_vendor_feature_report():
    # interface 2 of the wired mouse and dongle: vendor FFFF/2, one 64-byte feature report, no ID
    d = bytes.fromhex("06ffff0902a101" "150026ff00" "75089540" "0902b102" "c0")
    assert parse_report_descriptor(d) == [{"usage_page": 0xFFFF, "usage": 2,
                                           "reports": {"feature id=0": 512}}]


def test_long_item_skipped():
    d = bytes.fromhex("fe0201aabb") + bytes.fromhex("05010902a101c0")
    assert parse_report_descriptor(d)[0]["usage"] == 2
