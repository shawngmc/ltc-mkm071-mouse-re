#!/usr/bin/env python3
"""Read-only: poll the active profile (query 0x85, ~10 Hz) and print every change with a timestamp.
Needs mkm071_probe.py alongside; mouse wired.  Ctrl+C to stop.
  sudo .venv/bin/python profile_watch.py [seconds=60]"""
import sys, time
import mkm071_probe as P

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
link = P.UsbLink(pid=0x4026, iface=2)
try:
    assert (lambda r: r[1] | r[2] << 8)(bytes(P.query(link, P.OP_GET_REV))) == 1833, "not an MKM071"
    last, t0 = None, time.time()
    print("watching the active profile for %gs; press the profile buttons..." % secs)
    while time.time() - t0 < secs:
        try:
            cur = bytes(P.query(link, P.OP_GET_PROFILE))[1]
        except IOError:
            continue
        if cur != last:
            print("t=%6.2fs  profile %s%s" % (time.time() - t0, cur, "" if last is None else "   (was %s)" % last),
                  flush=True)
            last = cur
        time.sleep(0.1)
except KeyboardInterrupt:
    pass
finally:
    link.close()
