"""tools/mkm071_probe.py and tools/mkm071_write.py run end to end against the simulated mouse."""
import json
import os
import sys

import pytest

from fakedevice import FakeMouse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import mkm071_probe as P  # noqa: E402
import mkm071_write as W  # noqa: E402

# Names the other scripts in tools/ use through `import mkm071_probe as P`
USED_BY_TOOLS = ["auto_int", "build_frame", "checksum7", "OP_GET_FNMATRIX", "OP_GET_MATRIX", "OP_GET_OPT0",
                 "OP_GET_OPT1", "OP_GET_PROFILE", "OP_GET_REPORT", "OP_GET_REV", "parse_macro", "parse_opt0",
                 "parse_opt1", "query", "read_macro", "REPORT_LEN", "UsbLink", "parse_report_descriptor"]


def test_probe_reexports():
    missing = [n for n in USED_BY_TOOLS if not hasattr(P, n)]
    assert not missing


@pytest.fixture
def mouse(monkeypatch):
    m = FakeMouse()
    monkeypatch.setattr(P, "UsbLink", lambda **kw: m)
    monkeypatch.setattr(P, "pick_feature_interface", lambda: (2, 0))
    return m


def run(monkeypatch, capsys, main, argv):
    monkeypatch.setattr(sys, "argv", ["x"] + argv)
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            return "EXIT %s" % e.code
    return capsys.readouterr().out


def test_probe_dump(mouse, monkeypatch, capsys):
    import runpy
    monkeypatch.setattr("mkm071.transport.UsbLink", lambda **kw: mouse)   # run_path re-imports
    out = run(monkeypatch, capsys, lambda: runpy.run_path(P.__file__, run_name="__main__"),
              ["--dump", "--usb", "--profiles", "--macros", "3", "--fn", "--battery", "--wait", "0"])
    info = json.loads(out)
    assert info["model_id"] == 1833 and info["profile"] == 0 and "errors" not in info
    assert info["opt1"]["dpi_x"][:6] == [800, 2400, 3200, 6400, 9600, 12000]
    assert info["matrix"][9]["meaning"] == "mouse right" and info["fn_matrix"] == info["matrix"]
    assert info["macros"]["0"]["repeat"] == 1 and info["macros"]["1"] == {"erased_flash": True}
    assert info["identical_to"]["0"] == [1, 2, 3, 4]
    assert info["battery_raw_hex"].startswith("82")
    assert {f[0] for f in mouse.sent} <= {0x8F, 0x85, 0xD3, 0xD4, 0xD0, 0xD1, 0x84, 0x96, 0x82}


def test_writer_dry_run_sends_no_writes(mouse, monkeypatch, capsys, tmp_path):
    out = run(monkeypatch, capsys, W.main, ["--wait", "0", "--backup-dir", str(tmp_path),
                                            "--set-slot", "7", "--action", "0,0,34,0"])
    assert "dry run" in out and "frame=50000700000000a800002200" in out
    assert {f[0] for f in mouse.sent} <= {0x8F, 0x85, 0xD0, 0xD3, 0xD4, 0x96}
    assert len(list(tmp_path.glob("backup-*.json"))) == 1


@pytest.mark.parametrize("argv,expect", [
    (["--set-slot", "7", "--action", "0,0,34,0"], "profile 0 slot  7 now [0, 0, 34, 0]: OK"),
    (["--profile", "2", "--set-dpi", "0,1000"], "profile 2 dpi_x now [1000, 2400, 3200, 6400, 9600, 12000]"),
    (["--set-stage-color", "0,FF8000"], "-> ['FF8000', 'FF8000',"),
    (["--set-stage", "2"], "OK"),
    (["--set-profile", "2"], "active profile now 2: OK"),
    (["--set-opt0", "rate=500,debounce=4"], "rate 500 Hz, debounce 4"),
    (["--set-opt0", "charging=1"], "NOT APPLIED"),                     # byte 60 ignores writes
    (["--profile", "1", "--set-report-rate", "500"], "profile 1 report rate code now 2: OK"),
    (["--set-macro", "3", "--macro-text", "hi"], "macro 3 now 01000b8a0b0a0c8a0c0a: OK"),
    (["--clear-macro", "0"], "macro 0 now (empty): OK"),
    (["--set-light", "always,FF0000,4"], "light always_on: OK"),
])
def test_writer_commands(mouse, monkeypatch, capsys, tmp_path, argv, expect):
    out = run(monkeypatch, capsys, W.main, ["--wait", "0", "--backup-dir", str(tmp_path), "--yes"] + argv)
    assert expect in out, out


def test_writer_restore(mouse, monkeypatch, capsys, tmp_path):
    common = ["--wait", "0", "--backup-dir", str(tmp_path)]
    backup = run(monkeypatch, capsys, W.main, common + ["--backup-only"]).split("backup saved: ")[1].strip()
    original = {p: bytes(b) for p, b in mouse.matrix.items()}, bytes(mouse.opt0)
    run(monkeypatch, capsys, W.main,
        common + ["--yes", "--profile", "3", "--set-slot", "0", "--action", "1,0,241,0"])
    run(monkeypatch, capsys, W.main, common + ["--yes", "--set-opt0", "debounce=6"])
    out = run(monkeypatch, capsys, W.main, common + ["--yes", "--restore", backup])
    assert "profile 3 slot  0 now [1, 0, 240, 0]: OK" in out and "debounce 1" in out
    assert ({p: bytes(b) for p, b in mouse.matrix.items()}, bytes(mouse.opt0)) == original
    out = run(monkeypatch, capsys, W.main, common + ["--restore", backup])
    assert "nothing differs" in out
    assert len(list(tmp_path.glob("backup-*.json"))) == 5          # one per run, none overwritten


def test_writer_refusals(mouse, monkeypatch, capsys, tmp_path):
    common = ["--wait", "0", "--backup-dir", str(tmp_path), "--yes"]
    mouse.model_id = 1847
    out = run(monkeypatch, capsys, W.main, common + ["--set-slot", "7", "--action", "0,0,34,0"])
    assert "EXIT model id 1847" in out
    mouse.model_id = 1833
    mouse.profile = 5
    out = run(monkeypatch, capsys, W.main, common + ["--set-slot", "7", "--action", "0,0,34,0"])
    assert out.startswith("EXIT the mouse reports active profile 5")
    assert not mouse.frames(0x50)
