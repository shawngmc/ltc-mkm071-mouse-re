"""A simulated wired MKM071: answers the documented queries and applies the gated writes the way
the real mouse did in the hardware tests (docs/protocol.md section 0 and 2.x).

Behaviour copied from the hardware notes:
- 0xD0, 0xD1 and 0x96 replies carry raw data from byte 0 (no opcode echo); 0xD3/0xD4 echo the header.
- 0xF1 and 0x82 only echo the request; unknown opcodes answer with 64 zero bytes.
- Profiles 0..7 answer; 5..7 are default-initialized (active DPI stage 2). Profile 0x05 accepts any of them.
- 0xD1 is a fixed factory layout for every profile; 0x51 writes are accepted and ignored.
- Option block 0 byte 8 reads back the active profile; byte 60 (charging switch) ignores writes.
- Macros: a never-written slot reads all 0xFF. Writing page 0 erases bytes 0..223 to 0xFF first;
  bytes 224..255 are never written and read back as stale junk.
- mode="dongle" answers every query with zeros, like the 2.4G dongle with wired framing.

Every received frame is checked: 64 bytes after the report ID, valid BIT7 checksum, and nothing
from the never-send list (the fake raises instead of "bricking").
"""
# The FN-layer dump (0xD1), identical to the factory layout of profiles 0, 3 and 4 (2026-10-15)
FACTORY_MATRIX = bytes.fromhex(
    "0100f000" "08000300" "0100f200" "14000000" "0100f400" "0100f300" "0100f200" "00001e00"
    "00001f00" "0100f100" "00002000" "00002100" "0100f901" "0100f9ff" "0100f501" "0100f5ff")
DPI_STAGES = [800, 2400, 3200, 6400, 9600, 12000, 0, 0]
STAGE_COLORS = [0xFF0000, 0xFF8000, 0xFFFF00, 0x00FF00, 0x0000FF, 0xFF00FF]
MACRO_JUNK = bytes.fromhex("ff22090212ba") + b"\xff" * 26  # bytes 224..255 after a write
HI_MACRO = bytes.fromhex("01000b8a0b0a0c8a0c0a")           # "hi", repeat 1, 10 ms delays
NEVER_SEND = {0x01, 0x7F, 0xBA}


class NeverSendReceived(AssertionError):
    pass


def default_opt0():
    b = bytearray(64)
    b[0] = 0xD3
    b[9], b[10] = 0x01, 1                              # 1000 Hz, debounce 1
    b[24:32] = bytes.fromhex("03030400ffffff00")       # neon, speed 1, brightness 4, white
    b[40:48] = bytes([30, 0, 5, 0, 30, 0, 5, 0])       # sleep 30 s / deep 5 s, BT and 2.4G
    b[50] = b[51] = 100                                # X/Y sensitivity
    b[53] = 1                                          # angle snap
    return b


def default_opt1(profile, stage=4):
    b = bytearray(64)
    b[0], b[1], b[2], b[3] = 0xD4, profile, stage, 6
    for i, v in enumerate(DPI_STAGES):
        b[8 + 2 * i:10 + 2 * i] = v.to_bytes(2, "little")
        b[24 + 2 * i:26 + 2 * i] = v.to_bytes(2, "little")
    for i, c in enumerate(STAGE_COLORS):
        b[40 + 3 * i:43 + 3 * i] = c.to_bytes(3, "big")
    return b


class FakeMouse:
    def __init__(self, model_id=1833, profiles=8, mode="wired"):
        self.model_id = model_id
        self.mode = mode
        self.profile = 0
        self.matrix = {p: bytearray(FACTORY_MATRIX) for p in range(profiles)}
        self.opt1 = {p: default_opt1(p, 4 if p < 5 else 2) for p in range(profiles)}
        self.rate = {p: 1 for p in range(profiles)}
        self.opt0 = default_opt0()
        self.macros = {i: bytearray(b"\xff" * 256) for i in range(20)}
        self.macros[0][:10 + 4] = HI_MACRO + bytes(4)
        self.sent = []          # every frame received, without the report ID
        self.ignore = set()     # extra opcodes to accept without applying (for WriteNotApplied tests)
        self.reply = bytes(64)
        self.closed = False

    def _check(self, data):
        if len(data) != 65 or data[0] != 0:
            raise AssertionError("expected report ID 0 + 64 bytes, got %d bytes" % len(data))
        fr = bytes(data[1:])
        if fr[0] in NEVER_SEND:
            raise NeverSendReceived("never-send frame %s" % fr[:8].hex())
        if (sum(fr[:8]) & 0xFF) != 0xFF:
            raise AssertionError("bad checksum in %s" % fr[:8].hex())
        return fr

    def send_feature_report(self, data):
        fr = self._check(bytes(data))
        self.sent.append(fr)
        op = fr[0]
        r = bytearray(64)
        if self.mode == "dongle" or op in self.ignore:
            pass
        elif op == 0x8F:
            r[0], r[1], r[2] = 0x8F, self.model_id & 255, self.model_id >> 8
        elif op in (0xF1, 0x82):
            r[:8] = fr[:8]
        elif op == 0x85:
            r[0], r[1] = 0x85, self.profile
        elif op == 0x05:
            self.profile = fr[1]
        elif op == 0x84:
            r[0], r[1], r[2] = 0x84, fr[1], self.rate[fr[1]]
        elif op == 0x04:
            self.rate[fr[1]] = fr[2]
        elif op == 0xD0:
            r[:] = self.matrix[fr[1]]
        elif op == 0xD1:
            r[:] = FACTORY_MATRIX
        elif op == 0x50:
            self.matrix[fr[1]][4 * fr[2]:4 * fr[2] + 4] = fr[8:12]
        elif op == 0x51:
            pass
        elif op == 0xD3:
            r[:] = self.opt0
            r[8] = self.profile
        elif op == 0x53:
            charging = self.opt0[60]
            self.opt0[8:64] = fr[8:64]
            self.opt0[60] = charging
        elif op == 0xD4:
            r[:] = self.opt1[fr[1]]
        elif op == 0x54:
            b = self.opt1[fr[1]]
            b[2], b[3] = fr[2], fr[3]
            b[8:64] = fr[8:64]
        elif op == 0x96:
            r[:] = self.macros[fr[1]][64 * fr[2]:64 * fr[2] + 64]
        elif op == 0x16:
            idx, page = fr[1], fr[2]
            if page > 3:
                raise AssertionError("macro page %d: only pages 0..3 have been seen on hardware" % page)
            if page == 0:
                self.macros[idx][:224] = b"\xff" * 224
                self.macros[idx][224:] = MACRO_JUNK
            self.macros[idx][56 * page:56 * page + 56] = fr[8:64]
        self.reply = bytes(r)

    def get_feature_report(self, rid, n):
        return bytes([rid]) + self.reply

    def close(self):
        self.closed = True

    def frames(self, op):
        return [f for f in self.sent if f[0] == op]
