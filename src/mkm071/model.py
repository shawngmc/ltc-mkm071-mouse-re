"""Decoded device data.

Two layers: the `parse_*` functions return plain dicts (the probe's JSON output), and the
dataclasses decode a block into typed fields while keeping the raw bytes, so `to_bytes()` gives
back the block byte-for-byte, including fields nobody has decoded yet.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .commands import build_set_opt1
from .macro import MACRO_BUTTONS, MACRO_COUNT, MACRO_MODES, build_macro_buffer, parse_macro

RATE_CODES = {0x01: 1000, 0x02: 500, 0x04: 250, 0x08: 125,
              0x81: 8000, 0x82: 4000, 0x84: 2000}
LIGHT_TYPES = {0: "off", 1: "always_on", 2: "breathe", 3: "neon", 4: "wave",
               5: "dazzling", 6: "laser", 7: "music_follow",
               8: "screen_color", 9: "music_follow_2"}
KNOWN_IDS = {1833: "MKM071 (g29_yj01z_3104)", 1847: "GM025 (g51_gm025_3311)",
             1975: "GM024", 1829: "GM023", 1771: "GW24"}
# MKM071 slot -> physical control (docs/protocol.md section 2.3)
SLOT_NAMES = {0: "left", 1: "profile_button", 2: "side_button", 3: "dpi_button", 4: "forward",
              5: "back", 6: "wheel_click", 7: "thumb_1", 8: "thumb_2", 9: "right",
              10: "thumb_3", 11: "thumb_4", 12: "wheel_up", 13: "wheel_down",
              14: "unused_14", 15: "unused_15"}


# ---------------------------------------------------------------- dict parsers
def rgb(b, o):
    return "#%02X%02X%02X" % (b[o], b[o + 1], b[o + 2])


def parse_light(rec):
    return {"type": LIGHT_TYPES.get(rec[0], "unknown(%d)" % rec[0]),
            "speed": 4 - rec[1], "value": rec[2], "flags": rec[3],
            "rgb": rgb(rec, 4)}


def parse_opt0(b):
    flags = b[12] | (b[13] << 8)
    return {
        "profile": b[8],
        "report_rate_hz": RATE_CODES.get(b[9], "unknown(0x%02X)" % b[9]),
        "debounce": b[10],
        "flags": {"light_off": bool(flags & 1), "wheel_light_off": bool(flags & 2),
                  "smooth": bool(flags & 4), "led_select": bool(flags & 8),
                  "power_save": bool(flags & 16)},
        "button_change": b[14], "wheel_to_button": b[15], "button_to_wheel": b[16],
        "light": parse_light(b[24:32]),
        "sleep_s": {"bt": b[40] | b[41] << 8, "bt_deep": b[42] | b[43] << 8,
                    "dongle_2g4": b[44] | b[45] << 8,
                    "dongle_2g4_deep": b[46] | b[47] << 8},
        "x_sensitivity": b[50], "y_sensitivity": b[51],
        "lift_cutoff": b[52], "angle_snap": b[53],
        "battery_colors": [rgb(b, 54), rgb(b, 57)],
        "charging_switch": bool(b[60]),
    }


def parse_opt1(b):
    xs = [b[8 + 2 * i] | b[9 + 2 * i] << 8 for i in range(8)]
    ys = [b[24 + 2 * i] | b[25 + 2 * i] << 8 for i in range(8)]
    return {"current_stage": b[2], "stage_count": b[3],
            "current_dpi": xs[b[2]] if b[2] < 8 else None,
            "dpi_x": xs, "dpi_y": ys,
            "stage_colors": [rgb(b, 40 + 3 * i) for i in range(8)]}


def describe_slot(s):
    s = tuple(s)
    if s == (0, 0, 0, 0):
        return "disabled"
    if s[0] == 0:
        return "keyboard mods=0x%02X key=0x%02X key2=0x%02X" % (s[1], s[2], s[3])
    if s[0] == 1:
        names = {0xF0: "left", 0xF1: "right", 0xF2: "middle", 0xF3: "back",
                 0xF4: "forward", 0xF5: "wheel_left/scroll", 0xF6: "wheel_right",
                 0xF7: "wheel_fwd", 0xF8: "wheel_back"}
        return "mouse " + names.get(s[2], "0x%02X" % s[2]) + (
            " arg=%d" % s[3] if s[3] else "")
    if s[0] == 20:
        return {0: "dpi_cycle", 1: "dpi_up", 2: "dpi_down", 4: "dpi_shift"}.get(
            s[2], "dpi_function %s" % (list(s),))
    if s[0] == 8:  # profile functions, from GearMaster's function table
        return {(0, 0): "profile_value", (0, 1): "profile_up", (0, 2): "profile_down",
                (0, 3): "profile_cycle"}.get((s[1], s[2]),
               "profile_exchange %d" % s[3] if s[2] == 4 else "profile fn %s" % (list(s),))
    if s[0] == 9:
        return "macro #%d mode=%d" % (s[2], s[1])
    if s[0] == 3:
        return "consumer usage=0x%04X" % (s[2] | s[3] << 8)
    if s[0] == 2:
        return "system power key 0x%02X" % s[1]
    if s[0] == 10:
        return "fn key (%s)" % (list(s),)
    if s[0] == 11:
        return "fire key"
    if s[0] == 18:
        return "open website/siri"
    return "function/unknown %s" % (list(s),)


def parse_matrix(b):
    return [{"slot": i, "raw": list(b[4 * i:4 * i + 4]),
             "meaning": describe_slot(b[4 * i:4 * i + 4])} for i in range(16)]


# ---------------------------------------------------------------- typed model
def _u16(b, o):
    return b[o] | b[o + 1] << 8


def _put_u16(b, o, v):
    b[o], b[o + 1] = v & 0xFF, v >> 8 & 0xFF


def _rgb_int(b, o):
    return b[o] << 16 | b[o + 1] << 8 | b[o + 2]


def _put_rgb(b, o, v):
    b[o:o + 3] = bytes([v >> 16 & 255, v >> 8 & 255, v & 255])


MOUSE_CODES = MACRO_BUTTONS  # same codes as macro mouse events
DPI_FUNCS = {"cycle": 0, "up": 1, "down": 2, "shift": 4}
PROFILE_FUNCS = {"up": 1, "down": 2, "cycle": 3}  # "next" = up, "previous" = down
ACTION_KINDS = {0: "key", 1: "mouse", 2: "system", 3: "consumer", 8: "profile", 9: "macro",
                10: "fn", 11: "fire", 18: "launch", 20: "dpi"}


@dataclass(frozen=True)
class ButtonAction:
    """One 4-byte button-matrix slot: [type, a, b, c] (docs/protocol.md section 5.5)."""
    raw: bytes = bytes(4)

    def __post_init__(self):
        if len(self.raw) != 4:
            raise ValueError("a slot is 4 bytes")
        object.__setattr__(self, "raw", bytes(self.raw))

    @classmethod
    def disabled(cls):
        return cls(bytes(4))

    @classmethod
    def mouse(cls, name):
        return cls(bytes([1, 0, MOUSE_CODES[name], 0]))

    @classmethod
    def key(cls, usage, mods=0, usage2=0):
        return cls(bytes([0, mods, usage, usage2]))

    @classmethod
    def macro(cls, index, mode="repeat"):
        if not 0 <= index < MACRO_COUNT:
            raise ValueError("macro index must be 0..%d" % (MACRO_COUNT - 1))
        return cls(bytes([9, MACRO_MODES[mode], index, 0]))

    @classmethod
    def dpi(cls, fn="cycle"):
        return cls(bytes([20, 0, DPI_FUNCS[fn], 0]))

    @classmethod
    def profile(cls, fn="cycle"):
        return cls(bytes([8, 0, PROFILE_FUNCS[fn], 0]))

    @property
    def kind(self):
        if self.raw == bytes(4):
            return "disabled"
        return ACTION_KINDS.get(self.raw[0], "unknown")

    def describe(self):
        return describe_slot(self.raw)

    def __bytes__(self):
        return self.raw


@dataclass
class Matrix:
    """The 16-slot button matrix of one profile (0xD0 reply, no opcode echo)."""
    slots: List[ButtonAction]

    @classmethod
    def from_bytes(cls, b):
        b = bytes(b)
        if len(b) < 64:
            raise ValueError("matrix is 64 bytes")
        return cls([ButtonAction(b[4 * i:4 * i + 4]) for i in range(16)])

    def to_bytes(self):
        return b"".join(s.raw for s in self.slots)

    def __getitem__(self, i):
        return self.slots[i]


@dataclass
class LightRecord:
    """8-byte light record [type, 4 - speed, brightness, flags, R, G, B, pad]."""
    type: int
    speed_code: int
    brightness: int
    flags: int
    rgb: int
    pad: int = 0

    @classmethod
    def from_bytes(cls, b):
        return cls(b[0], b[1], b[2], b[3], _rgb_int(b, 4), b[7])

    def to_bytes(self):
        b = bytearray([self.type, self.speed_code, self.brightness, self.flags, 0, 0, 0, self.pad])
        _put_rgb(b, 4, self.rgb)
        return bytes(b)

    @property
    def type_name(self):
        return LIGHT_TYPES.get(self.type, "unknown(%d)" % self.type)

    @property
    def speed(self):
        return 4 - self.speed_code


@dataclass
class Opt0:
    """Option block 0 (0xD3 reply / 0x53 payload), shared by all profiles (docs/protocol.md 5.1)."""
    profile: int
    rate_code: int
    debounce: int
    flags: int
    button_change: int
    wheel_to_button: int
    button_to_wheel: int
    light: LightRecord
    logo_light: LightRecord
    sleep_bt: int
    deep_sleep_bt: int
    sleep_24: int
    deep_sleep_24: int
    x_sensitivity: int
    y_sensitivity: int
    lift_cutoff: int
    angle_snap: int
    battery_colors: Tuple[int, int]
    charging_switch: int
    raw: bytes = field(default=bytes(64), repr=False)

    @classmethod
    def from_bytes(cls, b):
        b = bytes(b)
        if len(b) < 64:
            raise ValueError("option block 0 is 64 bytes")
        return cls(profile=b[8], rate_code=b[9], debounce=b[10], flags=_u16(b, 12),
                   button_change=b[14], wheel_to_button=b[15], button_to_wheel=b[16],
                   light=LightRecord.from_bytes(b[24:32]),
                   logo_light=LightRecord.from_bytes(b[32:40]),
                   sleep_bt=_u16(b, 40), deep_sleep_bt=_u16(b, 42),
                   sleep_24=_u16(b, 44), deep_sleep_24=_u16(b, 46),
                   x_sensitivity=b[50], y_sensitivity=b[51], lift_cutoff=b[52],
                   angle_snap=b[53], battery_colors=(_rgb_int(b, 54), _rgb_int(b, 57)),
                   charging_switch=b[60], raw=b[:64])

    def to_bytes(self):
        b = bytearray(self.raw)
        b[8], b[9], b[10] = self.profile, self.rate_code, self.debounce
        _put_u16(b, 12, self.flags)
        b[14], b[15], b[16] = self.button_change, self.wheel_to_button, self.button_to_wheel
        b[24:32] = self.light.to_bytes()
        b[32:40] = self.logo_light.to_bytes()
        for o, v in ((40, self.sleep_bt), (42, self.deep_sleep_bt),
                     (44, self.sleep_24), (46, self.deep_sleep_24)):
            _put_u16(b, o, v)
        b[50], b[51], b[52], b[53] = (self.x_sensitivity, self.y_sensitivity,
                                      self.lift_cutoff, self.angle_snap)
        _put_rgb(b, 54, self.battery_colors[0])
        _put_rgb(b, 57, self.battery_colors[1])
        b[60] = self.charging_switch
        return bytes(b)

    @property
    def report_rate_hz(self):
        return RATE_CODES.get(self.rate_code)


@dataclass
class DpiBlock:
    """Option block 1 (0xD4 reply / 0x54 payload): per-profile DPI stages and stage colours."""
    profile: int
    current_stage: int
    stage_count: int
    dpi_x: List[int]
    dpi_y: List[int]
    colors: List[int]
    raw: bytes = field(default=bytes(64), repr=False)

    @classmethod
    def from_bytes(cls, b):
        b = bytes(b)
        if len(b) < 64:
            raise ValueError("DPI block is 64 bytes")
        return cls(profile=b[1], current_stage=b[2], stage_count=b[3],
                   dpi_x=[_u16(b, 8 + 2 * i) for i in range(8)],
                   dpi_y=[_u16(b, 24 + 2 * i) for i in range(8)],
                   colors=[_rgb_int(b, 40 + 3 * i) for i in range(8)], raw=b[:64])

    def to_bytes(self):
        b = bytearray(self.raw)
        b[1], b[2], b[3] = self.profile, self.current_stage, self.stage_count
        for i in range(8):
            _put_u16(b, 8 + 2 * i, self.dpi_x[i])
            _put_u16(b, 24 + 2 * i, self.dpi_y[i])
            _put_rgb(b, 40 + 3 * i, self.colors[i])
        return bytes(b)

    def to_frame(self, profile=None):
        """The 0x54 write that stores this block (for `profile`, default its own)."""
        return build_set_opt1(self.profile if profile is None else profile, self.to_bytes())

    @property
    def stages(self):
        return list(zip(self.dpi_x, self.dpi_y, self.colors))[:self.stage_count]

    @property
    def current_dpi(self):
        return self.dpi_x[self.current_stage] if self.current_stage < 8 else None


@dataclass
class Macro:
    """A macro slot: repeat count and event list. Zero-length delays are dropped when decoding,
    so decode/encode is exact for buffers this library wrote, not for arbitrary bytes."""
    repeat: int = 1
    events: List[dict] = field(default_factory=list)

    @classmethod
    def from_bytes(cls, b):
        b = bytes(b)
        if not any(b[2:6]) or b == b"\xff" * len(b):
            return cls(repeat=0, events=[])
        d = parse_macro(b)
        return cls(repeat=d["repeat"], events=d["events"])

    def to_bytes(self):
        if not self.events:
            return bytes(256)
        return build_macro_buffer(self.repeat, self.events)

    @property
    def empty(self):
        return not self.events


@dataclass
class Profile:
    index: int
    matrix: Matrix
    dpi: DpiBlock
    rate_hz: Optional[int] = None


@dataclass
class DeviceState:
    model_id: int
    active_profile: int
    opt0: Opt0
    profiles: Dict[int, Profile]
    macros: Dict[int, Macro] = field(default_factory=dict)

    @property
    def model_name(self):
        return KNOWN_IDS.get(self.model_id, "unknown")


__all__ = ["RATE_CODES", "LIGHT_TYPES", "KNOWN_IDS", "SLOT_NAMES", "MOUSE_CODES",
           "rgb", "parse_light", "parse_opt0", "parse_opt1", "describe_slot", "parse_matrix",
           "ButtonAction", "Matrix", "LightRecord", "Opt0", "DpiBlock", "Macro", "Profile",
           "DeviceState"]
