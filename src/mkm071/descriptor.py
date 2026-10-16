"""HID report-descriptor walker and sysfs lookup (no device access)."""
import glob
import os

from .frame import REPORT_LEN, VID


def parse_report_descriptor(d):
    """Minimal HID report-descriptor walker. Returns top-level application
    collections with their input/output/feature reports (bits per report ID)."""
    d = bytes(d)
    i = 0
    g = {"page": 0, "size": 0, "count": 0, "rid": 0}
    stack, depth, apps, cur = [], 0, [], None
    usage = None
    while i < len(d):
        b = d[i]
        if b == 0xFE:  # long item, skip
            i += 3 + d[i + 1]
            continue
        size = b & 3
        size = 4 if size == 3 else size
        typ, tag = (b >> 2) & 3, b >> 4
        val = int.from_bytes(d[i + 1:i + 1 + size], "little") if size else 0
        i += 1 + size
        if typ == 1:  # global
            if tag == 0:
                g["page"] = val
            elif tag == 7:
                g["size"] = val
            elif tag == 8:
                g["rid"] = val
            elif tag == 9:
                g["count"] = val
            elif tag == 10:
                stack.append(dict(g))
            elif tag == 11 and stack:
                g = stack.pop()
        elif typ == 2:  # local
            if tag == 0 and usage is None:
                usage = val
        elif typ == 0:  # main
            if tag == 10:  # collection
                if depth == 0:
                    cur = {"usage_page": g["page"], "usage": usage, "reports": {}}
                    apps.append(cur)
                depth += 1
            elif tag == 12:
                depth -= 1
            elif tag in (8, 9, 11) and cur is not None:
                kind = {8: "input", 9: "output", 11: "feature"}[tag]
                key = "%s id=%d" % (kind, g["rid"])
                cur["reports"][key] = cur["reports"].get(key, 0) + g["size"] * g["count"]
            usage = None
    return apps


def sysfs_hid():
    """Descriptors for every HID interface of VID 3151, from sysfs (no device access)."""
    out = []
    for p in sorted(glob.glob("/sys/bus/hid/devices/0003:%04X:*" % VID)):
        try:
            data = open(os.path.join(p, "report_descriptor"), "rb").read()
        except OSError:
            continue
        real = os.path.realpath(p)
        try:
            iface = int(open(os.path.join(os.path.dirname(real),
                                          "bInterfaceNumber")).read().strip(), 16)
        except (OSError, ValueError):
            iface = None
        out.append({"name": os.path.basename(p), "iface": iface,
                    "bytes": len(data), "raw_hex": data.hex(),
                    "collections": parse_report_descriptor(data)})
    return out


def pick_feature_interface():
    """From sysfs descriptors: the interface carrying a 64-byte feature report."""
    for h in sysfs_hid():
        for c in h["collections"]:
            for key, bits in c["reports"].items():
                if key.startswith("feature") and bits == REPORT_LEN * 8:
                    return h["iface"], int(key.split("=")[1])
    return None, None


# Real descriptors from the 2.4G dongle (3151:4027), interfaces 1 and 0
DONGLE_IF1_DESC = bytes.fromhex(
    "050c0901a1018503" "1900" "2a3c03" "150026" "3c03" "950175108100c0"
    "050109" "80a1018502" "050119" "8129831500250195037501" "81029501750581" "01c0"
    "0501" "0906a1018507" "050719" "e029e715002501750195088102"
    "1900" "2aff00" "150026ff00" "750895068100c0"
    "06ffff0901a1018505" "0901" "150026ff00" "7508950381" "02c0")
DONGLE_IF0_DESC = bytes.fromhex(
    "05010902a1010901a10005091500250119012905750195058102950381010501"
    "16018026ff7f093009317510950281061581257f09387508950181 06"
    "050c0a3802950181 06c0c0".replace(" ", ""))
