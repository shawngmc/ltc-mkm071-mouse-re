#!/usr/bin/env python3
"""MKM071 gated writer (companion to mkm071_probe.py, wired mode).

The frame builders, macro encoder and backup/restore live in the mkm071 package (src/mkm071);
this script is their command line.

Only three write opcodes are ever sent: 0x50 (set one button-matrix slot), 0x54 (DPI
option block) and 0x53 (option block 0, used only for the light setting here).
Both option-block writes are read-modify-write: the whole block is read, one field is
changed, everything else is echoed back. No firmware, bootloader or reset commands exist here.

Safety flow for every write:
  1. Verify model id (0x8F) is 1833 (MKM071) unless --force-model.
  2. Save a full backup JSON (matrix, option blocks, profile) before touching anything.
  3. Dry run by default; nothing is sent without --yes.
  4. After writing, re-read the matrix and report whether the slot really changed.
  5. --restore BACKUP.json (saved in captures/, see --backup-dir) writes back every slot that differs from the backup.

Usage (mouse wired, same venv as the probe):
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --backup-only
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --set-slot 7 --action 0,0,34,0          # dry run
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --set-slot 7 --action 0,0,34,0 --yes
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --restore captures/backup-XXXX.json --yes
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --set-dpi 0,1000 --yes     # stage 0 -> 1000 DPI
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --set-stage-color 0,FF8000 --yes   # DPI stage colour
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --set-stage 2 --yes                 # active stage
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --profile 2 --set-slot 7 --action 0,0,34,0 --yes   # edit profile 2 without switching
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --restore captures/backup-XXXX.json --yes   # all five profiles + option block 0
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --set-opt0 rate=500,debounce=4 --yes
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --profile 0 --set-report-rate 500 --yes
  sudo .venv/bin/python tools/mkm071_write.py --pid 0x4026 --set-light always,FF0000,4 --yes
Light spec: MODE[,RRGGBB[,BRIGHTNESS 0-4[,SPEED 0-4]]]; MODE = off|always|breath|neon|wave|dazzle|laser.
Action bytes are [type, 0, code, arg]; e.g. 0,0,34,0 = keyboard key '5' (HID 0x22).
"""
import argparse
import os
import sys
import time

import mkm071_probe as P  # also puts ../src on sys.path
from mkm071.backup import load_backup, make_backup, read_matrix, restore_plan, save_backup, slot_of  # noqa: F401
from mkm071.commands import (LIGHT_MODES, MAX_DPI, MAX_PROFILE, MAXSPEED, OPT0_FIELDS,  # noqa: F401
                             RATE_TO_CODE, build_set_opt0, build_set_opt1, build_set_profile,
                             build_set_rate, build_set_slot, parse_light_spec, parse_opt0_spec)
from mkm071.frame import (MKM071_ID, OP_GET_PROFILE, OP_SET_MACRO, OP_SET_OPT0,  # noqa: F401
                          OP_SET_OPT1, OP_SET_PROFILE, OP_SET_REPORT, OP_SET_SLOT)
from mkm071.macro import (MACRO_BUTTONS, MACRO_COUNT, MACRO_LIMIT, MACRO_PAGE,  # noqa: F401
                          MACRO_SIZE, build_macro_buffer, hid_usage, macro_frames, macro_is_empty,
                          macro_norm, macro_text_events, parse_macro_spec)
from mkm071.transport import send_command

CAPTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captures")


def switch_profile(link, n, rid, wait):
    send_command(link, build_set_profile(n), rid, wait)
    return P.query(link, OP_GET_PROFILE, rid=rid, wait=wait)[1]


def write_opt0(link, frame, rid, wait):
    send_command(link, frame, rid, wait)
    return bytes(P.query(link, P.OP_GET_OPT0, rid=rid, wait=wait))


def write_opt1(link, frame, profile, rid, wait):
    send_command(link, frame, rid, wait)
    return bytes(P.query(link, P.OP_GET_OPT1, profile, rid=rid, wait=wait))


def write_macro(link, idx, buf, rid, wait):
    for fr in macro_frames(idx, buf):
        send_command(link, fr, rid, wait)
    time.sleep(0.1)
    return P.read_macro(link, idx, rid=rid, wait=wait)


def write_slot(link, profile, slot, action, rid, wait):
    send_command(link, build_set_slot(profile, slot, action), rid, wait)
    return read_matrix(link, profile, rid, wait)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=P.auto_int, default=0x4026)
    ap.add_argument("--iface", type=int, default=2)
    ap.add_argument("--rid", type=int, default=0)
    ap.add_argument("--wait", type=float, default=0.02)
    ap.add_argument("--backup-only", action="store_true")
    ap.add_argument("--profile", type=int, metavar="N",
                    help="profile to edit (default: the active one); no switching happens")
    ap.add_argument("--set-slot", type=int)
    ap.add_argument("--action", help="four comma-separated bytes, e.g. 0,0,34,0")
    ap.add_argument("--set-dpi", metavar="STAGE,DPI", help="e.g. 0,1000")
    ap.add_argument("--set-stage-color", metavar="STAGE,RRGGBB", help="e.g. 0,FF8000")
    ap.add_argument("--set-stage", type=int, metavar="N", help="make stage N active")
    ap.add_argument("--set-profile", type=int, metavar="N", help="switch the active profile (0-4)")
    ap.add_argument("--set-opt0", metavar="SPEC",
                    help="e.g. rate=500,debounce=4,sleep=60,x_sens=100,lift=1,angle_snap=0")
    ap.add_argument("--set-report-rate", type=int, metavar="HZ",
                    help="per-profile report rate via opcode 0x04 (use --profile N)")
    ap.add_argument("--set-macro", type=int, metavar="IDX", help="write macro slot IDX (0-19)")
    ap.add_argument("--macro-text", metavar="TEXT", help="type TEXT (letters, digits, space)")
    ap.add_argument("--macro-spec", metavar="SPEC",
                    help="e.g. tap:h,delay:50,click:left,move:10:0,text:ab (see parse_macro_spec)")
    ap.add_argument("--macro-repeat", type=int, default=1)
    ap.add_argument("--macro-delay", type=int, default=10, help="delay value used by text/tap/click")
    ap.add_argument("--clear-macro", type=int, metavar="IDX", help="write an empty macro to slot IDX")
    ap.add_argument("--set-light", metavar="SPEC", help="e.g. always,FF0000,4")
    ap.add_argument("--restore", metavar="BACKUP.json")
    ap.add_argument("--backup-dir", default=CAPTURES, help="where backups are saved (default: repo captures/)")
    ap.add_argument("--yes", action="store_true", help="actually send writes")
    ap.add_argument("--force-model", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return P.selftest()

    link = P.UsbLink(pid=a.pid, iface=a.iface)
    try:
        bk = make_backup(link, a.rid, a.wait)
        if bk["model_id"] != MKM071_ID and not a.force_model:
            sys.exit("model id %d is not MKM071 (%d); refusing (use --force-model)"
                     % (bk["model_id"], MKM071_ID))
        print("backup saved:", save_backup(bk, a.backup_dir))
        if a.backup_only:
            return
        target = a.profile if a.profile is not None else bk["profile"]
        if a.set_profile is None and not a.restore and not 0 <= target <= MAX_PROFILE:
            sys.exit("the mouse reports active profile %d, outside 0..%d; use --profile N to "
                     "pick one, or --set-profile N to switch to a valid profile first"
                     % (target, MAX_PROFILE))
        state = {int(p): d for p, d in bk["profiles"].items() if "error" not in d}
        slot_plan = []   # (profile, slot, action)
        opt1_plan = []   # (profile, frame)
        opt0_frame = None
        macro_plan = []  # (idx, buffer)
        cur_opt0 = bytes.fromhex(bk["opt0_hex"])
        cur_macros = {int(k): bytes.fromhex(v) for k, v in bk.get("macros", {}).items()
                      if isinstance(v, str)}

        def need(p):
            if p not in state:
                sys.exit("could not read profile %d from the mouse" % p)
            return (bytes.fromhex(state[p]["matrix_hex"]),
                    bytes.fromhex(state[p]["opt1_hex"]))

        if a.restore:
            try:
                plan = restore_plan(load_backup(a.restore), bk, a.profile)
            except (ValueError, IOError) as e:
                sys.exit(str(e))
            slot_plan, opt1_plan, opt0_frame, macro_plan = plan.slots, plan.opt1, plan.opt0, plan.macros
        elif a.set_slot is not None:
            if not a.action:
                sys.exit("--action required")
            slot_plan = [(target, a.set_slot, [int(x, 0) for x in a.action.split(",")])]
        elif a.set_dpi:
            st, val = [int(x, 0) for x in a.set_dpi.split(",")]
            opt1_plan = [(target, build_set_opt1(target, need(target)[1], st, val))]
        elif a.set_stage_color:
            st, col = a.set_stage_color.split(",")
            opt1_plan = [(target, build_set_opt1(target, need(target)[1],
                                                 color_stage=int(st, 0), rgb=int(col, 16)))]
        elif a.set_stage is not None:
            opt1_plan = [(target, build_set_opt1(target, need(target)[1], active=a.set_stage))]
        elif a.set_profile is not None:
            build_set_profile(a.set_profile)  # validates
            print("profile: %d -> %d   frame=%s" % (
                bk["profile"], a.set_profile, build_set_profile(a.set_profile)[:8].hex()))
            if not a.yes:
                print("dry run: add --yes to send")
                return
            now = switch_profile(link, a.set_profile, a.rid, a.wait)
            print("active profile now %d: %s" % (
                now, "OK" if now == a.set_profile else "NOT APPLIED"))
            return
        elif a.set_report_rate is not None:
            fr = build_set_rate(target, a.set_report_rate)
            have = P.query(link, P.OP_GET_REPORT, target, rid=a.rid, wait=a.wait)[2]
            print("profile %d report rate code %d -> %d   frame=%s" % (
                target, have, fr[2], fr[:8].hex()))
            if not a.yes:
                print("dry run: add --yes to send")
                return
            send_command(link, fr, a.rid, a.wait)
            now = P.query(link, P.OP_GET_REPORT, target, rid=a.rid, wait=a.wait)[2]
            print("profile %d report rate code now %d: %s" % (
                target, now, "OK" if now == fr[2] else "NOT APPLIED"))
            return
        elif a.set_macro is not None or a.clear_macro is not None:
            if a.clear_macro is not None:
                idx, buf = a.clear_macro, bytes(MACRO_SIZE)
            else:
                idx = a.set_macro
                if bool(a.macro_text) == bool(a.macro_spec):
                    sys.exit("give exactly one of --macro-text / --macro-spec")
                ev = (macro_text_events(a.macro_text, a.macro_delay) if a.macro_text
                      else parse_macro_spec(a.macro_spec, a.macro_delay))
                buf = build_macro_buffer(a.macro_repeat, ev)
            have = cur_macros.get(idx)
            if have is None:
                sys.exit("could not read macro %d from the mouse" % idx)
            if macro_norm(have) != macro_norm(buf):
                macro_plan = [(idx, buf)]
        elif a.set_opt0:
            opt0_frame = build_set_opt0(cur_opt0, fields=parse_opt0_spec(a.set_opt0))
        elif a.set_light:
            opt0_frame = build_set_opt0(cur_opt0, parse_light_spec(a.set_light))
        else:
            sys.exit("nothing to do: use --backup-only, --set-slot, --set-dpi, "
                     "--set-stage-color, --set-stage, --set-profile, --set-opt0, "
                     "--set-report-rate, --set-macro, --clear-macro, --set-light or --restore")

        if not slot_plan and not opt1_plan and opt0_frame is None and not macro_plan:
            print("nothing differs; no writes needed")
            return
        for p, slot, act in slot_plan:
            print("profile %d slot %2d: %s -> %s   frame=%s" % (
                p, slot, slot_of(need(p)[0], slot), act,
                build_set_slot(p, slot, act)[:12].hex()))
        for p, fr in opt1_plan:
            cur = need(p)[1]
            before, after_p = P.parse_opt1(cur), P.parse_opt1(fr)
            print("profile %d dpi_x: %s -> %s\n  stage: %s -> %s   frame=%s" % (
                p, before["dpi_x"][:before["stage_count"]],
                after_p["dpi_x"][:after_p["stage_count"]],
                before["current_stage"], after_p["current_stage"], fr[:12].hex()))
            print("  stage colours: %s -> %s" % (
                ["%06X" % int.from_bytes(cur[40 + 3 * i:43 + 3 * i], "big")
                 for i in range(before["stage_count"])],
                ["%06X" % int.from_bytes(fr[40 + 3 * i:43 + 3 * i], "big")
                 for i in range(before["stage_count"])]))
        if opt0_frame is not None:
            diffs = ["byte %d: %d -> %d" % (i, cur_opt0[i], opt0_frame[i])
                     for i in range(8, 64) if cur_opt0[i] != opt0_frame[i]]
            print("option block 0 changes: %s   frame=%s" % (
                "; ".join(diffs) or "none", opt0_frame[:12].hex()))
        for idx, buf in macro_plan:
            have = cur_macros.get(idx, b"")
            state = "empty" if macro_is_empty(have) else "in use"
            if any(buf):
                dec = P.parse_macro(buf)
                print("macro %d (%s now): repeat %d, %d bytes, events: %s" % (
                    idx, state, dec["repeat"], dec["used_bytes"],
                    " ".join(("%s%s" % (e.get("action", e["type"])[:1], e.get("usage", e.get("code", "")))
                              if e["type"] != "delay" else "%dms" % e["value"])
                             if e["type"] != "mouse_move" else "move(%d,%d)" % (e["dx"], e["dy"])
                             for e in dec["events"])))
            else:
                print("macro %d (%s now): -> empty" % (idx, state))
            for fr in macro_frames(idx, buf):
                print("  frame page %d total_len %d last %d  %s..." % (fr[2], fr[3], fr[4], fr[:16].hex()))
        if not a.yes:
            print("dry run: add --yes to send")
            return
        for idx, buf in macro_plan:
            rb = write_macro(link, idx, buf, a.rid, a.wait)
            ok = macro_norm(rb) == macro_norm(buf)
            print("macro %d now %s: %s" % (idx, macro_norm(rb).hex()[:60] or "(empty)",
                                         "OK" if ok else "NOT APPLIED"))
        for p, slot, act in slot_plan:
            after = write_slot(link, p, slot, act, a.rid, a.wait)
            ok = slot_of(after, slot) == act
            print("profile %d slot %2d now %s: %s" % (p, slot, slot_of(after, slot),
                                                      "OK" if ok else "NOT APPLIED"))
        for p, fr in opt1_plan:
            rb = write_opt1(link, fr, p, a.rid, a.wait)
            same = (rb[8:64] == fr[8:64] and rb[2] == fr[2])
            print("profile %d dpi_x now %s, colours/Y table %s: %s" % (
                p, P.parse_opt1(rb)["dpi_x"][:rb[3]], "kept" if same else "CHANGED",
                "OK" if same else "NOT APPLIED"))
        if opt0_frame is not None:
            rb = write_opt0(link, opt0_frame, a.rid, a.wait)
            ok = rb[8:64] == opt0_frame[8:64]
            parsed = P.parse_opt0(rb)
            print("option block 0 now: rate %s Hz, debounce %s, sleep %s, sens %s/%s, lift %s,"
                  " angle_snap %s, charging %s, light %s: %s" % (
                      parsed["report_rate_hz"], parsed["debounce"], parsed["sleep_s"],
                      parsed["x_sensitivity"], parsed["y_sensitivity"], parsed["lift_cutoff"],
                      parsed["angle_snap"], parsed["charging_switch"], parsed["light"]["type"],
                      "OK" if ok else "NOT APPLIED"))
    finally:
        link.close()


if __name__ == "__main__":
    main()
