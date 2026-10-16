"""Transports for the 64-byte feature-report channel, and the query/command exchange on top of them.

A transport is anything with the hidapi-style feature-report methods (see `Transport`):
`UsbTransport` (libusb control transfers), a `hid.device()` from hidapi, or a fake in tests.
"""
import time
from typing import Protocol

from .frame import (OP_GET_MACRO, REPORT_LEN, VID, USAGE, USAGE_PAGE, build_frame,
                    check_frame, strip_report_id)

WAIT = 0.01  # seconds between SET_REPORT and GET_REPORT
MACRO_PAGES = 4


class Transport(Protocol):
    def send_feature_report(self, data: bytes) -> object:
        """data[0] is the report ID, as in hidapi."""

    def get_feature_report(self, rid: int, n: int) -> bytes:
        """n includes the report-ID byte; the reply starts with the report ID."""

    def close(self) -> None: ...


class UsbTransport:
    """HID feature reports over libusb control transfers (no hidraw needed).
    SET_REPORT = 0x09, GET_REPORT = 0x01, report type 3 (feature)."""

    def __init__(self, pid=None, iface=2):
        import usb.core
        import usb.util
        self.usb = usb
        devs = [d for d in usb.core.find(find_all=True, idVendor=VID)
                if pid is None or d.idProduct == pid]
        if not devs:
            raise IOError("no USB device 3151:%s found" %
                          ("%04x" % pid if pid else "????"))
        self.dev, self.iface, self.detached = devs[0], iface, False
        if self.dev.is_kernel_driver_active(iface):
            self.dev.detach_kernel_driver(iface)
            self.detached = True
        usb.util.claim_interface(self.dev, iface)

    def send_feature_report(self, data):  # data[0] = report ID, as in hidapi
        rid = data[0]
        payload = bytes(data) if rid else bytes(data[1:])
        self.dev.ctrl_transfer(0x21, 0x09, (3 << 8) | rid, self.iface,
                               payload, timeout=1000)

    def get_feature_report(self, rid, n):  # n includes the report-ID byte
        length = n if rid else n - 1
        got = bytes(self.dev.ctrl_transfer(0xA1, 0x01, (3 << 8) | rid,
                                           self.iface, length, timeout=1000))
        return got if rid else b"\x00" + got

    def close(self):
        try:
            self.usb.util.release_interface(self.dev, self.iface)
        finally:
            if self.detached:
                try:
                    self.dev.attach_kernel_driver(self.iface)
                except Exception:
                    pass


UsbLink = UsbTransport  # the name the tools/ scripts use


def all_interfaces():
    import hid
    return list(hid.enumerate(VID, 0))


def find_paths():
    ifs = all_interfaces()
    out = [d for d in ifs
           if d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE]
    if not out:
        out = [d for d in ifs if d.get("interface_number") == 2]
    return out


def query(link, opcode, *args, rid=0, wait=None, echo=True, allow_zero=False):
    """echo=False for replies that carry raw payload from byte 0 (button matrix)."""
    wait = WAIT if wait is None else wait
    link.send_feature_report(bytes([rid]) + build_frame(opcode, *args))
    time.sleep(wait)
    raw = link.get_feature_report(rid, REPORT_LEN + 1)
    reply = strip_report_id(raw, rid)
    # dongle mode answers with all zeros: that is "no data", not a value
    if not any(reply) and not allow_zero:
        raise IOError("all-zero reply for 0x%02X (no data; dongle or sleeping mouse?)" % opcode)
    if echo and (not reply or reply[0] != opcode):
        raise IOError("unexpected reply for 0x%02X: %s" %
                      (opcode, reply[:16].hex()))
    return reply


def send_command(link, frame, rid=0, wait=None):
    """Send one write frame, then drain the ack (its content is unknown). Nothing is verified here."""
    wait = WAIT if wait is None else wait
    check_frame(frame)
    link.send_feature_report(bytes([rid]) + frame)
    time.sleep(wait)
    try:
        link.get_feature_report(rid, REPORT_LEN + 1)
    except Exception:
        pass
    time.sleep(wait)


def read_macro(link, idx, rid=0, wait=None):
    """Raw 256-byte macro buffer (4 pages of 64 bytes). All-zero pages are valid (empty macro)."""
    return b"".join(bytes(query(link, OP_GET_MACRO, idx, page, rid=rid, wait=wait,
                                echo=False, allow_zero=True)[:64]) for page in range(MACRO_PAGES))
