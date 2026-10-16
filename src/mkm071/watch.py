"""Following live profile and DPI-stage changes.

- Wired: poll the active profile (0x85) and that profile's DPI block (0xD4); queries only.
- 2.4G dongle: read the unsolicited vendor report 5 from the dongle's keyboard-interface hidraw node;
  nothing is sent to the device (docs/protocol.md section 2.9).
Bluetooth notifications need BlueZ GATT (handle 0x36) and are not handled here yet.
"""
import glob
import os
import select
import time

from .descriptor import parse_report_descriptor
from .events import decode_event
from .frame import PID_DONGLE, VID


def find_event_hidraw(pid=PID_DONGLE, sysfs="/sys/class/hidraw"):
    """The /dev/hidrawN node of the interface carrying vendor input report 5 (page 0xFFFF, usage 1)."""
    tag = "%08X:%08X" % (VID, pid)
    for dev in sorted(glob.glob(os.path.join(sysfs, "hidraw*"))):
        try:
            with open(os.path.join(dev, "device", "uevent")) as f:
                if tag not in f.read().upper():
                    continue
            with open(os.path.join(dev, "device", "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        for c in parse_report_descriptor(desc):
            if (c["usage_page"], c["usage"]) == (0xFFFF, 1) and "input id=5" in c["reports"]:
                return "/dev/" + os.path.basename(dev)
    return None


def read_events(fd, seconds=0, clock=time.monotonic):
    """Yield decoded Events read from an open hidraw fd; stops after `seconds` (0 = never)."""
    end = clock() + seconds if seconds else None
    while end is None or clock() < end:
        timeout = 0.2 if end is None else max(0.0, min(0.2, end - clock()))
        if not select.select([fd], [], [], timeout)[0]:
            continue
        try:
            data = os.read(fd, 64)
        except BlockingIOError:
            continue
        if not data:
            return
        ev = decode_event(data)
        if ev is not None:
            yield ev


def poll_wired(dev, interval=0.1, seconds=0, clock=time.monotonic, sleep=time.sleep):
    """Yield (kind, old, new) for changes of 'profile' and 'dpi_stage' seen by polling."""
    end = clock() + seconds if seconds else None
    last_profile = dev.active_profile()
    last_stage = dev.read_dpi(last_profile).current_stage
    while end is None or clock() < end:
        sleep(interval)
        p = dev.active_profile()
        if p != last_profile:
            yield "profile", last_profile, p
            last_profile = p
        stage = dev.read_dpi(p).current_stage
        if stage != last_stage:
            yield "dpi_stage", last_stage, stage
            last_stage = stage
