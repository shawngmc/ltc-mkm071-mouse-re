"""Builders for the gated write frames (button slot, option blocks, profile, report rate).

Option-block writes are built from a fresh read of the same block, so every byte the caller did
not ask to change is echoed back unchanged (docs/protocol.md sections 2.3 and 2.7).
"""
from .frame import (OP_SET_OPT0, OP_SET_OPT1, OP_SET_PROFILE, OP_SET_REPORT, OP_SET_SLOT,
                    checksum7)

MAX_PROFILE = 4  # the listing says 5 profiles (0..4); the firmware also answers for 5..7
SLOT_COUNT = 16
MAX_DPI = 12000
LIGHT_MODES = {'off': 0, 'always': 1, 'breath': 2, 'neon': 3, 'wave': 4,
               'dazzle': 5, 'laser': 6}
MAXSPEED = 4
RATE_TO_CODE = {1000: 1, 500: 2, 250: 4, 125: 8}
# Fields of option block 0 that may be changed (offset, width, min, max); ranges follow
# GearMaster's settings for this model (debounce max 10, sleep 0..60 min, no deep-sleep UI).
OPT0_FIELDS = {"debounce": (10, 1, 0, 10), "sleep_bt": (40, 2, 0, 3600),
               "sleep_24": (44, 2, 0, 3600), "x_sens": (50, 1, 10, 200),
               "y_sens": (51, 1, 10, 200), "lift": (52, 1, 0, 3),
               "angle_snap": (53, 1, 0, 1), "charging": (60, 1, 0, 1)}


def build_set_slot(profile, slot, action):
    action = bytes(action)
    if not 0 <= slot < SLOT_COUNT:
        raise ValueError("slot must be 0..15")
    if len(action) != 4:
        raise ValueError("action must be 4 bytes")
    frame = bytearray(64)
    frame[0], frame[1], frame[2] = OP_SET_SLOT, profile, slot
    frame[7] = checksum7(frame)
    frame[8:12] = action
    return bytes(frame)


def build_set_opt1(profile, reply, stage=None, dpi=None, color_stage=None,
                   rgb=None, active=None):
    """Rebuild the 0x54 write from a 0xD4 read so nothing else in the block changes.
    GearMaster writes [0x54, profile, current_stage, stage_count, 0,0,0,chk] + 56 bytes
    (8 X dpi u16, 8 Y dpi u16, 8 RGB colours). Colours MUST be echoed back or they are blanked."""
    reply = bytes(reply)
    frame = bytearray(64)
    frame[0], frame[1], frame[2], frame[3] = OP_SET_OPT1, profile, reply[2], reply[3]
    frame[8:64] = reply[8:64]
    if stage is not None:
        if not 0 <= stage < reply[3]:
            raise ValueError("stage must be 0..%d" % (reply[3] - 1))
        if dpi % 50 or not 50 <= dpi <= MAX_DPI:
            raise ValueError("dpi must be a multiple of 50 in 50..%d" % MAX_DPI)
        for base in (8, 24):  # X table, then Y table (same value, like GearMaster's banXY)
            frame[base + 2 * stage] = dpi & 0xFF
            frame[base + 2 * stage + 1] = dpi >> 8
    if color_stage is not None:  # stage indicator colour (what the wheel LED shows)
        if not 0 <= color_stage < reply[3]:
            raise ValueError("stage must be 0..%d" % (reply[3] - 1))
        if not 0 <= rgb <= 0xFFFFFF:
            raise ValueError("rgb must be 6 hex digits")
        o = 40 + 3 * color_stage
        frame[o:o + 3] = bytes([rgb >> 16, rgb >> 8 & 255, rgb & 255])
    if active is not None:  # make this the active stage (header byte 2)
        if not 0 <= active < reply[3]:
            raise ValueError("stage must be 0..%d" % (reply[3] - 1))
        frame[2] = active
    frame[7] = checksum7(frame)
    return bytes(frame)


def build_set_profile(n):
    if not 0 <= n <= MAX_PROFILE:
        raise ValueError("profile must be 0..%d" % MAX_PROFILE)
    frame = bytearray(64)
    frame[0], frame[1] = OP_SET_PROFILE, n
    frame[7] = checksum7(frame)
    return bytes(frame)


def parse_light_spec(spec):
    parts = spec.split(",")
    mode = parts[0].lower()
    if mode not in LIGHT_MODES:
        raise ValueError("mode must be one of %s" % "|".join(LIGHT_MODES))
    rgb = int(parts[1], 16) if len(parts) > 1 and parts[1] else None
    bright = int(parts[2]) if len(parts) > 2 and parts[2] else 4
    speed = int(parts[3]) if len(parts) > 3 and parts[3] else 0
    if rgb is not None and not 0 <= rgb <= 0xFFFFFF:
        raise ValueError("rgb must be 6 hex digits")
    if not 0 <= bright <= 4 or not 0 <= speed <= 4:
        raise ValueError("brightness and speed must be 0..4")
    return {"mode": mode, "rgb": rgb, "bright": bright, "speed": speed}


def build_set_opt0(reply, light=None, fields=None):
    """0x53 write built from a 0xD3 read. Only the light record (bytes 24..31) and the named
    `fields` change; sleep timers, sensitivity, rate, debounce, flags, battery colours are echoed."""
    reply = bytes(reply)
    frame = bytearray(64)
    frame[0] = OP_SET_OPT0
    frame[8:64] = reply[8:64]
    if light is not None:
        code = LIGHT_MODES[light["mode"]]
        buf = bytearray(8)
        if code:
            buf[0] = code
            buf[1] = MAXSPEED - light["speed"]
            buf[2] = light["bright"]
            buf[3] = 7  # "normal" colour mode (GearMaster NORMAL)
            if light["rgb"] is not None:
                c = 0xFAFAFA if light["rgb"] == 0xFFFFFF else light["rgb"]  # GearMaster quirk
                buf[4:7] = bytes([c >> 16, c >> 8 & 255, c & 255])
            else:
                buf[4:7] = reply[28:31]
        frame[24:32] = buf
    for name, val in (fields or {}).items():
        if name == "rate":
            frame[9] = RATE_TO_CODE[val]
            continue
        off, width, lo, hi = OPT0_FIELDS[name]
        frame[off:off + width] = val.to_bytes(width, "little")
    frame[7] = checksum7(frame)
    return bytes(frame)


def check_opt0_fields(fields):
    """Validate {field: value} for build_set_opt0; raises ValueError."""
    for k, v in fields.items():
        if k == "rate":
            if v not in RATE_TO_CODE:
                raise ValueError("rate must be one of %s" % sorted(RATE_TO_CODE))
            continue
        if k not in OPT0_FIELDS:
            raise ValueError("unknown field %r; use rate, sleep, %s" % (
                k, ", ".join(OPT0_FIELDS)))
        if not OPT0_FIELDS[k][2] <= v <= OPT0_FIELDS[k][3]:
            raise ValueError("%s must be %d..%d" % (k, OPT0_FIELDS[k][2], OPT0_FIELDS[k][3]))
    return fields


def parse_opt0_spec(spec):
    """'rate=500,debounce=4,sleep=60' -> dict. sleep sets both the Bluetooth and 2.4G timers."""
    out = {}
    for part in spec.split(","):
        k, v = part.split("=")
        k, v = k.strip().lower(), int(v, 0)
        if k == "sleep":
            if not OPT0_FIELDS["sleep_bt"][2] <= v <= OPT0_FIELDS["sleep_bt"][3]:
                raise ValueError("sleep must be 0..3600 seconds")
            out["sleep_bt"] = out["sleep_24"] = v
        else:
            out[k] = v
    return check_opt0_fields(out)


def build_set_rate(profile, hz):
    if hz not in RATE_TO_CODE:
        raise ValueError("rate must be one of %s" % sorted(RATE_TO_CODE))
    if not 0 <= profile <= MAX_PROFILE:
        raise ValueError("profile must be 0..%d" % MAX_PROFILE)
    frame = bytearray(64)
    frame[0], frame[1], frame[2] = OP_SET_REPORT, profile, RATE_TO_CODE[hz]
    frame[7] = checksum7(frame)
    return bytes(frame)
