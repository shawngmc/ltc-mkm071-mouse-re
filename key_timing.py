#!/usr/bin/env python3
"""Print timestamps of key/button events from an evdev node, with gaps between events.
Usage: sudo .venv/bin/python key_timing.py            (lists keyboard-ish devices)
       sudo .venv/bin/python key_timing.py /dev/input/eventN [seconds=15]
Press the macro key once; stops after <seconds>."""
import struct, sys, time, select, os
if len(sys.argv) < 2:
    cur = {}
    for line in open("/proc/bus/input/devices"):
        line = line.strip()
        if line.startswith("N: Name="): cur = {"n": line.split("=", 1)[1]}
        elif line.startswith("H: Handlers=") and "kbd" in line:
            ev = [h for h in line.split("=", 1)[1].split() if h.startswith("event")]
            print("/dev/input/" + ev[0], cur.get("n"))
    sys.exit("pass one of the paths above (the LTC/MKM071 keyboard node)")
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 15
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK)
SZ = struct.calcsize("llHHi"); t0 = time.time(); last = None
print("press the macro key now (%gs)..." % secs)
while time.time() - t0 < secs:
    if not select.select([fd], [], [], 0.1)[0]: continue
    d = os.read(fd, SZ * 64)
    for i in range(0, len(d) - SZ + 1, SZ):
        s, us, ty, co, v = struct.unpack("llHHi", d[i:i+SZ])
        if ty != 1 or v == 2: continue
        t = s + us / 1e6
        print("key %3d %-4s t=%.3f gap=%s" % (co, "down" if v else "up", t,
              "-" if last is None else "%.3fs" % (t - last)))
        last = t
