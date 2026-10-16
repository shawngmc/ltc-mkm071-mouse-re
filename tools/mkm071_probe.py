#!/usr/bin/env python3
"""Read-only probe for Rongyuan-family mice (LTC MKM071 / GM025 / ...).

Protocol notes: see docs/protocol.md. The protocol code lives in the mkm071 package (src/mkm071);
this script is its read-only command line and re-exports its names for the other tools.

Safety: this tool only sends opcodes from READ_OPCODES (device ID, firmware,
profile, report rate, option blocks, button matrix). It has no write path, and it
never sends bootloader, reset or firmware commands.

Usage:
  python3 tools/mkm071_probe.py --selftest      # run the offline test suite (tests/, needs pytest)
  python3 tools/mkm071_probe.py --desc          # read HID report descriptors from sysfs (no device access)
  python3 tools/mkm071_probe.py --list          # list hidapi interfaces for VID 3151
  python3 tools/mkm071_probe.py --dump          # query via hidapi/hidraw
  sudo python3 tools/mkm071_probe.py --dump --usb [--pid 0x4027] [--iface 2] [--rid 0]
                                           # query via libusb control transfers

--usb is needed when the config interface has no hidraw node (the 2.4G dongle
binds interface 2 as hiddev only). It temporarily detaches the kernel driver from
that one interface and re-attaches it on exit.
Needs `pip install hidapi` (hidapi mode) or `pip install pyusb` + libusb (--usb).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from mkm071 import transport  # noqa: E402
from mkm071.descriptor import parse_report_descriptor, pick_feature_interface, sysfs_hid  # noqa: E402,F401
from mkm071.frame import *  # noqa: E402,F401,F403  (re-exported for the other tools)
from mkm071.frame import (OP_GET_BATTERY, OP_GET_FNMATRIX, OP_GET_ID, OP_GET_MATRIX,  # noqa: E402
                          OP_GET_OPT0, OP_GET_OPT1, OP_GET_PROFILE, OP_GET_REPORT, OP_GET_REV)
from mkm071.macro import parse_macro  # noqa: E402,F401
from mkm071.model import (KNOWN_IDS, LIGHT_TYPES, RATE_CODES, describe_slot,  # noqa: E402,F401
                          parse_light, parse_matrix, parse_opt0, parse_opt1, rgb)
from mkm071.transport import (MACRO_PAGES, UsbLink, all_interfaces, find_paths,  # noqa: E402,F401
                              query, read_macro)


def dump(args):
    iface, rid = args.iface, args.rid
    if args.usb:
        if iface is None:
            iface, auto_rid = pick_feature_interface()
            iface = 2 if iface is None else iface
            rid = auto_rid if (rid is None and auto_rid is not None) else rid
        rid = 0 if rid is None else rid
        link = UsbLink(pid=args.pid, iface=iface)
        label = {"transport": "libusb", "iface": iface, "report_id": rid}
    else:
        import hid
        cands = find_paths()
        if not cands:
            sys.exit("no vendor interface found via hidapi; try --desc, then --usb")
        link = hid.device()
        link.open_path(args.path.encode() if args.path else cands[0]["path"])
        rid = 0 if rid is None else rid
        label = {"transport": "hidapi", "path": cands[0]["path"].decode()}
    info = dict(label)
    errors = {}

    def attempt(name, fn):
        try:
            info[name] = fn()
        except Exception as e:  # keep going, report everything we could read
            errors[name] = "%s: %s" % (type(e).__name__, e)

    try:
        # On the wired MKM071, GET_REV (0x8F) returns the device-table ID (1833 = MKM071),
        # not a firmware version.
        rev = query(link, OP_GET_REV, rid=rid)
        info["model_id"] = rev[1] | rev[2] << 8
        info["model_guess"] = KNOWN_IDS.get(info["model_id"], "unknown")
        info["rev_reply_hex"] = rev[:8].hex()
    except Exception as e:
        errors["model_id"] = "%s: %s" % (type(e).__name__, e)
    try:
        if args.f1:  # 0xF1 only echoes the request on the application firmware
            attempt("f1_reply_hex", lambda: query(link, OP_GET_ID, rid=rid)[:8].hex())
        attempt("profile", lambda: query(link, OP_GET_PROFILE, rid=rid)[1])
        attempt("opt0", lambda: parse_opt0(query(link, OP_GET_OPT0, rid=rid)))
        attempt("opt1", lambda: parse_opt1(
            query(link, OP_GET_OPT1, info.get("profile", 0), rid=rid)))
        attempt("matrix", lambda: parse_matrix(
            query(link, OP_GET_MATRIX, info.get("profile", 0), rid=rid, echo=False)))
        if args.profiles:
            profs = {}
            for p in range(args.profile_count):
                try:
                    m = bytes(query(link, OP_GET_MATRIX, p, rid=rid, echo=False)[:64])
                    o1 = bytes(query(link, OP_GET_OPT1, p, rid=rid)[:64])
                    rr = bytes(query(link, OP_GET_REPORT, p, rid=rid)[:8])
                    profs[p] = {"matrix_hex": m.hex(), "opt1_hex": o1.hex(),
                                "rate_reply_hex": rr.hex(),
                                "dpi_x": parse_opt1(o1)["dpi_x"],
                                "current_stage": o1[2],
                                "stage_colors": parse_opt1(o1)["stage_colors"][:6],
                                "buttons": [d["meaning"] for d in parse_matrix(m)]}
                except Exception as e:
                    profs[p] = {"error": "%s: %s" % (type(e).__name__, e)}
            same = {p: [q for q in profs if q != p and "error" not in profs[q] and
                        "error" not in profs[p] and
                        profs[q]["matrix_hex"] == profs[p]["matrix_hex"] and
                        # byte 1 of the DPI block is the profile index itself, so skip it
                        profs[q]["opt1_hex"][4:] == profs[p]["opt1_hex"][4:]] for p in profs}
            info["profiles"] = profs
            info["identical_to"] = same
        if args.macros:
            macros = {}
            for m in range(args.macros):
                try:
                    raw = read_macro(link, m, rid=rid)
                    macros[m] = ({"empty": True} if not any(raw) else
                                 {"erased_flash": True} if raw == b"\xff" * len(raw) else
                                 {"hex": raw.rstrip(b"\0").hex(), **parse_macro(raw)})
                except Exception as e:
                    macros[m] = {"error": "%s: %s" % (type(e).__name__, e)}
                    break
            info["macros"] = macros
        if args.fn:
            attempt("fn_matrix", lambda: parse_matrix(
                query(link, OP_GET_FNMATRIX, info.get("profile", 0), rid=rid, echo=False)))
        if args.battery:
            # Raw reply only; layout unknown. The mouse may echo, return zeros, or stall.
            attempt("battery_raw_hex", lambda: query(
                link, OP_GET_BATTERY, rid=rid, echo=False)[:16].hex())
    finally:
        link.close()
    if errors:
        info["errors"] = errors
    print(json.dumps(info, indent=2))



def selftest():
    """The offline checks now live in the pytest suite (tests/); run it."""
    tests = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests")
    try:
        import pytest
    except ImportError:
        sys.exit("--selftest runs the test suite in tests/; install pytest (pip install -e '.[dev]')")
    sys.exit(pytest.main(["-q", tests]))


def auto_int(s):
    return int(s, 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--desc", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--usb", action="store_true")
    ap.add_argument("--pid", type=auto_int)
    ap.add_argument("--iface", type=int)
    ap.add_argument("--rid", type=int)
    ap.add_argument("--path")
    ap.add_argument("--f1", action="store_true", help="also send the 0xF1 query")
    ap.add_argument("--profiles", action="store_true", help="read matrix, DPI block and rate for each profile index")
    ap.add_argument("--profile-count", type=int, default=5)
    ap.add_argument("--macros", type=int, nargs="?", const=20, default=0,
                    help="read macro slots 0..N-1 (default 20) with opcode 0x96")
    ap.add_argument("--fn", action="store_true", help="also read the FN-layer matrix (0xD1)")
    ap.add_argument("--battery", action="store_true", help="try the 0x82 battery query")
    ap.add_argument("--wait", type=float, default=0.01,
                    help="seconds to wait before reading the reply (try 0.2 on 2.4G)")
    a = ap.parse_args()
    transport.WAIT = a.wait
    if a.selftest:
        selftest()
    elif a.desc:
        for h in sysfs_hid():
            print("%s  iface=%s  %d-byte descriptor" % (h["name"], h["iface"], h["bytes"]))
            for c in h["collections"]:
                print("   usage_page=0x%04X usage=%s  %s" % (
                    c["usage_page"], c["usage"], c["reports"] or "(no reports)"))
            print("   raw: " + h["raw_hex"])
    elif a.list:
        ifs = all_interfaces()
        if not ifs:
            print("hidapi sees no VID 3151 devices (wrong `hid` module? you need `pip install hidapi`)")
        for d in ifs:
            print("if=%s usage_page=0x%04X usage=0x%02X %04x:%04x %s %s" % (
                d.get("interface_number"), d["usage_page"], d["usage"],
                d["vendor_id"], d["product_id"], d.get("product_string"), d["path"]))
    elif a.dump:
        dump(a)
    else:
        ap.print_help()
