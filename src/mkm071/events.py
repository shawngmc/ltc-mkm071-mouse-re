"""Decoders for unsolicited events (no device commands needed to receive them).

- 2.4G dongle: vendor input report 5 on the keyboard interface, `05 <type> <value> 00`
  (docs/protocol.md section 2.9).
- Bluetooth LE: notifications on the vendor input characteristic (handle 0x36),
  `66 <type> <value> 00 ...`, 65 bytes (section 2.10).
Types follow GearMaster's watchVender table (section 6).
"""
from dataclasses import dataclass

EVENT_TYPES = {0x01: "profile", 0x02: "report_rate", 0x03: "power_save", 0x0C: "dpi_stage",
               0x0D: "reset", 0x0F: "indicator", 0x13: "sleep"}
DONGLE_REPORT_ID = 0x05
BLE_PREFIX = 0x66


@dataclass(frozen=True)
class Event:
    source: str   # "dongle" or "bluetooth"
    kind: str     # EVENT_TYPES value, or "unknown(0xNN)"
    value: int
    raw: bytes

    def __str__(self):
        return "%s %s=%d" % (self.source, self.kind, self.value)


def decode_event(data):
    """Decode one dongle report-5 or Bluetooth vendor notification; None if it is neither."""
    data = bytes(data)
    if len(data) < 3:
        return None
    if data[0] == DONGLE_REPORT_ID and len(data) <= 8:
        source = "dongle"
    elif data[0] == BLE_PREFIX:
        source = "bluetooth"
    else:
        return None
    t = data[1]
    return Event(source, EVENT_TYPES.get(t, "unknown(0x%02X)" % t), data[2], data)
