#!/usr/bin/env python3
"""MKM071 FN-layer test (wired mode). Companion to mkm071_probe.py / mkm071_write.py.

Question: is the FN-layer matrix (read 0xD1, write 0x51) a separate layer, an alias of the
base matrix (0xD0 / 0x50), or unusable? This writes ONE slot of the FN layer (default: slot 14,
an unused wheel-tilt slot on this mouse), reads both matrices, reports which one changed,
then puts everything back. Same safety flow as mkm071_write.py:
  1. model id (0x8F) must be 1833 unless --force-model
  2. both matrices are saved to captures/fn-backup-*.json first (--backup-dir)
  3. dry run by default; nothing is sent without --yes
  4. after the write both matrices are re-read and compared
  5. the original value is written back automatically (0x51 for the FN layer, 0x50 for the base
     layer if it changed too) and verified; --keep skips this. --restore FILE restores by hand.
Only slot writes (0x51, plus 0x50 if needed to undo) are ever sent. No other write opcodes.

  sudo .venv/bin/python tools/mkm071_fn_test.py                  # dry run
  sudo .venv/bin/python tools/mkm071_fn_test.py --yes
  sudo .venv/bin/python tools/mkm071_fn_test.py --restore captures/fn-backup-XXXX.json --yes
"""
import argparse, datetime, json, os, sys, time

import mkm071_probe as P

CAPTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captures")

OP_SET_FN = 0x51
OP_SET_BASE = 0x50
MKM071_ID = 1833


def build_set(opcode, profile, slot, action):
    action = bytes(action)
    if opcode not in (OP_SET_FN, OP_SET_BASE):
        raise ValueError("only 0x50 / 0x51 slot writes are allowed here")
    if not 0 <= slot < 16 or len(action) != 4:
        raise ValueError("slot 0..15 and a 4-byte action required")
    f = bytearray(64)
    f[0], f[1], f[2] = opcode, profile, slot
    f[7] = P.checksum7(f)
    f[8:12] = action
    return bytes(f)


def slot_of(m, i):
    return list(m[4 * i:4 * i + 4])


def read_both(link, profile, rid, wait):
    base = bytes(P.query(link, P.OP_GET_MATRIX, profile, rid=rid, wait=wait, echo=False)[:64])
    fn = bytes(P.query(link, P.OP_GET_FNMATRIX, profile, rid=rid, wait=wait, echo=False)[:64])
    return base, fn


def send(link, frame, rid, wait):
    link.send_feature_report(bytes([rid]) + frame)
    time.sleep(wait)
    try:
        link.get_feature_report(rid, P.REPORT_LEN + 1)   # drain the ack
    except Exception:
        pass
    time.sleep(wait)


def verdict(base0, fn0, base1, fn1, slot, action):
    b_changed = slot_of(base1, slot) != slot_of(base0, slot)
    f_changed = slot_of(fn1, slot) != slot_of(fn0, slot)
    if f_changed and not b_changed:
        return "SEPARATE LAYER: 0x51 changed the FN matrix only; the base matrix is untouched"
    if f_changed and b_changed:
        return "ALIAS / SHARED: 0x51 changed both 0xD1 and 0xD0 (same storage)"
    if b_changed and not f_changed:
        return "UNEXPECTED: only the base matrix changed"
    return "NOT APPLIED: neither matrix changed (0x51 ignored or not supported on this firmware)"


def run(link, a):
    rev = bytes(P.query(link, P.OP_GET_REV, rid=a.rid, wait=a.wait))
    model = rev[1] | rev[2] << 8
    if model != MKM071_ID and not a.force_model:
        sys.exit("model id %d is not MKM071 (%d); refusing" % (model, MKM071_ID))
    profile = a.profile if a.profile is not None else bytes(
        P.query(link, P.OP_GET_PROFILE, rid=a.rid, wait=a.wait))[1]
    base0, fn0 = read_both(link, profile, a.rid, a.wait)
    print("profile %d: FN matrix %s the base matrix" % (
        profile, "equals" if base0 == fn0 else "DIFFERS from"))
    if a.restore:
        old = json.load(open(a.restore))
        ob, of = bytes.fromhex(old["base_hex"]), bytes.fromhex(old["fn_hex"])
        plan = [(OP_SET_FN, i, slot_of(of, i)) for i in range(16) if slot_of(of, i) != slot_of(fn0, i)]
        plan += [(OP_SET_BASE, i, slot_of(ob, i)) for i in range(16) if slot_of(ob, i) != slot_of(base0, i)]
        if not plan:
            print("nothing differs; no writes needed")
            return 0
        for op, i, act in plan:
            print("0x%02X slot %2d -> %s   frame=%s" % (op, i, act, build_set(op, old["profile"], i, act)[:12].hex()))
        if not a.yes:
            print("dry run: add --yes to send")
            return 0
        for op, i, act in plan:
            send(link, build_set(op, old["profile"], i, act), a.rid, a.wait)
        b, f = read_both(link, old["profile"], a.rid, a.wait)
        ok = b == ob and f == of
        print("restore: %s" % ("OK" if ok else "NOT FULLY RESTORED - compare with the backup"))
        return 0 if ok else 1
    os.makedirs(a.backup_dir, exist_ok=True)
    name = os.path.join(os.path.normpath(a.backup_dir),
                        "fn-backup-%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    json.dump({"profile": profile, "base_hex": base0.hex(), "fn_hex": fn0.hex()}, open(name, "w"), indent=2)
    print("backup saved:", name)
    action = [int(x, 0) for x in a.action.split(",")]
    frame = build_set(OP_SET_FN, profile, a.slot, action)
    print("FN layer slot %d: %s -> %s   frame=%s" % (a.slot, slot_of(fn0, a.slot), action, frame[:12].hex()))
    if slot_of(fn0, a.slot) == action:
        sys.exit("the slot already holds that action; pick a different --action")
    if not a.yes:
        print("dry run: add --yes to send")
        return 0
    send(link, frame, a.rid, a.wait)
    base1, fn1 = read_both(link, profile, a.rid, a.wait)
    print("after write: FN slot %d = %s, base slot %d = %s" % (
        a.slot, slot_of(fn1, a.slot), a.slot, slot_of(base1, a.slot)))
    print("RESULT:", verdict(base0, fn0, base1, fn1, a.slot, action))
    if a.keep:
        print("--keep: left as is; restore with --restore %s --yes" % name)
        return 0
    if slot_of(fn1, a.slot) != slot_of(fn0, a.slot):
        send(link, build_set(OP_SET_FN, profile, a.slot, slot_of(fn0, a.slot)), a.rid, a.wait)
    if slot_of(read_both(link, profile, a.rid, a.wait)[0], a.slot) != slot_of(base0, a.slot):
        send(link, build_set(OP_SET_BASE, profile, a.slot, slot_of(base0, a.slot)), a.rid, a.wait)
    base2, fn2 = read_both(link, profile, a.rid, a.wait)
    ok = base2 == base0 and fn2 == fn0
    print("restore: %s" % ("OK, both matrices back to the original" if ok
                           else "NOT FULLY RESTORED - run --restore %s --yes" % name))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=P.auto_int, default=0x4026)
    ap.add_argument("--iface", type=int, default=2)
    ap.add_argument("--rid", type=int, default=0)
    ap.add_argument("--wait", type=float, default=0.02)
    ap.add_argument("--profile", type=int)
    ap.add_argument("--slot", type=int, default=14, help="slot to test (default 14, an unused tilt slot)")
    ap.add_argument("--action", default="0,0,34,0", help="four bytes (default: key '5')")
    ap.add_argument("--keep", action="store_true", help="do not restore automatically")
    ap.add_argument("--restore", metavar="FN-BACKUP.json")
    ap.add_argument("--backup-dir", default=CAPTURES, help="where backups are saved (default: repo captures/)")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--force-model", action="store_true")
    a = ap.parse_args()
    link = P.UsbLink(pid=a.pid, iface=a.iface)
    try:
        sys.exit(run(link, a))
    finally:
        link.close()


if __name__ == "__main__":
    main()
