#!/usr/bin/env python3
"""Read-only: compare the base matrix (0xD0) and the FN-layer matrix (0xD1) for profiles 0..4.
Sends only the query opcodes 0xD0 / 0xD1 (and 0x8F for the model check). Needs mkm071_probe.py.
  sudo .venv/bin/python tools/fn_scan.py > fn_scan.txt"""
import mkm071_probe as P

link = P.UsbLink(pid=0x4026, iface=2)
try:
    rev = bytes(P.query(link, P.OP_GET_REV))
    assert rev[1] | rev[2] << 8 == 1833, "not an MKM071"
    print("active profile:", bytes(P.query(link, P.OP_GET_PROFILE))[1])
    base, fn = {}, {}
    for p in range(5):
        base[p] = bytes(P.query(link, P.OP_GET_MATRIX, p, echo=False)[:64])
        fn[p] = bytes(P.query(link, P.OP_GET_FNMATRIX, p, echo=False)[:64])
    for p in range(5):
        print("profile %d  D0 %s" % (p, base[p].hex()))
        print("profile %d  D1 %s" % (p, fn[p].hex()))
    for p in range(5):
        same_base = [q for q in range(5) if fn[p] == base[q]]
        print("D1(%d) equals D0 of profiles %s; equals D1 of profiles %s" % (
            p, same_base, [q for q in range(5) if fn[q] == fn[p]]))
finally:
    link.close()
