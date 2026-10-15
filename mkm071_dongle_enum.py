#!/usr/bin/env python3
"""READ-ONLY enumeration of the MKM071 2.4G dongle (3151:4027).
Dumps descriptors, HID report descriptors (parsed for report IDs/sizes) and tries
GET_REPORT (feature and input) for each report ID. Never sends SET_REPORT, never claims
or detaches an interface. Optional --listen N reads raw input reports from hidraw nodes.
Usage: sudo .venv/bin/python dongle_enum.py [--pid 0x4027] [--listen 10] > dongle_enum.txt"""
import argparse, glob, os, select, sys, time
import usb.core, usb.util

VID = 0x3151

def parse_rdesc(d):
    """Return list of (kind, report_id, bits, usage_page, usage) for Input/Output/Feature items."""
    items, i = [], 0
    gstack, g = [], {"page": 0, "rid": 0, "size": 0, "count": 0}
    usage = None
    while i < len(d):
        b = d[i]
        if b == 0xFE:  # long item
            i += 3 + d[i + 1]; continue
        size = {0: 0, 1: 1, 2: 2, 3: 4}[b & 3]
        typ, tag = (b >> 2) & 3, b >> 4
        val = int.from_bytes(d[i + 1:i + 1 + size], "little")
        i += 1 + size
        if typ == 1:  # global
            if tag == 0: g["page"] = val
            elif tag == 7: g["size"] = val
            elif tag == 8: g["rid"] = val
            elif tag == 9: g["count"] = val
            elif tag == 10: gstack.append(dict(g))
            elif tag == 11 and gstack: g = gstack.pop()
        elif typ == 2 and tag == 0:  # local usage
            usage = val
        elif typ == 0 and tag in (8, 9, 11):  # main Input/Output/Feature
            items.append(({8: "input", 9: "output", 11: "feature"}[tag], g["rid"],
                          g["size"] * g["count"], g["page"], usage))
            usage = None
    return items

def vendor_iface(dev, a):
    n = 2
    detached = False
    if dev.is_kernel_driver_active(n):
        dev.detach_kernel_driver(n); detached = True
    usb.util.claim_interface(dev, n)
    try:
        rd = bytes(dev.ctrl_transfer(0x81, 0x06, 0x2200, n, 4096, timeout=1000))
        print("iface 2 report descriptor (%d bytes): %s" % (len(rd), rd.hex()))
        seen = {}
        for kind, rid, bits, page, usage in parse_rdesc(rd):
            print("     %-7s id %-3d %4d bits  page 0x%04x usage %s" % (
                kind, rid, bits, page, "0x%x" % usage if usage is not None else "-"))
            seen.setdefault((kind, rid), bits)
        for (kind, rid), bits in sorted(seen.items()):
            if kind == "output":
                continue
            length = (bits + 7) // 8 + (1 if rid else 0)
            try:
                got = bytes(dev.ctrl_transfer(0xA1, 0x01, ((3 if kind == "feature" else 1) << 8) | rid, n, length, timeout=1000))
                print("   GET_REPORT %s id %d: %s%s" % (kind, rid, got.hex(), "   [all zero]" if not any(got) else ""))
            except Exception as e:
                print("   GET_REPORT %s id %d: %s" % (kind, rid, e))
        if a.poke:
            frame = bytearray(64); frame[0] = 0x8F; frame[7] = (0xFF - 0x8F) & 0xFF
            dev.ctrl_transfer(0x21, 0x09, 0x0300, n, bytes(frame), timeout=1000)
            print("sent GET_INFOR (0x8F)")
        end = time.time() + max(a.listen, 3)
        print("reading interrupt endpoint 0x83 for %.0fs..." % (end - time.time()))
        while time.time() < end:
            try:
                data = bytes(dev.read(0x83, 64, timeout=200))
                print("  ep83 %s" % data.hex())
            except usb.core.USBTimeoutError:
                pass
            except usb.core.USBError as e:
                print("  ep83 error:", e); break
        if a.poke:
            for wait in (0.05, 0.3, 1.0):
                time.sleep(wait)
                got = bytes(dev.ctrl_transfer(0xA1, 0x01, 0x0300, n, 64, timeout=1000))
                print("  GET_REPORT after +%.2fs: %s%s" % (wait, got[:16].hex(), "  [all zero]" if not any(got) else ""))
    finally:
        usb.util.release_interface(dev, n)
        if detached:
            try: dev.attach_kernel_driver(n)
            except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", default="0x4027")
    ap.add_argument("--iface2", action="store_true",
                    help="detach the kernel driver from interface 2 (the vendor, hiddev-only one, as "
                         "mkm071_probe does), read its report descriptor and listen on its interrupt endpoint")
    ap.add_argument("--poke", action="store_true",
                    help="with --iface2: also send the harmless GET_INFOR query (0x8F) once and watch for the reply")
    ap.add_argument("--listen", type=float, default=0)
    a = ap.parse_args()
    pid = int(a.pid, 0)
    dev = usb.core.find(idVendor=VID, idProduct=pid)
    if dev is None:
        sys.exit("device 3151:%04x not found" % pid)
    print("device 3151:%04x  bcdUSB %04x  class %02x  strings:" % (pid, dev.bcdUSB, dev.bDeviceClass),
          end=" ")
    for n in ("iManufacturer", "iProduct", "iSerialNumber"):
        try: print(usb.util.get_string(dev, getattr(dev, n)), end=" | ")
        except Exception: print("?", end=" | ")
    print()
    for cfg in dev:
        print("config %d, %d interfaces, maxpower %d mA" % (cfg.bConfigurationValue, cfg.bNumInterfaces, cfg.bMaxPower * 2))
        for intf in cfg:
            drv = None
            try: drv = dev.is_kernel_driver_active(intf.bInterfaceNumber)
            except Exception: pass
            print(" iface %d alt %d class %02x sub %02x proto %02x endpoints %d kernel_driver=%s" % (
                intf.bInterfaceNumber, intf.bAlternateSetting, intf.bInterfaceClass,
                intf.bInterfaceSubClass, intf.bInterfaceProtocol, intf.bNumEndpoints, drv))
            for ep in intf:
                print("   ep 0x%02x attrs %02x maxpkt %d interval %d" % (
                    ep.bEndpointAddress, ep.bmAttributes, ep.wMaxPacketSize, ep.bInterval))
            if intf.bInterfaceClass != 3:
                continue
            rd = None
            if drv:  # bound to the kernel: read the descriptor from sysfs instead of claiming it
                for p in glob.glob("/sys/bus/usb/devices/%d-%s:1.%d/*/hidraw/hidraw*/device/report_descriptor" % (
                        dev.bus, ".".join(map(str, dev.port_numbers)), intf.bInterfaceNumber)) + \
                        glob.glob("/sys/bus/usb/devices/%d-%s:1.%d/*/report_descriptor" % (
                        dev.bus, ".".join(map(str, dev.port_numbers)), intf.bInterfaceNumber)):
                    rd = open(p, "rb").read(); break
                if rd is None:
                    print("   bound to kernel driver; no sysfs report descriptor (probably hiddev-only): "
                          "use --iface2 to read it"); continue
            else:
                try:
                    rd = bytes(dev.ctrl_transfer(0x81, 0x06, 0x2200, intf.bInterfaceNumber, 4096, timeout=1000))
                except Exception as e:
                    print("   report descriptor: ERROR %s" % e); continue
            print("   report descriptor (%d bytes): %s" % (len(rd), rd.hex()))
            seen = {}
            for kind, rid, bits, page, usage in parse_rdesc(rd):
                print("     %-7s id %-3d %4d bits  page 0x%04x usage %s" % (
                    kind, rid, bits, page, "0x%x" % usage if usage is not None else "-"))
                seen.setdefault((kind, rid), bits)
            for (kind, rid), bits in sorted(seen.items()):
                if kind == "output":
                    continue
                length = (bits + 7) // 8 + (1 if rid else 0)
                wtype = 3 if kind == "feature" else 1
                if drv:
                    continue
                try:
                    got = bytes(dev.ctrl_transfer(0xA1, 0x01, (wtype << 8) | rid,
                                                  intf.bInterfaceNumber, length, timeout=1000))
                    print("   GET_REPORT %s id %d (%d B): %s%s" % (
                        kind, rid, len(got), got.hex(), "   [all zero]" if not any(got) else ""))
                except Exception as e:
                    print("   GET_REPORT %s id %d: %s" % (kind, rid, e))
    if a.iface2:
        vendor_iface(dev, a)
    if a.listen:
        print("listening %.0fs on hidraw nodes of 3151:%04x (move the mouse / press buttons)..." % (a.listen, pid))
        fds = {}
        for uevent in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
            if "HID_ID=" in open(uevent).read() and ("%08X:%08X" % (VID, pid)) in open(uevent).read().upper():
                node = "/dev/" + uevent.split("/")[4]
                try: fds[os.open(node, os.O_RDONLY | os.O_NONBLOCK)] = node
                except OSError as e: print("  cannot open", node, e)
        print("  nodes:", list(fds.values()))
        end = time.time() + a.listen
        while time.time() < end and fds:
            for fd in select.select(list(fds), [], [], 0.2)[0]:
                try: data = os.read(fd, 256)
                except BlockingIOError: continue
                print("  %s %s" % (fds[fd], data.hex()))

main()
