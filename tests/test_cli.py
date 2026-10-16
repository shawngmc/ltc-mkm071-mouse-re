"""`mkm071` end to end against the simulated mouse."""
import json

import pytest

from fakedevice import FACTORY_MATRIX, FakeMouse
from mkm071 import Mkm071, cli

WRITE_OPS = {0x04, 0x05, 0x16, 0x50, 0x51, 0x53, 0x54}


@pytest.fixture
def mouse(monkeypatch):
    m = FakeMouse()

    def open_device(a):
        d = Mkm071(m, wait=0)
        d.check_model(force=a.force_model)
        return d
    monkeypatch.setattr(cli, "open_device", open_device)
    return m


@pytest.fixture
def run(mouse, capsys, tmp_path):
    def run(*args):
        code = cli.main(["--backup-dir", str(tmp_path / "bk")] + list(args))
        out, err = capsys.readouterr()
        return code, out, err
    run.backups = lambda: sorted((tmp_path / "bk").glob("*.json")) if (tmp_path / "bk").exists() else []
    return run


def writes(m):
    return [f for f in m.sent if f[0] in WRITE_OPS]


# ------------------------------------------------------------ read-only
def test_info(run, mouse):
    code, out, _ = run("info")
    assert code == 0 and out.strip() == "model 1833 (MKM071 (g29_yj01z_3104)), profile 0, 1000 Hz, DPI stage 4 = 9600"
    info = json.loads(run("info", "--json")[1])
    assert info["dpi"] == 9600 and not writes(mouse)


def test_dump(run, mouse):
    code, out, _ = run("dump")
    assert code == 0 and "profile 0 (active), 1000 Hz" in out and "*4:9600 #0000FF" in out
    assert "%-24s %s" % ("thumb_1 (slot 7)", "key:1") in out and "%-24s %s" % ("right (slot 9)", "right") in out
    assert " 0: repeat 1, 10 bytes: down:h,delay:10,up:h" in out
    d = json.loads(run("dump", "--json")[1])
    assert d["profiles"]["2"]["matrix_hex"] == FACTORY_MATRIX.hex() and d["macros"]["0"]["repeat"] == 1
    assert d["options"]["x_sens"] == 100 and d["profiles"]["0"]["buttons"]["3"] == "dpi:cycle"
    assert not writes(mouse)


def test_lists(run):
    assert "%-24s %-20s %s" % ("dpi_button (slot 3)", "dpi:cycle", "14000000") in run("button", "list")[1]
    assert "* stage 4:  9600 DPI  #0000FF" in run("dpi", "list")[1]
    assert "lift            0   0-3; reads back, no observed effect" in run("option", "list")[1]
    assert run("macro", "list")[1].strip() == "0: repeat 1, 10 bytes: down:h,delay:10,up:h,delay:10,down:i,delay:10,up:i,delay:10"
    assert run("macro", "list", "--all")[1].count("empty") == 19
    assert run("macro", "show", "0")[1].startswith("repeat 1\ndown:h,delay:10")
    assert run("profile", "get")[1] == "0\n"
    assert run("rate", "get", "--per-profile")[1].count("1000 Hz") == 6


# ------------------------------------------------------------ dry run and --yes
@pytest.mark.parametrize("args,check", [
    (["button", "set", "thumb_1", "key:5"], lambda m: m.matrix[0][28:32].hex() == "00002200"),
    (["-p", "2", "button", "set", "7", "macro:3:hold"], lambda m: m.matrix[2][28:32].hex() == "09020300"),
    (["dpi", "set", "0", "1000"], lambda m: m.opt1[0][8:10].hex() == "e803" == m.opt1[0][24:26].hex()),
    (["dpi", "stage", "2"], lambda m: m.opt1[0][2] == 2),
    (["dpi", "color", "0", "#ff8000"], lambda m: m.opt1[0][40:43].hex() == "ff8000"),
    (["profile", "set", "3"], lambda m: m.profile == 3),
    (["rate", "set", "125"], lambda m: m.opt0[9] == 8),
    (["-p", "1", "rate", "set", "500", "--per-profile"], lambda m: m.rate[1] == 2),
    (["option", "set", "debounce=4", "sleep=60"], lambda m: (m.opt0[10], m.opt0[40], m.opt0[44]) == (4, 60, 60)),
    (["light", "set", "always,FF0000,4"], lambda m: m.opt0[24:32].hex() == "01040407ff000000"),
    (["macro", "write", "4", "--text", "Hi there"], lambda m: m.macros[4][:4].hex() == "0100e18a"),
    (["macro", "write", "5", "--spec", "click:left,delay:200,move:10:0", "--repeat", "3"],
     lambda m: m.macros[5][:2].hex() == "0300"),
    (["macro", "clear", "0"], lambda m: m.macros[0][:56] == bytes(56)),
])
def test_dry_run_then_write(run, mouse, args, check):
    code, out, _ = run(*args)
    assert code == 0 and "dry run: nothing written" in out and "->" in out
    assert not writes(mouse) and not run.backups()
    code, out, _ = run("--yes", *args)
    assert code == 0 and "written and read back: OK" in out, out
    assert check(mouse) and len(run.backups()) == 1
    code, out, _ = run("--yes", *args)                     # second time: nothing to do
    assert code == 0 and "already" in out and len(run.backups()) == 1


def test_warnings_go_to_stderr(run):
    code, out, err = run("button", "set", "thumb_2", "profile:next")
    assert code == 0 and "does not wrap" in err and "profile:next" in out
    assert "not verified on hardware" in run("button", "set", "thumb_2", "key:ctrl+c")[2]


def test_not_applied_exit_code(run, mouse):
    code, out, err = run("--yes", "option", "set", "charging=1")
    assert code == cli.EXIT_NOT_APPLIED and "NOT APPLIED" in out and "ignored writes" in err


def test_usage_errors(run, mouse):
    for args in (["button", "set", "thumb_9", "left"], ["button", "set", "7", "wiggle"],
                 ["dpi", "set", "0", "1025"], ["dpi", "set", "6", "1000"], ["option", "set", "debounce=11"],
                 ["option", "set"], ["macro", "write", "3"], ["macro", "write", "20", "--text", "x"],
                 ["macro", "write", "3", "--text", "a" * 60], ["profile", "set", "5"],
                 ["light", "set", "sparkle"], ["restore", "/nonexistent.json"]):
        code, _, err = run("--yes", *args)
        assert code == cli.EXIT_USAGE and err.startswith("error: "), args
    assert not writes(mouse)


def test_left_button_guard(run, mouse):
    code, _, err = run("--yes", "button", "set", "left", "right")
    assert code == cli.EXIT_USAGE and "--force" in err and not writes(mouse)
    assert run("--yes", "button", "set", "left", "right", "--force")[0] == 0


def test_stranded_profile(run, mouse):
    mouse.profile = 5
    code, _, err = run("--yes", "button", "set", "thumb_1", "key:5")
    assert code == cli.EXIT_USAGE and "profile set 0" in err
    assert run("button", "list")[0] == 0                    # reading profile 5 is fine
    assert run("--yes", "profile", "set", "0")[0] == 0 and mouse.profile == 0


def test_model_mismatch(run, mouse, capsys):
    mouse.model_id = 1847
    code, _, err = run("info")
    assert code == cli.EXIT_DEVICE and "not MKM071" in err


def test_backup_and_restore(run, mouse, tmp_path):
    path = str(tmp_path / "before.json")
    assert run("backup", "--out", path) == (0, "backup saved: %s\n" % path, "")
    assert run("backup", "--out", path)[0] == cli.EXIT_USAGE          # never overwrites
    run("--yes", "-p", "3", "button", "set", "thumb_4", "dpi:cycle")
    run("--yes", "dpi", "set", "5", "8000")
    run("--yes", "macro", "write", "2", "--text", "gg")
    run("--yes", "option", "set", "x_sens=150")
    code, out, _ = run("restore", path)
    assert code == 0 and "profile 3 thumb_4 (slot 11): dpi:cycle -> key:4" in out
    assert "12000" in out and "macro 2: repeat 1" in out and "dry run" in out
    code, out, _ = run("--yes", "restore", path)
    assert code == 0 and "OK" in out
    assert "already matches" in run("restore", path)[1]
    assert mouse.matrix[3][44:48].hex() == "00002100" and mouse.opt0[50] == 100


def test_restore_one_profile(run, mouse, tmp_path):
    path = str(tmp_path / "before.json")
    run("backup", "--out", path)
    run("--yes", "-p", "1", "button", "set", "thumb_1", "key:9")
    run("--yes", "-p", "2", "button", "set", "thumb_1", "key:9")
    run("--yes", "-p", "2", "restore", path)
    assert mouse.matrix[1][28:32].hex() == "00002600" and mouse.matrix[2][28:32].hex() == "00001e00"


def test_watch_wired(run, mouse, monkeypatch):
    import mkm071.watch as W

    def fake_poll(dev, interval, seconds):
        yield "dpi_stage", 4, 5
        yield "profile", 0, 1
    monkeypatch.setattr(cli, "poll_wired", fake_poll)
    code, out, _ = run("watch", "--seconds", "1")
    assert code == 0 and "dpi_stage 4 -> 5" in out and "profile 0 -> 1" in out
    assert W.poll_wired is not fake_poll


def test_watch_dongle_without_node(run, monkeypatch):
    monkeypatch.setattr(cli, "find_event_hidraw", lambda: None)
    code, _, err = run("watch", "--dongle", "--seconds", "1")
    assert code == cli.EXIT_USAGE and "dongle" in err


def test_help_lists_action_syntax(capsys):
    with pytest.raises(SystemExit):
        cli.main(["button", "--help"])
    out = capsys.readouterr().out
    assert "macro:3:toggle" in out and "profile:next" in out


def test_no_device(monkeypatch, capsys):
    def boom(a):
        raise IOError("no USB device 3151:4026 found")
    monkeypatch.setattr(cli, "open_device", boom)
    assert cli.main(["info"]) == cli.EXIT_DEVICE
    assert "udev/70-mkm071.rules" in capsys.readouterr().err
