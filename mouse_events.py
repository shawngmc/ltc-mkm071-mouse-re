#!/usr/bin/env python3
"""Log mouse evdev events (buttons, relative motion) with timestamps and gaps.
Usage: sudo .venv/bin/python mouse_events.py               (lists mouse nodes)
       sudo .venv/bin/python mouse_events.py /dev/input/eventN [seconds=15]
Hold the mouse still, then press the macro key once."""
import struct, sys, time, select, os
if len(sys.argv) < 2:
    cur = {}
    for line in open("/proc/bus/input/devices"):
        line = line.strip()
        if line.startswith("N: Name="): cur = {"n": line.split("=", 1)[1]}
        elif line.startswith("H: Handlers=") and "mouse" in line:
            ev = [h for h in line.split("=", 1)[1].split() if h.startswith("event")]
            print("/dev/input/" + ev[0], cur.get("n"))
    sys.exit("pass one of the paths above")
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 15
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK)
SZ = struct.calcsize("llHHi"); t0 = time.time(); last = None
NAMES = {272: "LEFT", 273: "RIGHT", 274: "MIDDLE", 275: "SIDE(back)", 276: "EXTRA(forward)",
         277: "FORWARD", 278: "BACK", 279: "TASK"}
dx = dy = 0
def gap(t):
    global last
    g = "-" if last is None else "%.3fs" % (t - last); last = t; return g
print("press the macro key now (%gs)..." % secs)
while time.time() - t0 < secs:
    if not select.select([fd], [], [], 0.1)[0]: continue
    d = os.read(fd, SZ * 64)
    for i in range(0, len(d) - SZ + 1, SZ):
        s, us, ty, co, v = struct.unpack("llHHi", d[i:i+SZ]); t = s + us / 1e6
        if ty == 2 and co == 0: dx += v
        elif ty == 2 and co == 1: dy += v
        elif ty == 1 and v != 2:
            print("button %-15s %-4s t=%.3f gap=%s" % (NAMES.get(co, co), "down" if v else "up", t, gap(t)))
        elif ty == 0 and (dx or dy):
            print("move dx=%+d dy=%+d       t=%.3f gap=%s" % (dx, dy, t, gap(t))); dx = dy = 0
