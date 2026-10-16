"""Full-device backups (JSON) and the diff that restores one.

Schema (version 2): saved, schema_version, model_id, profile (active), matrix_hex / opt0_hex /
opt1_hex (active profile), profiles {"0".."4": {matrix_hex, opt1_hex} | {error}}, macros
{"0".."19": hex | {error}}. Version 1 backups (no schema_version, possibly no profiles) still load.
"""
import datetime
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .commands import MAX_PROFILE, build_set_opt0, build_set_opt1
from .frame import OP_GET_OPT0, OP_GET_OPT1, OP_GET_PROFILE, OP_GET_REV, OP_GET_MATRIX
from .macro import MACRO_COUNT, MACRO_SIZE, macro_norm
from .transport import query, read_macro

SCHEMA_VERSION = 2


def read_matrix(link, profile, rid=0, wait=None):
    return bytes(query(link, OP_GET_MATRIX, profile, rid=rid, wait=wait, echo=False)[:64])


def make_backup(link, rid=0, wait=None):
    rev = query(link, OP_GET_REV, rid=rid, wait=wait)
    profile = query(link, OP_GET_PROFILE, rid=rid, wait=wait)[1]
    profiles = {}
    for p in range(MAX_PROFILE + 1):  # the mouse has 5 profiles (verified: all five read back)
        try:
            profiles[str(p)] = {
                "matrix_hex": read_matrix(link, p, rid, wait).hex(),
                "opt1_hex": bytes(query(link, OP_GET_OPT1, p, rid=rid, wait=wait)).hex()}
        except Exception as e:
            profiles[str(p)] = {"error": "%s: %s" % (type(e).__name__, e)}
    macros = {}
    for m in range(MACRO_COUNT):
        try:
            macros[str(m)] = read_macro(link, m, rid=rid, wait=wait).hex()
        except Exception as e:
            macros[str(m)] = {"error": "%s: %s" % (type(e).__name__, e)}
            break
    return {
        "saved": datetime.datetime.now().isoformat(timespec="seconds"),
        "schema_version": SCHEMA_VERSION,
        "model_id": rev[1] | rev[2] << 8,
        "profile": profile,
        "matrix_hex": read_matrix(link, profile, rid, wait).hex(),
        "opt0_hex": bytes(query(link, OP_GET_OPT0, rid=rid, wait=wait)).hex(),
        "opt1_hex": bytes(query(link, OP_GET_OPT1, profile, rid=rid, wait=wait)).hex(),
        "profiles": profiles,
        "macros": macros,
    }


def save_backup(bk, directory, prefix="backup"):
    """Write `bk` to DIRECTORY/PREFIX-YYYYmmdd-HHMMSS.json and return the path. Never overwrites:
    a second backup in the same second gets a -2, -3, ... suffix."""
    os.makedirs(directory, exist_ok=True)
    stem = os.path.join(os.path.normpath(directory), "%s-%s" % (
        prefix, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    n = 1
    while True:
        name = stem + (".json" if n == 1 else "-%d.json" % n)
        try:
            f = open(name, "x")
        except FileExistsError:
            n += 1
            continue
        with f:
            json.dump(bk, f, indent=2)
        return name


def load_backup(path):
    with open(path) as f:
        bk = json.load(f)
    for key in ("model_id", "profile", "opt0_hex"):
        if key not in bk:
            raise ValueError("%s is not a backup (no %r)" % (path, key))
    if bk.get("schema_version", 1) > SCHEMA_VERSION:
        raise ValueError("%s has schema version %d; this library reads up to %d" % (
            path, bk["schema_version"], SCHEMA_VERSION))
    return bk


def readable_profiles(bk):
    """{profile: {matrix_hex, opt1_hex}} for the profiles a backup holds without errors.
    A version 1 backup without `profiles` holds the active profile only."""
    out = {int(p): d for p, d in bk.get("profiles", {}).items() if "error" not in d}
    if not out and "matrix_hex" in bk:
        out = {bk["profile"]: {"matrix_hex": bk["matrix_hex"], "opt1_hex": bk["opt1_hex"]}}
    return out


def slot_of(matrix, i):
    return list(matrix[4 * i:4 * i + 4])


@dataclass
class RestorePlan:
    slots: List[Tuple[int, int, list]] = field(default_factory=list)   # (profile, slot, action)
    opt1: List[Tuple[int, bytes]] = field(default_factory=list)        # (profile, 0x54 frame)
    opt0: Optional[bytes] = None                                       # 0x53 frame
    macros: List[Tuple[int, bytes]] = field(default_factory=list)      # (index, 256-byte buffer)

    def __bool__(self):
        return bool(self.slots or self.opt1 or self.opt0 is not None or self.macros)


def restore_plan(old, current, profile=None):
    """Writes that turn the device state `current` (a fresh make_backup) into backup `old`.
    With `profile`, only that profile's matrix and DPI block; option block 0 and macros are skipped."""
    if old["model_id"] != current["model_id"]:
        raise ValueError("backup is for a different model")
    have = readable_profiles(current)
    want = readable_profiles(old)
    if profile is not None:
        want = {p: d for p, d in want.items() if p == profile}
    plan = RestorePlan()
    for p, d in sorted(want.items()):
        if p not in have:
            raise IOError("could not read profile %d from the mouse" % p)
        have_m, have_o = bytes.fromhex(have[p]["matrix_hex"]), bytes.fromhex(have[p]["opt1_hex"])
        want_m, want_o = bytes.fromhex(d["matrix_hex"]), bytes.fromhex(d["opt1_hex"])
        plan.slots += [(p, i, slot_of(want_m, i)) for i in range(16)
                       if slot_of(want_m, i) != slot_of(have_m, i)]
        if want_o[2:4] != have_o[2:4] or want_o[8:64] != have_o[8:64]:
            plan.opt1.append((p, build_set_opt1(p, want_o)))
    if profile is None:
        old_opt0, cur_opt0 = bytes.fromhex(old["opt0_hex"]), bytes.fromhex(current["opt0_hex"])
        if old_opt0[8:64] != cur_opt0[8:64]:
            plan.opt0 = build_set_opt0(old_opt0)
        cur_macros = {int(k): bytes.fromhex(v) for k, v in current.get("macros", {}).items()
                      if isinstance(v, str)}
        for k, v in old.get("macros", {}).items():
            if not isinstance(v, str) or int(k) not in cur_macros:
                continue
            want_n = macro_norm(bytes.fromhex(v))
            if want_n != macro_norm(cur_macros[int(k)]):
                plan.macros.append((int(k), want_n.ljust(MACRO_SIZE, b"\0")))
    return plan
