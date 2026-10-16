"""mkm071: configure the LTC MKM071 mouse over its wired config channel.

Writes are dry runs unless --yes is given. Before any write a full backup is saved
(see --backup-dir), and every write is read back; a write that does not stick exits with status 1.
"""
import argparse
import json
import os
import sys
import time

from . import __version__
from . import actions as A
from . import commands as C
from .actions import format_action, parse_action, parse_slot, slot_label
from .backup import load_backup, readable_profiles, restore_plan, save_backup, slot_of
from .device import Mkm071, WriteNotApplied
from .frame import PID_DONGLE, PID_WIRED
from .macro import MACRO_COUNT, format_macro_events, macro_norm, macro_text_events, parse_macro_spec
from .model import KNOWN_IDS, ButtonAction, DpiBlock, Macro, Opt0
from .watch import find_event_hidraw, poll_wired, read_events

EXIT_OK, EXIT_NOT_APPLIED, EXIT_USAGE, EXIT_DEVICE = 0, 1, 2, 3

OPTIONS = {  # name: (Opt0 attribute, note from the hardware tests)
    "rate": ("report_rate_hz", "Hz: 125, 250, 500 or 1000; verified"),
    "debounce": ("debounce", "0-10; writes and reads back, effect not measurable"),
    "sleep_bt": ("sleep_bt", "Bluetooth light-sleep timer, 0-3600 (seconds assumed, unverified)"),
    "sleep_24": ("sleep_24", "2.4G light-sleep timer, 0-3600 (seconds assumed, unverified)"),
    "x_sens": ("x_sensitivity", "10-200, 100 = 1.0x; verified"),
    "y_sens": ("y_sensitivity", "10-200, 100 = 1.0x; consistent with 0.5x at 50"),
    "lift": ("lift_cutoff", "0-3; reads back, no observed effect"),
    "angle_snap": ("angle_snap", "0/1; reads back, no observed effect"),
    "charging": ("charging_switch", "0/1; the mouse ignores writes"),
}


class UsageError(Exception):
    pass


def default_backup_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "mkm071", "backups")


def open_device(a):
    """Opened per command; tests replace this."""
    return Mkm071.open(pid=a.pid, iface=a.iface, wait=a.wait, force_model=a.force_model)


def hexrgb(c):
    return "%06X" % c


def warn(msgs):
    for m in msgs:
        print("warning: " + m, file=sys.stderr)


def target_profile(dev, a, write=True):
    p = dev.active_profile() if a.profile is None else a.profile
    if write and not 0 <= p <= C.MAX_PROFILE:
        raise UsageError("profile %d is outside 0-%d; pick one with --profile N, or switch back with "
                         "`mkm071 --yes profile set 0`" % (p, C.MAX_PROFILE))
    return p


def commit(dev, a, lines, write):
    """Print the planned change; with --yes save a backup, run `write`, report the read-back."""
    for line in lines:
        print(line)
    if not a.yes:
        print("dry run: nothing written; add --yes to write")
        return EXIT_OK
    print("backup saved: " + save_backup(dev.backup(), a.backup_dir))
    try:
        write()
    except WriteNotApplied as e:
        print("NOT APPLIED: %s" % e)
        return EXIT_NOT_APPLIED
    print("written and read back: OK")
    return EXIT_OK


# ------------------------------------------------------------ read-only commands
def describe_options(o):
    return ("rate %s Hz, debounce %d, sleep BT %d/%d, 2.4G %d/%d, sensitivity %d/%d, lift %d, "
            "angle snap %d" % (o.report_rate_hz, o.debounce, o.sleep_bt, o.deep_sleep_bt, o.sleep_24,
                               o.deep_sleep_24, o.x_sensitivity, o.y_sensitivity, o.lift_cutoff,
                               o.angle_snap))


def dpi_line(blk):
    return "  ".join("%s%d:%d #%s" % ("*" if i == blk.current_stage else "", i, x, hexrgb(c))
                     for i, (x, _, c) in enumerate(blk.stages))


def macro_line(m):
    if m.empty:
        return "empty"
    spec = format_macro_events(m.events)
    if len(spec) > 70:
        spec = spec[:67] + "..."
    return "repeat %d, %d bytes: %s" % (m.repeat, len(macro_norm(m.to_bytes())), spec)


def state_dict(st):
    return {
        "model_id": st.model_id, "model": st.model_name, "active_profile": st.active_profile,
        "options": {k: getattr(st.opt0, attr) for k, (attr, _) in OPTIONS.items()},
        "light": {"type": st.opt0.light.type_name, "rgb": hexrgb(st.opt0.light.rgb)},
        "profiles": {str(i): {
            "rate_hz": p.rate_hz, "current_stage": p.dpi.current_stage,
            "stages": [{"dpi_x": x, "dpi_y": y, "color": hexrgb(c)} for x, y, c in p.dpi.stages],
            "buttons": {str(n): format_action(s) for n, s in enumerate(p.matrix.slots)},
            "matrix_hex": p.matrix.to_bytes().hex()} for i, p in st.profiles.items()},
        "macros": {str(i): {"repeat": m.repeat, "spec": format_macro_events(m.events)}
                   for i, m in st.macros.items() if not m.empty},
    }


def cmd_info(dev, a):
    p = dev.active_profile()
    blk = dev.read_dpi(p)
    o = dev.read_opt0()
    info = {"model_id": dev.model_id(), "model": KNOWN_IDS.get(dev.model_id(), "unknown"),
            "active_profile": p, "report_rate_hz": o.report_rate_hz,
            "dpi_stage": blk.current_stage, "dpi": blk.current_dpi}
    if a.json:
        print(json.dumps(info, indent=2))
    else:
        print("model %(model_id)d (%(model)s), profile %(active_profile)d, %(report_rate_hz)s Hz, "
              "DPI stage %(dpi_stage)d = %(dpi)s" % info)
    return EXIT_OK


def cmd_dump(dev, a):
    st = dev.read_all(macros=not a.no_macros)
    if a.json:
        print(json.dumps(state_dict(st), indent=2))
        return EXIT_OK
    print("model %d (%s), active profile %d" % (st.model_id, st.model_name, st.active_profile))
    print("options: " + describe_options(st.opt0))
    for i, p in st.profiles.items():
        print("\nprofile %d%s, %s Hz" % (i, " (active)" if i == st.active_profile else "", p.rate_hz))
        print("  dpi: " + dpi_line(p.dpi))
        for n, s in enumerate(p.matrix.slots):
            print("  %-24s %s" % (slot_label(n), format_action(s)))
    if st.macros:
        print("\nmacros:")
        for i, m in st.macros.items():
            if not m.empty:
                print("  %2d: %s" % (i, macro_line(m)))
    return EXIT_OK


def cmd_backup(dev, a):
    bk = dev.backup()
    if a.out:
        with open(a.out, "x") as f:
            json.dump(bk, f, indent=2)
        print("backup saved: " + a.out)
    else:
        print("backup saved: " + save_backup(bk, a.backup_dir))
    return EXIT_OK


# ------------------------------------------------------------ writes
def cmd_restore(dev, a):
    old = load_backup(a.file)
    cur = dev.backup()
    plan = restore_plan(old, cur, a.profile)
    if not plan:
        print("device already matches the backup; nothing to write")
        return EXIT_OK
    have = readable_profiles(cur)
    lines = []
    for p, slot, act in plan.slots:
        now = ButtonAction(bytes(slot_of(bytes.fromhex(have[p]["matrix_hex"]), slot)))
        lines.append("profile %d %s: %s -> %s" % (p, slot_label(slot), format_action(now),
                                                  format_action(ButtonAction(bytes(act)))))
    for p, fr in plan.opt1:
        now, want = DpiBlock.from_bytes(bytes.fromhex(have[p]["opt1_hex"])), DpiBlock.from_bytes(fr)
        lines.append("profile %d dpi: %s\n          -> %s" % (p, dpi_line(now), dpi_line(want)))
    if plan.opt0 is not None:
        now, want = Opt0.from_bytes(bytes.fromhex(cur["opt0_hex"])), Opt0.from_bytes(plan.opt0)
        lines.append("options: %s\n      -> %s" % (describe_options(now), describe_options(want)))
    cur_macros = {int(k): bytes.fromhex(v) for k, v in cur["macros"].items() if isinstance(v, str)}
    for i, buf in plan.macros:
        lines.append("macro %d: %s -> %s" % (i, macro_line(Macro.from_bytes(cur_macros[i])),
                                             macro_line(Macro.from_bytes(buf))))
    return commit(dev, a, lines, lambda: dev.restore(old, a.profile))


def cmd_profile(dev, a):
    now = dev.active_profile()
    if a.action == "get":
        print(now)
        return EXIT_OK
    C.build_set_profile(a.n)  # validates the range
    if a.n == now:
        print("profile %d is already active" % now)
        return EXIT_OK
    return commit(dev, a, ["active profile: %d -> %d" % (now, a.n)], lambda: dev.switch_profile(a.n))


def cmd_button(dev, a):
    p = target_profile(dev, a, write=a.action == "set")
    matrix = dev.read_matrix(p)
    if a.action == "list":
        for n, s in enumerate(matrix.slots):
            print("%-24s %-20s %s" % (slot_label(n), format_action(s), bytes(s).hex()))
        return EXIT_OK
    slot = parse_slot(a.slot)
    action, warnings = parse_action(a.action_text)
    warn(warnings)
    if slot == 0 and action != ButtonAction.mouse("left") and not a.force:
        raise UsageError("slot 0 is the left button; remapping it can leave you without a left click. "
                         "Add --force if you mean it")
    if matrix[slot] == action:
        print("profile %d %s is already %s" % (p, slot_label(slot), format_action(action)))
        return EXIT_OK
    line = "profile %d %s: %s -> %s   [%s -> %s]" % (p, slot_label(slot), format_action(matrix[slot]),
                                                    format_action(action), bytes(matrix[slot]).hex(),
                                                    bytes(action).hex())
    return commit(dev, a, [line], lambda: dev.set_slot(slot, action, profile=p))


def cmd_dpi(dev, a):
    p = target_profile(dev, a, write=a.action != "list")
    blk = dev.read_dpi(p)
    if a.action == "list":
        for i, (x, y, c) in enumerate(blk.stages):
            print("%s stage %d: %5d DPI%s  #%s" % ("*" if i == blk.current_stage else " ", i, x,
                                                 "" if x == y else " (Y %d)" % y, hexrgb(c)))
        return EXIT_OK
    raw = blk.to_bytes()
    if a.action == "set":
        frame = C.build_set_opt1(p, raw, a.stage, a.dpi)
        write = lambda: dev.set_dpi(a.stage, a.dpi, profile=p)  # noqa: E731
    elif a.action == "stage":
        frame = C.build_set_opt1(p, raw, active=a.stage)
        write = lambda: dev.set_stage(a.stage, profile=p)  # noqa: E731
    else:
        rgb = int(a.color.lstrip("#"), 16)
        frame = C.build_set_opt1(p, raw, color_stage=a.stage, rgb=rgb)
        write = lambda: dev.set_stage_color(a.stage, rgb, profile=p)  # noqa: E731
    new = DpiBlock.from_bytes(frame)
    if frame[8:] == raw[8:] and frame[2] == raw[2]:
        print("profile %d DPI block already has that value" % p)
        return EXIT_OK
    lines = ["profile %d dpi: %s" % (p, dpi_line(blk)), "          -> %s" % dpi_line(new)]
    return commit(dev, a, lines, write)


def opt0_change(dev, a, fields):
    cur = dev.read_opt0()
    frame = C.build_set_opt0(cur.to_bytes(), fields=fields)
    new = Opt0.from_bytes(frame)
    diffs = ["%s: %s -> %s" % (k, getattr(cur, attr), getattr(new, attr))
             for k, (attr, _) in OPTIONS.items() if getattr(cur, attr) != getattr(new, attr)]
    if not diffs:
        print("options already have those values")
        return EXIT_OK
    return commit(dev, a, diffs, lambda: dev.set_opt0(**fields))


def cmd_option(dev, a):
    if a.action == "list":
        o = dev.read_opt0()
        for k, (attr, note) in OPTIONS.items():
            print("%-10s %6s   %s" % (k, getattr(o, attr), note))
        print("%-10s %6s   wheel LED ignores it; use `mkm071 dpi color`" % ("light", o.light.type_name))
        return EXIT_OK
    if not a.assignments:
        raise UsageError("give FIELD=VALUE pairs, e.g. `mkm071 option set debounce=4 x_sens=100`")
    fields = C.parse_opt0_spec(",".join(a.assignments))
    if "charging" in fields:
        warn(["the charging switch (byte 60) ignored writes on hardware; expect NOT APPLIED"])
    return opt0_change(dev, a, fields)


def cmd_rate(dev, a):
    if a.action == "get":
        o = dev.read_opt0()
        print("%s Hz (option block 0)" % o.report_rate_hz)
        if a.per_profile:
            for p in range(C.MAX_PROFILE + 1):
                print("  profile %d: %s Hz (0x84)" % (p, dev.read_rate(p)))
        return EXIT_OK
    if a.per_profile:
        p = target_profile(dev, a)
        C.build_set_rate(p, a.hz)
        warn(["per-profile rate (opcode 0x04) is untested on hardware; the verified path is without "
              "--per-profile"])
        now = dev.read_rate(p)
        if now == a.hz:
            print("profile %d already at %d Hz" % (p, a.hz))
            return EXIT_OK
        return commit(dev, a, ["profile %d rate: %s -> %d Hz (0x04)" % (p, now, a.hz)],
                      lambda: dev.set_rate(a.hz, profile=p))
    return opt0_change(dev, a, C.check_opt0_fields({"rate": a.hz}))


def cmd_light(dev, a):
    spec = C.parse_light_spec(a.spec)
    warn(["the light record has no visible effect on the MKM071; the wheel LED shows the DPI stage "
          "colour (`mkm071 dpi color`)"])
    cur = dev.read_opt0()
    frame = C.build_set_opt0(cur.to_bytes(), spec)
    if frame[24:32] == cur.to_bytes()[24:32]:
        print("light record already has that value")
        return EXIT_OK
    line = "light record: %s -> %s" % (cur.light.to_bytes().hex(), frame[24:32].hex())
    return commit(dev, a, [line], lambda: dev.set_light(a.spec))


def cmd_macro(dev, a):
    if a.action == "list":
        for i in range(MACRO_COUNT):
            m = dev.read_macro(i)
            if not m.empty or a.all:
                print("%2d: %s" % (i, macro_line(m)))
        return EXIT_OK
    if not 0 <= a.index < MACRO_COUNT:
        raise UsageError("macro index must be 0-%d" % (MACRO_COUNT - 1))
    cur = dev.read_macro(a.index)
    if a.action == "show":
        if a.json:
            print(json.dumps({"repeat": cur.repeat, "events": cur.events}, indent=2))
        elif cur.empty:
            print("empty")
        else:
            print("repeat %d\n%s" % (cur.repeat, format_macro_events(cur.events)))
        return EXIT_OK
    if a.action == "clear":
        new = Macro(0, [])
    else:
        if bool(a.text) == bool(a.spec):
            raise UsageError("give exactly one of --text or --spec")
        ev = macro_text_events(a.text, a.delay) if a.text else parse_macro_spec(a.spec, a.delay)
        new = Macro(a.repeat, ev)
        new.to_bytes()  # validates the size before anything is printed as a plan
    if macro_norm(new.to_bytes()) == macro_norm(cur.to_bytes()):
        print("macro %d already holds that" % a.index)
        return EXIT_OK
    lines = ["macro %d: %s" % (a.index, macro_line(cur)), "      -> %s" % macro_line(new)]
    return commit(dev, a, lines, lambda: dev.write_macro(a.index, None if new.empty else new))


def cmd_watch(a):
    if a.dongle:
        node = a.node or find_event_hidraw()
        if not node:
            raise UsageError("no 3151:%04x hidraw node with vendor report 5; is the dongle plugged in?"
                             % PID_DONGLE)
        print("listening on %s (Ctrl+C to stop)" % node, flush=True)
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        try:
            for ev in read_events(fd, a.seconds):
                if ev.kind != "indicator" or a.verbose:
                    print("%s %s %d" % (time.strftime("%H:%M:%S"), ev.kind, ev.value), flush=True)
        finally:
            os.close(fd)
        return EXIT_OK
    dev = open_device(a)
    try:
        print("polling every %gs (Ctrl+C to stop)" % a.interval, flush=True)
        for kind, old, new in poll_wired(dev, a.interval, a.seconds):
            print("%s %s %d -> %d" % (time.strftime("%H:%M:%S"), kind, old, new), flush=True)
    finally:
        dev.close()
    return EXIT_OK


# ------------------------------------------------------------ argument parsing
def build_parser():
    ap = argparse.ArgumentParser(prog="mkm071", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-y", "--yes", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("-p", "--profile", type=int, metavar="N",
                    help="profile to read or edit (default: the active one); does not switch profiles")
    ap.add_argument("--backup-dir", default=default_backup_dir(),
                    help="where automatic backups go (default: %(default)s)")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=PID_WIRED, help=argparse.SUPPRESS)
    ap.add_argument("--iface", type=int, default=2, help=argparse.SUPPRESS)
    ap.add_argument("--wait", type=float, default=0.02, help="seconds between request and reply")
    ap.add_argument("--force-model", action="store_true", help="allow writes to a model other than 1833")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    s = sub.add_parser("info", help="model, active profile, rate and DPI")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("dump", help="everything: options, profiles 0-4, buttons, DPI, macros")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-macros", action="store_true", help="skip the 20 macro slots (faster)")
    s = sub.add_parser("backup", help="save a full backup")
    s.add_argument("--out", metavar="FILE", help="file to write (default: a new file in --backup-dir)")
    s = sub.add_parser("restore", help="write back everything that differs from a backup")
    s.add_argument("file")

    s = sub.add_parser("profile", help="get or switch the active profile")
    ps = s.add_subparsers(dest="action", required=True)
    ps.add_parser("get")
    ps.add_parser("set").add_argument("n", type=int, metavar="N", help="0-%d" % C.MAX_PROFILE)

    s = sub.add_parser("button", help="list or remap buttons",
                       epilog=A.__doc__,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    bs = s.add_subparsers(dest="action", required=True)
    bs.add_parser("list")
    b = bs.add_parser("set", help="set SLOT (number or name, e.g. thumb_1) to ACTION")
    b.add_argument("slot")
    b.add_argument("action_text", metavar="action")
    b.add_argument("--force", action="store_true", help="allow remapping the left button")

    s = sub.add_parser("dpi", help="list or change DPI stages")
    ds = s.add_subparsers(dest="action", required=True)
    ds.add_parser("list")
    d = ds.add_parser("set", help="set STAGE (0-based) to DPI (50-%d, step 50)" % C.MAX_DPI)
    d.add_argument("stage", type=int)
    d.add_argument("dpi", type=int)
    ds.add_parser("stage", help="make STAGE active").add_argument("stage", type=int)
    d = ds.add_parser("color", help="wheel-LED colour of STAGE, RRGGBB")
    d.add_argument("stage", type=int)
    d.add_argument("color")

    s = sub.add_parser("rate", help="report rate")
    rs = s.add_subparsers(dest="action", required=True)
    rs.add_parser("get").add_argument("--per-profile", action="store_true")
    r = rs.add_parser("set")
    r.add_argument("hz", type=int, choices=sorted(C.RATE_TO_CODE))
    r.add_argument("--per-profile", action="store_true", help="use opcode 0x04 for --profile (untested)")

    s = sub.add_parser("option", help="debounce, sleep, sensitivity, lift-off, angle snap")
    os_ = s.add_subparsers(dest="action", required=True)
    os_.add_parser("list")
    os_.add_parser("set").add_argument("assignments", nargs="*", metavar="FIELD=VALUE")

    s = sub.add_parser("light", help="light record (no visible effect on the MKM071)")
    s.add_argument("action", choices=["set"])
    s.add_argument("spec", help="MODE[,RRGGBB[,BRIGHTNESS 0-4[,SPEED 0-4]]]")

    s = sub.add_parser("macro", help="list, show, write or clear macros", epilog=(
        "spec tokens: tap:KEY down:KEY up:KEY delay:MS click:BUTTON mdown:BUTTON mup:BUTTON move:DX:DY "
        "text:STRING (comma separated). Assign a macro with `mkm071 button set SLOT macro:N[:toggle|hold]`."))
    ms = s.add_subparsers(dest="action", required=True)
    ms.add_parser("list").add_argument("--all", action="store_true", help="include empty slots")
    m = ms.add_parser("show")
    m.add_argument("index", type=int)
    m.add_argument("--json", action="store_true")
    m = ms.add_parser("write")
    m.add_argument("index", type=int)
    m.add_argument("--text", help="type this text (letters, digits, space, punctuation keys)")
    m.add_argument("--spec", help="event list, see below")
    m.add_argument("--repeat", type=int, default=1)
    m.add_argument("--delay", type=int, default=10, help="ms used by text, tap and click (default 10)")
    ms.add_parser("clear").add_argument("index", type=int)

    s = sub.add_parser("watch", help="print profile and DPI-stage changes as they happen")
    s.add_argument("--dongle", action="store_true", help="listen on the 2.4G dongle (no commands sent)")
    s.add_argument("--node", help="hidraw node for --dongle (default: found in sysfs)")
    s.add_argument("--interval", type=float, default=0.1, help="wired polling interval")
    s.add_argument("--seconds", type=float, default=0, help="stop after this long (default: never)")
    s.add_argument("-v", "--verbose", action="store_true", help="also print indicator events")
    return ap


COMMANDS = {"info": cmd_info, "dump": cmd_dump, "backup": cmd_backup, "restore": cmd_restore,
            "profile": cmd_profile, "button": cmd_button, "dpi": cmd_dpi, "rate": cmd_rate,
            "option": cmd_option, "light": cmd_light, "macro": cmd_macro}


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.cmd is None:
        ap.print_help()
        return EXIT_USAGE
    try:
        if a.cmd == "watch":
            return cmd_watch(a)
        try:
            dev = open_device(a)
        except ImportError:
            print("error: pyusb is not installed (pip install pyusb)", file=sys.stderr)
            return EXIT_DEVICE
        except Exception as e:  # not found, no permission, wrong model
            print("error: %s\n(is the mouse connected by cable? without root, install "
                  "udev/70-mkm071.rules)" % e, file=sys.stderr)
            return EXIT_DEVICE
        try:
            return COMMANDS[a.cmd](dev, a)
        finally:
            dev.close()
    except (UsageError, ValueError, FileExistsError, FileNotFoundError) as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_USAGE
    except OSError as e:  # the device stopped answering, or a read failed
        print("error: %s" % e, file=sys.stderr)
        return EXIT_DEVICE
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
