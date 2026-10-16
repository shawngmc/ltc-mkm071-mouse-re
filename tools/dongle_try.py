#!/usr/bin/env python3
"""Low-risk dongle experiments (3151:4027), only patterns GearMaster itself uses. Needs
mkm071_probe.py in the same folder.  Everything is a read, a report-ID-5 listen, or the
documented '[0xFE, n]' next-packet-length frame followed by the harmless 0x8F query.
  sudo .venv/bin/python tools/dongle_try.py --listen 20     # press DPI / profile buttons, wake/sleep the mouse
  sudo .venv/bin/python tools/dongle_try.py --getinput      # ask for input report 5 on the keyboard interface
  sudo .venv/bin/python tools/dongle_try.py --fe 8          # [FE 08] then 0x8F query"""
import argparse, fcntl, glob, os, select, time
import mkm071_probe as P

VID, PID = 0x3151, 0x4027

def hidraw_nodes():
    out = []
    for u in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        txt = open(u).read().upper()
        if "%08X:%08X" % (VID, PID) in txt:
            node = "/dev/" + u.split("/")[4]
            rd = open(os.path.dirname(u) + "/report_descriptor", "rb").read()
            out.append((node, rd))
    return out

def vendor_node():
    for node, rd in hidraw_nodes():
        if b"\x06\xff\xff\x09\x01\xa1\x01\x85\x05" in rd or (b"\x85\x05" in rd and b"\xff\xff" in rd):
            return node
    return None

def hidiocginput(fd, rid, n):
    # HIDIOCGINPUT(len) = _IOC(_IOC_READ|_IOC_WRITE, 'H', 0x0A, len)
    buf = bytearray(n); buf[0] = rid
    req = (3 << 30) | (n << 16) | (ord("H") << 8) | 0x0A
    fcntl.ioctl(fd, req, buf, True)
    return bytes(buf)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=float, default=0)
    ap.add_argument("--getinput", action="store_true")
    ap.add_argument("--fe", type=int, default=None, metavar="N")
    a = ap.parse_args()
    node = vendor_node()
    print("vendor report-5 interface:", node)
    if a.getinput and node:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        for rid, n in ((5, 4), (5, 8), (5, 65)):
            try: print("GET_INPUT id %d len %d: %s" % (rid, n, hidiocginput(fd, rid, n).hex()))
            except OSError as e: print("GET_INPUT id %d len %d: %s" % (rid, n, e))
        os.close(fd)
    if a.fe is not None:
        link = P.UsbLink(PID, 2)
        try:
            fe = bytearray(64); fe[0], fe[1] = 0xFE, a.fe          # NONE checksum, as in GearMaster
            link.send_feature_report(bytes([0]) + bytes(fe)); time.sleep(0.02)
            print("sent FE %02x" % a.fe)
            link.send_feature_report(bytes([0]) + P.build_frame(0x8F))
            for w in (0.02, 0.1, 0.5, 1.0):
                time.sleep(w)
                got = link.get_feature_report(0, 65)
                print("  reply after +%.2fs: %s%s" % (w, got[1:17].hex(), "   [all zero]" if not any(got) else ""))
        finally:
            link.close()
    if a.listen and node:
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        print("listening %.0fs on %s (press DPI/profile buttons; turn the mouse off/on; put it on the dock)..." % (a.listen, node))
        end = time.time() + a.listen
        while time.time() < end:
            if select.select([fd], [], [], 0.2)[0]:
                try: d = os.read(fd, 64)
                except BlockingIOError: continue
                tag = "  <== report 5" if d[:1] == b"\x05" else ""
                print("  %s %s%s" % (time.strftime("%H:%M:%S"), d.hex(), tag))

main()
