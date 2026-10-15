#!/usr/bin/env python3
"""Measure the mouse's effective report rate: count EV_REL motion packets per second.
Usage: sudo .venv/bin/python tools/rate_check.py [/dev/input/eventN] [seconds]
Move the mouse in fast circles the whole time. With no path, lists candidate devices."""
import struct, sys, time, select, os

def candidates():
    out, cur = [], {}
    for line in open("/proc/bus/input/devices"):
        line = line.strip()
        if line.startswith("N: Name="):
            cur = {"name": line.split("=", 1)[1].strip('"')}
        elif line.startswith("H: Handlers="):
            ev = [h for h in line.split("=", 1)[1].split() if h.startswith("event")]
            if "mouse" in line and ev:
                out.append((cur.get("name"), "/dev/input/" + ev[0]))
    return out

if len(sys.argv) < 2:
    for n, p in candidates():
        print(p, n)
    sys.exit("pass one of the paths above")
path, secs = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 5
fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
SZ = struct.calcsize("llHHi")
print("move the mouse in fast circles for %gs..." % secs)
t0 = time.time(); packets = 0; stamps = []; sx = sy = 0
while time.time() - t0 < secs:
    if not select.select([fd], [], [], 0.1)[0]:
        continue
    try:
        data = os.read(fd, SZ * 64)
    except BlockingIOError:
        continue
    for i in range(0, len(data) - SZ + 1, SZ):
        s, us, typ, code, val = struct.unpack("llHHi", data[i:i + SZ])
        if typ == 2 and code == 0: sx += abs(val)
        if typ == 2 and code == 1: sy += abs(val)
        if typ == 0 and code == 0:   # SYN_REPORT = end of one packet
            packets += 1; stamps.append(s + us / 1e6)
if len(stamps) > 10:
    span = stamps[-1] - stamps[0]
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:]) if b - a < 0.05)
    med = gaps[len(gaps) // 2]
    print("packets %d over %.2fs active -> avg %.0f/s; median gap %.2f ms (~%.0f Hz)" % (
        packets, span, packets / span, med * 1000, 1 / med))
else:
    print("too few events:", packets)
print("total |dx| %d, |dy| %d counts" % (sx, sy))
