"""High-level API for the wired MKM071.

    with Mkm071.open() as m:                 # libusb, 3151:4026, interface 2
        print(m.model_id(), m.active_profile())
        m.set_dpi(stage=0, dpi=1000)          # read-modify-write, then read back
        m.set_slot(7, ButtonAction.key(0x22)) # thumb key 1 -> '5' in the active profile

Every write reads the block first, changes only what was asked, sends it, and reads it back.
A read-back that differs raises `WriteNotApplied` (the read-back is on the exception).
Writes refuse to run until the model id has been checked (`open()` does this) and refuse
profiles outside 0..MAX_PROFILE.
"""
import time

from . import commands as C
from .backup import make_backup, read_matrix, restore_plan
from .frame import (MKM071_ID, OP_GET_OPT0, OP_GET_OPT1, OP_GET_PROFILE, OP_GET_REPORT,
                    OP_GET_REV, PID_WIRED)
from .macro import MACRO_COUNT, MACRO_SIZE, macro_frames, macro_norm
from .model import (RATE_CODES, ButtonAction, DeviceState, DpiBlock, Macro, Matrix, Opt0,
                    Profile)
from .transport import UsbTransport, query, read_macro, send_command


class WriteNotApplied(IOError):
    def __init__(self, msg, readback=None):
        super().__init__(msg)
        self.readback = readback


class Mkm071:
    def __init__(self, link, rid=0, wait=0.02):
        self.link, self.rid, self.wait = link, rid, wait
        self._model = None
        self._writes_allowed = False

    @classmethod
    def open(cls, pid=PID_WIRED, iface=2, rid=0, wait=0.02, force_model=False):
        """Open over libusb and check the model id (pass force_model=True to write to another model)."""
        dev = cls(UsbTransport(pid=pid, iface=iface), rid=rid, wait=wait)
        try:
            dev.check_model(force=force_model)
        except Exception:
            dev.close()
            raise
        return dev

    def close(self):
        self.link.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ reads
    def _query(self, opcode, *args, **kw):
        return bytes(query(self.link, opcode, *args, rid=self.rid, wait=self.wait, **kw))

    def model_id(self):
        if self._model is None:
            rev = self._query(OP_GET_REV)
            self._model = rev[1] | rev[2] << 8
        return self._model

    def check_model(self, force=False):
        mid = self.model_id()
        if mid != MKM071_ID and not force:
            raise IOError("model id %d is not MKM071 (%d); refusing writes" % (mid, MKM071_ID))
        self._writes_allowed = True
        return mid

    def active_profile(self):
        return self._query(OP_GET_PROFILE)[1]

    def read_matrix(self, profile):
        return Matrix.from_bytes(read_matrix(self.link, profile, self.rid, self.wait))

    def read_opt0(self):
        return Opt0.from_bytes(self._query(OP_GET_OPT0))

    def read_dpi(self, profile):
        return DpiBlock.from_bytes(self._query(OP_GET_OPT1, profile))

    def read_rate(self, profile):
        return RATE_CODES.get(self._query(OP_GET_REPORT, profile)[2])

    def read_macro(self, idx):
        return Macro.from_bytes(read_macro(self.link, idx, rid=self.rid, wait=self.wait))

    def read_profile(self, profile):
        return Profile(profile, self.read_matrix(profile), self.read_dpi(profile),
                       self.read_rate(profile))

    def read_all(self, macros=True):
        return DeviceState(
            model_id=self.model_id(), active_profile=self.active_profile(),
            opt0=self.read_opt0(),
            profiles={p: self.read_profile(p) for p in range(C.MAX_PROFILE + 1)},
            macros={i: self.read_macro(i) for i in range(MACRO_COUNT)} if macros else {})

    def backup(self):
        """The backup dict (see mkm071.backup); save it with save_backup()."""
        return make_backup(self.link, self.rid, self.wait)

    # ------------------------------------------------------------ writes
    def _guard(self, profile=None):
        if not self._writes_allowed:
            raise IOError("call check_model() before writing")
        if profile is not None and not 0 <= profile <= C.MAX_PROFILE:
            raise ValueError("profile must be 0..%d" % C.MAX_PROFILE)

    def _send(self, frame):
        send_command(self.link, frame, rid=self.rid, wait=self.wait)

    def _target(self, profile):
        return self.active_profile() if profile is None else profile

    def set_slot(self, slot, action, profile=None):
        """Set one button slot (0..15) to a ButtonAction or 4 raw bytes. Returns the new Matrix."""
        profile = self._target(profile)
        self._guard(profile)
        action = bytes(action)
        self._send(C.build_set_slot(profile, slot, action))
        after = self.read_matrix(profile)
        if after[slot].raw != action:
            raise WriteNotApplied("profile %d slot %d reads %s" % (
                profile, slot, after[slot].raw.hex()), after)
        return after

    def _write_opt1(self, profile, frame):
        self._send(frame)
        rb = self._query(OP_GET_OPT1, profile)
        if rb[8:64] != frame[8:64] or rb[2] != frame[2]:
            raise WriteNotApplied("profile %d DPI block did not read back" % profile,
                                  DpiBlock.from_bytes(rb))
        return DpiBlock.from_bytes(rb)

    def set_dpi(self, stage, dpi, profile=None):
        """Set one DPI stage (X and Y) to `dpi` (multiple of 50, 50..MAX_DPI)."""
        profile = self._target(profile)
        self._guard(profile)
        cur = self._query(OP_GET_OPT1, profile)
        return self._write_opt1(profile, C.build_set_opt1(profile, cur, stage, dpi))

    def set_stage(self, stage, profile=None):
        """Make `stage` the active DPI stage."""
        profile = self._target(profile)
        self._guard(profile)
        cur = self._query(OP_GET_OPT1, profile)
        return self._write_opt1(profile, C.build_set_opt1(profile, cur, active=stage))

    def set_stage_color(self, stage, rgb, profile=None):
        """Set the wheel-LED colour shown for `stage` (rgb as 0xRRGGBB)."""
        profile = self._target(profile)
        self._guard(profile)
        cur = self._query(OP_GET_OPT1, profile)
        return self._write_opt1(profile, C.build_set_opt1(profile, cur, color_stage=stage, rgb=rgb))

    def write_dpi_block(self, block, profile=None):
        """Store a whole DpiBlock (e.g. one edited from read_dpi())."""
        profile = block.profile if profile is None else profile
        self._guard(profile)
        return self._write_opt1(profile, block.to_frame(profile))

    def _write_opt0(self, frame):
        self._send(frame)
        rb = self._query(OP_GET_OPT0)
        if rb[8:64] != frame[8:64]:
            raise WriteNotApplied("option block 0 did not read back", Opt0.from_bytes(rb))
        return Opt0.from_bytes(rb)

    def set_opt0(self, **fields):
        """Change option-block-0 fields: rate (Hz), debounce, sleep_bt, sleep_24, x_sens, y_sens,
        lift, angle_snap, charging. Everything else is echoed."""
        self._guard()
        C.check_opt0_fields(fields)
        return self._write_opt0(C.build_set_opt0(self._query(OP_GET_OPT0), fields=fields))

    def set_light(self, spec):
        """Light record from a spec like 'always,FF0000,4' (no visible effect on the MKM071)."""
        self._guard()
        return self._write_opt0(C.build_set_opt0(self._query(OP_GET_OPT0), C.parse_light_spec(spec)))

    def set_rate(self, hz, profile=None):
        """Per-profile report rate through opcode 0x04 (untested on hardware; the verified path
        is set_opt0(rate=...))."""
        profile = self._target(profile)
        self._guard(profile)
        fr = C.build_set_rate(profile, hz)
        self._send(fr)
        now = self._query(OP_GET_REPORT, profile)[2]
        if now != fr[2]:
            raise WriteNotApplied("profile %d rate code reads %d" % (profile, now), now)
        return RATE_CODES.get(now)

    def switch_profile(self, n):
        self._guard()
        self._send(C.build_set_profile(n))
        now = self.active_profile()
        if now != n:
            raise WriteNotApplied("active profile reads %d" % now, now)
        return now

    def write_macro(self, idx, macro):
        """Write a Macro (or a raw 256-byte buffer) to slot idx; None clears it."""
        self._guard()
        buf = bytes(MACRO_SIZE) if macro is None else (
            macro.to_bytes() if isinstance(macro, Macro) else bytes(macro))
        for fr in macro_frames(idx, buf):
            self._send(fr)
        time.sleep(0.1)
        rb = read_macro(self.link, idx, rid=self.rid, wait=self.wait)
        if macro_norm(rb) != macro_norm(buf):
            raise WriteNotApplied("macro %d did not read back" % idx, rb)
        return Macro.from_bytes(rb)

    def restore(self, backup, profile=None):
        """Write back everything in `backup` that differs from the device. Returns the plan used.
        Stops at the first write that does not read back (WriteNotApplied); tools/mkm071_write.py
        --restore instead reports each write and carries on."""
        self._guard(profile)
        plan = restore_plan(backup, self.backup(), profile)
        for idx, buf in plan.macros:
            self.write_macro(idx, buf)
        for p, slot, act in plan.slots:
            self.set_slot(slot, act, profile=p)
        for p, fr in plan.opt1:
            self._write_opt1(p, fr)
        if plan.opt0 is not None:
            self._write_opt0(plan.opt0)
        return plan


__all__ = ["Mkm071", "WriteNotApplied", "ButtonAction"]
