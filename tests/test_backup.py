import json

import pytest

from fakedevice import FakeMouse
from mkm071 import ButtonAction, Macro, Mkm071, WriteNotApplied
from mkm071.backup import (SCHEMA_VERSION, load_backup, make_backup, readable_profiles, restore_plan,
                           save_backup)
from mkm071.macro import macro_text_events


def dev(mouse):
    d = Mkm071(mouse, wait=0)
    d.check_model()
    return d


def state(m):
    return ({p: bytes(b) for p, b in m.matrix.items()}, {p: bytes(b) for p, b in m.opt1.items()},
            bytes(m.opt0), {i: bytes(b[:224]) for i, b in m.macros.items()})


def test_backup_contents():
    bk = make_backup(FakeMouse(), wait=0)
    assert bk["schema_version"] == SCHEMA_VERSION and bk["model_id"] == 1833 and bk["profile"] == 0
    assert set(bk["profiles"]) == {"0", "1", "2", "3", "4"}          # 5..7 are not backed up
    assert set(bk["macros"]) == {str(i) for i in range(20)}
    assert bk["macros"]["0"].startswith("01000b8a0b0a0c8a0c0a") and bk["macros"]["1"] == "ff" * 256


def test_save_and_load(tmp_path):
    bk = make_backup(FakeMouse(), wait=0)
    path = save_backup(bk, str(tmp_path / "sub"))
    assert path.endswith(".json") and load_backup(path) == json.loads(json.dumps(bk))
    again = save_backup(bk, str(tmp_path / "sub"))                    # same second: new name
    assert again != path and load_backup(path) == load_backup(again)
    bad = tmp_path / "x.json"
    bad.write_text("{}")
    with pytest.raises(ValueError, match="not a backup"):
        load_backup(str(bad))
    bad.write_text(json.dumps(dict(bk, schema_version=SCHEMA_VERSION + 1)))
    with pytest.raises(ValueError, match="schema version"):
        load_backup(str(bad))


def test_change_everything_then_restore():
    m = FakeMouse()
    d = dev(m)
    bk = d.backup()
    original = state(m)
    d.set_slot(7, ButtonAction.key(0x22), profile=1)
    d.set_slot(0, ButtonAction.mouse("right"), profile=4)
    d.set_dpi(0, 1000, profile=3)
    d.set_stage(1, profile=2)
    d.set_opt0(debounce=6, rate=500)
    d.write_macro(2, Macro(1, macro_text_events("hi")))
    d.write_macro(0, None)
    plan = d.restore(bk)
    assert sorted((p, s) for p, s, _ in plan.slots) == [(1, 7), (4, 0)]
    assert sorted(p for p, _ in plan.opt1) == [2, 3] and plan.opt0 is not None
    assert sorted(i for i, _ in plan.macros) == [0, 2]
    assert state(m)[:3] == original[:3]
    assert d.read_macro(0) == Macro.from_bytes(bytes.fromhex(bk["macros"]["0"]))
    assert d.read_macro(2).empty
    assert not restore_plan(bk, make_backup(m, wait=0))               # nothing left to do


def test_restore_one_profile_only():
    m = FakeMouse()
    d = dev(m)
    bk = d.backup()
    d.set_slot(7, ButtonAction.key(0x22), profile=1)
    d.set_slot(7, ButtonAction.key(0x22), profile=2)
    d.set_opt0(debounce=6)
    plan = d.restore(bk, profile=2)
    assert [(p, s) for p, s, _ in plan.slots] == [(2, 7)] and plan.opt0 is None and not plan.macros
    assert m.matrix[1][28:32].hex() == "00002200" and m.opt0[10] == 6


def test_restore_v1_backup():
    m = FakeMouse()
    bk = make_backup(m, wait=0)
    v1 = {k: bk[k] for k in ("saved", "model_id", "profile", "matrix_hex", "opt0_hex", "opt1_hex")}
    assert list(readable_profiles(v1)) == [0]
    m.matrix[0][0:4] = bytes(4)
    m.matrix[1][0:4] = bytes(4)                                        # not in a v1 backup
    plan = restore_plan(v1, make_backup(m, wait=0))
    assert plan.slots == [(0, 0, [1, 0, 0xF0, 0])]


def test_restore_refusals():
    m = FakeMouse()
    bk = make_backup(m, wait=0)
    with pytest.raises(ValueError, match="different model"):
        restore_plan(dict(bk, model_id=1847), bk)
    broken = dict(bk, profiles=dict(bk["profiles"], **{"3": {"error": "IOError: x"}}))
    with pytest.raises(IOError, match="profile 3"):
        restore_plan(bk, broken)


def test_restore_stops_on_first_failure():
    m = FakeMouse()
    d = dev(m)
    bk = d.backup()
    d.set_slot(7, ButtonAction.key(0x22), profile=1)
    d.set_opt0(debounce=6)
    m.ignore.add(0x50)
    with pytest.raises(WriteNotApplied):
        d.restore(bk)
    assert m.opt0[10] == 6                                             # opt0 comes after the slots
