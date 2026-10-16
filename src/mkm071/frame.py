"""Command framing: opcodes, BIT7 checksum, and the opcode allow-lists (docs/protocol.md sections 3-4).

Every frame is 64 bytes: opcode, up to 6 argument bytes, checksum at byte 7, then 56 payload bytes.
`build_frame` only builds queries. `build_command` builds the gated writes. Anything on the
never-send list raises `UnsafeCommand`, whichever way the frame was built.
"""
VID = 0x3151
PID_WIRED = 0x4026
PID_DONGLE = 0x4027
PID_BLUETOOTH = 0x4028
USAGE_PAGE = 0xFFFF
USAGE = 0x02
REPORT_LEN = 64  # payload bytes; the HID layer adds the report-ID byte
MKM071_ID = 1833

# Query opcodes (mouse class)
OP_GET_REV = 0x8F        # on the wired MKM071 this returns the model id (1833), not a version
OP_GET_REPORT = 0x84     # report rate for the profile given in byte 1 (mouse class)
OP_GET_PROFILE = 0x85    # current profile in reply byte 1 (0x84 only echoes the profile you ask about)
OP_GET_OPT0 = 0xD3
OP_GET_OPT1 = 0xD4
OP_GET_MATRIX = 0xD0
OP_GET_ID = 0xF1
OP_GET_FNMATRIX = 0xD1   # FN-layer matrix, same layout as 0xD0 (read-only)
OP_GET_BATTERY = 0x82    # keyboard battery query; on the wired mouse it only echoes
OP_GET_MACRO = 0x96      # GET_MACRO_SIMPLE: [0x96, macro index, page 0..3], 64 raw bytes per page
READ_OPCODES = {OP_GET_REV, OP_GET_REPORT, OP_GET_PROFILE, OP_GET_OPT0,
                OP_GET_OPT1, OP_GET_MATRIX, OP_GET_ID, OP_GET_FNMATRIX,
                OP_GET_BATTERY, OP_GET_MACRO}

# Write opcodes, each verified on hardware with a read-back (0x51 is accepted but not applied)
OP_SET_REPORT = 0x04     # per-profile report rate: [4, profile, code]
OP_SET_PROFILE = 0x05
OP_SET_MACRO = 0x16      # SET_MACRO_SIMPLE: pages of 56 bytes after an 8-byte header
OP_SET_SLOT = 0x50
OP_SET_FNSLOT = 0x51
OP_SET_OPT0 = 0x53
OP_SET_OPT1 = 0x54
WRITE_OPCODES = {OP_SET_REPORT, OP_SET_PROFILE, OP_SET_MACRO, OP_SET_SLOT,
                 OP_SET_FNSLOT, OP_SET_OPT0, OP_SET_OPT1}

# Never send: factory reset, bootloader entry (7F 55 AA 55 AA ...), bootloader/firmware transfer (BA ..)
NEVER_SEND = {0x01, 0x7F, 0xBA}


class UnsafeCommand(ValueError):
    """A frame on the never-send list, or an opcode outside the allow-list."""


def checksum7(frame):
    """byte7 = 0xFF - (sum(byte0..byte6) & 0xFF)"""
    return (0xFF - (sum(frame[:7]) & 0xFF)) & 0xFF


def check_frame(frame):
    """Raise UnsafeCommand unless the frame's opcode is a known query or gated write."""
    op = frame[0]
    if op in NEVER_SEND:
        raise UnsafeCommand("refusing to send 0x%02X (never-send list)" % op)
    if op not in READ_OPCODES and op not in WRITE_OPCODES:
        raise UnsafeCommand("refusing to send unknown opcode 0x%02X" % op)


def build_frame(opcode, *args):
    """64-byte query: [opcode, args(<=6), chk, zeros...]. Only READ_OPCODES are allowed."""
    if opcode not in READ_OPCODES:
        raise ValueError("refusing to build non-query opcode 0x%02X" % opcode)
    if len(args) > 6:
        raise ValueError("at most 6 argument bytes")
    f = bytearray(REPORT_LEN)
    f[0] = opcode
    for i, a in enumerate(args):
        f[1 + i] = a & 0xFF
    f[7] = checksum7(f)
    return bytes(f)


def build_command(opcode, *args, payload=b""):
    """64-byte write: [opcode, args(<=6), chk] + payload (<=56 bytes) at byte 8."""
    if opcode not in WRITE_OPCODES:
        raise UnsafeCommand("refusing to build non-write opcode 0x%02X" % opcode)
    if len(args) > 6:
        raise ValueError("at most 6 argument bytes")
    if len(payload) > REPORT_LEN - 8:
        raise ValueError("at most %d payload bytes" % (REPORT_LEN - 8))
    f = bytearray(REPORT_LEN)
    f[0] = opcode
    for i, a in enumerate(args):
        f[1 + i] = a & 0xFF
    f[7] = checksum7(f)
    f[8:8 + len(payload)] = payload
    return bytes(f)


def strip_report_id(buf, rid=0):
    """Drop the leading report-ID byte (hidapi and numbered reports include it)."""
    buf = bytes(buf)
    if len(buf) == REPORT_LEN + 1 and buf[0] == rid:
        return buf[1:]
    return buf
