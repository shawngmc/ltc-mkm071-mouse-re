#!/usr/bin/env python3
"""Read-only: record the Bluetooth identity of the MKM071 once it is paired.
Needs tools/mkm071_probe.py alongside. Sends nothing to the mouse.
Usage: .venv/bin/python tools/bt_ids.py > bt_ids.json"""
import glob, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mkm071_probe import parse_report_descriptor

def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return "ERR %s" % e

out = {"bluetoothctl": {}, "hid_bt": [], "input_bt": []}
for line in run("bluetoothctl", "devices").splitlines():
    m = re.match(r"Device ([0-9A-F:]{17}) (.*)", line)
    if m:
        out["bluetoothctl"][m.group(1)] = {"name": m.group(2),
                                           "info": run("bluetoothctl", "info", m.group(1)).splitlines()}
for p in sorted(glob.glob("/sys/bus/hid/devices/0005:*")):
    rec = {"id": os.path.basename(p)}
    try:
        for l in open(os.path.join(p, "uevent")).read().splitlines():
            k, _, v = l.partition("=")
            if k in ("HID_ID", "HID_NAME", "HID_UNIQ", "DRIVER"):
                rec[k] = v
        d = open(os.path.join(p, "report_descriptor"), "rb").read()
        rec.update(bytes=len(d), raw_hex=d.hex(), collections=parse_report_descriptor(d))
    except OSError as e:
        rec["error"] = str(e)
    rec["hidraw"] = [os.path.basename(h) for h in glob.glob(os.path.join(p, "hidraw", "hidraw*"))]
    out["hid_bt"].append(rec)
cur = {}
for line in open("/proc/bus/input/devices").read().split("\n\n"):
    if "Bus=0005" in line:
        out["input_bt"].append(line.strip().splitlines())
print(json.dumps(out, indent=1))
