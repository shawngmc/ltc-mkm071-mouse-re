# LTC MKM071 (Rongyuan-family) mouse protocol — static analysis

Source: GearMaster 470.1.80 (`GearMaster_setup_470.1.80(WIN20241213).exe`,
sha256 `428f17b0ac3d780ad9c7e93988e199665646f40f05af5a1770ee2446a3bc290e`).

**Status legend**
- `[SW]`  read directly from GearMaster's JS (high confidence for layout, *not yet verified on hardware*)
- `[INF]` inferred from code structure (medium/low confidence)
- `[?]`   unknown / needs a capture or a device

Hardware tests (wired 3151:4026, 2.4G dongle 3151:4027, Bluetooth 3151:4028) were done on one physical MKM071
between 2026-10-14 and 2026-10-15. `[HW]` = verified on the physical mouse. Sections 2.x and 5.6 are measured;
sections 3-7 are the original static analysis, corrected where hardware disagreed. Section 0 is the current
state; where it disagrees with older text, section 0 wins.

---

## 0. Current state (2026-10-15)

**Works over USB (wired, vendor interface 2, 64-byte feature reports, libusb; `mkm071_write.py`, `mkm071_probe.py`):**
- Read: model id (0x8F), active profile (0x85), per-profile button matrix (0xD0), DPI block (0xD4), option block 0 (0xD3), report rate (0x84), macros (0x96).
- Write (all verified with read-back): active profile (0x05), button slots (0x50), DPI stages, active stage and stage colours (0x54), option block 0 fields (0x53: report rate, X/Y sensitivity, debounce, sleep, lift, angle snap), macros (0x16).
- Button actions: mouse button, keyboard key with modifiers, macro (repeat / touch toggle / hold), profile next/previous/cycle, DPI cycle/up/down.
- Macros: 20 slots, 1 ms delay units, key + mouse button + relative move events, up to 224 bytes with the 4x56-byte page protocol.
- Settings persist in flash across replug.

**Read-only / not available:**
- FN layer (0xD1) is a fixed default layout; writes (0x51) are accepted but not applied.
- Profile names are not stored in the mouse (GearMaster keeps them locally).
- Jump-to-profile ([8,0,4,n]) does nothing. Profile cycle visits only 0-2; `next` has no wrap or clamp (4 -> 5), `previous` stops at 0. Profiles 5-7 read as default-initialized; upper bound unknown.
- Lighting: the only light is the wheel LED, a status indicator (green charging pulse, connect flash) plus the DPI-stage colour. Stage colours are the one addressable lighting setting. The logo is paint; the dock is a passive pass-through with its own button.
- Battery: not available through the wired opcodes (0x82 echoes). Over Bluetooth it is the standard GATT Battery Service.

**Wireless:**
- 2.4G dongle: DPI-stage and profile changes arrive unsolicited as vendor input report 5 on the keyboard interface (hidraw); configuration needs dongle-specific framing that is still unknown.
- Bluetooth LE (HID over GATT): the vendor channel is HID report 6 (notify `66 0c <stage>`, `66 01 <profile>`, `66 0f 01/00`); passive reads and notifications work through BlueZ; nothing has been written to the vendor output characteristic.

**Open:** dongle/BT configuration framing and dongle battery (needs a GearMaster USB capture on Windows), sleep timer units and real effect, debounce/lift-off/angle-snap real effect, charging switch (write not applied), option-block flags 12-13, whether profiles 5+ are real storage.

**Safety rules used in this project:** only query opcodes plus gated, reversible writes (dry run by default, automatic backup, read-back check, `--restore`). Never send the bootloader frame, `BA FF`, `BA C0`, factory reset 0x01, or `upgradeOTAGATT`. `mkm071_write.py` backs up profiles 0-4 only (`MAX_PROFILE = 4`).

---

## 1. How GearMaster is built

| Layer | What it is |
|---|---|
| Installer | NSIS wrapper around a stored 75 MB 7z archive (7-Zip 23.01 cannot open the NSIS shell directly; the header is plain deflate and the 7z blob is the 5th data block) |
| UI | Electron app, unpacked in `resources/app`. Renderer bundle `dist/static/js/main_*.js` (unminified-ish webpack, 16 MB) |
| Bridge | `resources/app/iot_driver.exe`, a Rust program (tonic-web gRPC + hidapi). Listens on **`http://127.0.0.1:3814`** (gRPC-web, service `driver.DriverGrpc`) |
| Protocol | **Built in the renderer JS.** The UI hands the driver raw 64-byte buffers; the driver does the HID I/O and checksum |

The company/skin is `LTCFn` ("GearMaster"). The same driver family serves
Akko, MonsGeek, Epomaker, Attack Shark, Ajazz and others (2,600 device rows, most with VID `0x3151`),
so protocol notes for those vendors may apply, but this has not been checked against them.

### gRPC service `driver.DriverGrpc`
| RPC | Kind | Request → Response | Notes |
|---|---|---|---|
| `watchDevList` | server stream | Empty → `DeviceList` | `Device{devtype,is24,path,id,battery,isonline,vid,pid}` — **battery and online state are supplied by the driver** |
| `watchVender` | server stream | Empty → `VenderMsg{msg}` | Unsolicited input reports (button/DPI/profile events, §6) |
| `watchSystemInfo` | server stream | Empty → `SystemInfo` | |
| `sendMsg` / `readMsg` | unary | `SendMsg{devicepath,msg,checksumtype,dangledevtype}` / `ReadMsg{devicepath}` | Normal config path |
| `sendRawFeature` / `readRawFeature` | unary | same | Used for the `0xF1` ID query and `0xFE` length preamble |
| `upgradeOTAGATT` | server stream | `OTAUpgrade` → `Progress` | **Firmware update. Never call.** |
| `insertDb`, `getItemFromDb`, … | unary | | App-side key/value store |
| `cleanDev`, `setLightType`, `changeWirelessLoopStatus`, `getVersion`, mic/weather RPCs | | | Not relevant |

Enums: `CheckSumType{BIT7=0,BIT8=1,NONE=2}`, `DangleDevType{NONE=0,KEYBOARD=1,MOUSE=2}`,
`DeviceType{YZWKEYBOARD=0,YZWBOOT=1,YZWVENDER=2}`.
`BIT8` is never used by the UI; `BIT7` is used 108 times.

Open: the exact difference between `sendMsg` and `sendRawFeature` inside the Rust driver `[?]`.
The UI uses `sendMsg` for ordinary settings and `sendRawFeature` for `0xF1` and `0xFE`.

---

## 2. Identifying the device

Device table (in the renderer bundle) rows for LTC (`company:"LTCFn"`, group `rongyuan_k_rgb`):

| Model | id | Wired (VID:PID) | 2.4G dongle (VID:PID) | Sensor class from internal name |
|---|---|---|---|---|
| **MKM071** | **1833** (0x0729) | 3151:**4026** | 3151:**5008** (dongle id −2147485481) | `g29_yj01z_3104` (PAW3104-class) |
| GM025 | 1847 (0x0737) | 3151:4026 | 3151:4027 | `g51_gm025_3311` (PAW3311) |
| GM024 | 1975 | 3151:4026 | 3151:5008 | `g59_gm024_3104` |
| GM023 | 1829 | 3151:4026 | 3151:5008 | `g32_yj01z_3104` |
| GW24 | 1771 | 3151:4026 | 3151:402d | `g59` |

- All mice: HID usage page `0xFFFF`, usage `2`, **65-byte feature report** (report ID 0 + 64 bytes).
- Negative table id = dongle variant: `dongle_id = −(0x80000000 + id)`.
- **Wired PID `0x4026` is shared by every model above**, so VID:PID does not identify the model.
  Use the `0xF1` query (§3.3).
- **Discrepancy to resolve on hardware:** the retail listing says *PAW3311, 6 DPI stages up to 12000, RGB dock*.
  That matches the **GM025 (id 1847)** profile (RGB, 12000 default top stage, 24000 max) better than the
  **MKM071 (id 1833)** profile (no RGB in software, max 8000). The `0xF1` reply will say which firmware you have.

---

### 2.1 Measured: 2.4G dongle `3151:4027` (hardware, 2026-10-14)

| Interface | Class | Report descriptor | Linux node |
|---|---|---|---|
| 0 | boot mouse | 71 B: 5 buttons, X/Y 16-bit, wheel, pan; **no report ID** | hidraw |
| 1 | boot keyboard + extras | 120 B: consumer (id 3), system control (id 2), keyboard (id 7), **vendor `FFFF/1` input id 5, 3 bytes** | hidraw |
| 2 | non-boot HID | 20 B: **vendor `FFFF/2`, one 64-byte feature report, no report ID** | **hiddev only, no hidraw** |

- Interface 2 is the command channel (matches the device table: usage `2`, page `0xFFFF`, 65-byte feature report incl. ID byte).
- Interface 1 vendor input (id 5, 3 bytes) is the unsolicited-event channel (§6): `t0,t1,t2` = the 3 data bytes.
- Only 5 buttons are in the mouse report; the other side buttons must reach the host as keyboard/consumer codes on interface 1.
- hidapi's hidraw backend cannot see interface 2. Use libusb control transfers: `SET_REPORT` (`0x21/0x09`) and `GET_REPORT`
  (`0xA1/0x01`), `wValue = 0x0300`, `wIndex = 2`, 64 bytes.
- **Wired-style query frames (§3) returned 64 zero bytes over the dongle** for all opcodes tried (`F1`, `8F`, `84`, `D3`, `D4`, `D0`),
  with a 10 ms delay. Transfers were accepted. Likely cause: dongle mode uses different framing.

**Dongle framing hints from the JS `[SW]`**: when `is24`, GearMaster first sends raw `[0xFE, n]` (n = length of the next relayed packet,
no checksum), and several 2.4G frames use a **1-byte header (opcode only, no checksum)** instead of the 8-byte checksummed header.
The Rust driver has a dongle module with `is_online`, `is_write_finish`, paired `keyboard_id` / `mouse_id` status; the exact
relay handshake is not in the JS `[?]`. Wired mode (`3151:4026`) is expected to use the plain §3 framing.

## 3. Transport and framing

### 3.1 Report shape `[SW]`
All traffic is 64-byte feature reports (65 with the leading report-ID byte `0x00`).
Layout of a command: an 8-byte header, then 56 bytes of payload.

```
byte 0      opcode
bytes 1-6   command arguments (profile index, sub-index, length, page ...)
byte 7      checksum (BIT7)
bytes 8-63  payload
```

### 3.2 Checksum (BIT7) `[SW]`
```
byte7 = 0xFF - (sum(byte0 .. byte6) & 0xFF)
```
Seen verbatim in `_setKeyConfig`; the driver applies it automatically for `CheckSumType.BIT7`.
Read requests are sent as 8-byte buffers (zero-padded to 64 by the driver) with the same checksum.

### 3.3 Request/response pattern `[SW]`
1. `Set Feature` with the command.
2. Wait ~5–10 ms (the UI waits 10 ms before write, 5 ms before read; **60 ms extra for Bluetooth**).
3. `Get Feature`; reply byte 0 echoes the opcode.

**Reply layouts differ per command** `[SW]`: option blocks (`0xD3`/`0xD4`) echo the header layout (payload fields at the
same offsets as the SET buffer, e.g. profile at byte 8), whereas the button matrix (`0xD0`) and macro (`0x96`) replies are
parsed from **byte 0** with no header (the UI concatenates raw 64-byte replies). Check each one against a capture.

**Device ID query** (raw feature, BIT7): send `[0xF1]`, wait 10 ms, read; require `reply[0] == 0xF1`;
`device_id = reply[2] << 8 | reply[1]`.

**Dongle relay** `[INF]`: for 2.4G, the UI sends `[0xFE, n]` (raw, no checksum) to set the next packet length,
and passes `DangleDevType.MOUSE`. Behaviour unverified.

---

## 4. Opcode table (mouse-relevant)

Set opcodes are in the low range; the matching Get is `Set | 0x80` for most pairs.

| Function | SET | GET | Notes |
|---|---|---|---|
| Firmware version | – | `0x8F` | `version = reply[1] | reply[2]<<8` `[SW]` |
| Factory reset | `0x01` | – | Resets to profile 0. **Destructive** |
| Report rate | `0x04` | `0x84` | §5.2; `[HW]` correction: the mouse class uses 0x04/0x84 (byte 1 = profile, reply byte 2 = code); writes were verified through option block 0 byte 9 |
| Active profile | `0x05` | `0x85` | §5.3; `[HW]` verified (reply byte 1) |
| Debounce | `0x06` | `0x86` | Folded into option block 0 for mice (byte 10); standalone opcode exists `[INF]` |
| Sleep time | `0x11` | `0x91` | Folded into option block 0 (bytes 40–47) `[INF]` |
| Light parameters | `0x07` | `0x87` | 7-byte light record, §5.1 |
| **Mouse option block 0** | `0x53` | `0xD3` | Profile, rate, debounce, flags, light, sleep, sensitivities, battery colors — §5.1 |
| **Mouse option block 1** | `0x54` | `0xD4` | DPI table, current stage, DPI colors — §5.4 |
| Button matrix (full) | `0x0A` | `0x8A` | 16 slots × 4 bytes, 2 pages — §5.5 |
| Button matrix (single / "simple") | `0x50` | `0xD0` | Set one slot — §5.5 |
| FN-layer matrix | `0x51` (mouse) / `0x10` | `0xD1` / `0x90` | |
| Macro (simple) | `0x16` | `0x96` | §5.6 |
| Battery (keyboards) | – | `0x82` | UI uses it for `keyboard3123` only; mice get battery via the driver `[?]` |
| Low-battery threshold | `0x02` | – | keyboards `[INF]` |

Out-of-scope opcodes present in the same table (OLED/TFT screen, magnetic switches, EQ, sound, "gun" recoil)
exist for other products and are ignored here.

### Never send `[SW]`
| Bytes | Effect |
|---|---|
| `7F 55 AA 55 AA 00 00 82` | `FEA_CMD_SET_BOOTLOATER` — enters bootloader |
| `BA FF`, `BA C0 …` | Bootloader/firmware transfer commands |
| `0x01` | Factory reset (only deliberately, after a backup) |
| `upgradeOTAGATT` RPC | OTA update |

---

## 5. Payload layouts

### 5.1 Option block 0 — `SET 0x53` / `GET 0xD3` `[SW]`
`GET` is an 8-byte request `[0xD3, 0,0,0,0,0,0, chk]`; the reply is laid out like the `SET` buffer.

| Offset | Field | Encoding |
|---|---|---|
| 0 | opcode | `0x53` |
| 7 | checksum | BIT7 |
| 8 | profile index | u8 (also read back as the active profile) |
| 9 | report-rate code | §5.2 |
| 10 | debounce | u8 (UI default 10; labelled `deBounce`, units unconfirmed — likely ms) |
| 11 | reserved | 0 |
| 12–13 | flags | u16 LE: bit0 `lightOff`, bit1 `wheelLightOff`, bit2 `smooth`, bit3 `ledSelect`, bit4 `powerSaveMode` |
| 14 | `buttonChange` | u8 (UI default 1) |
| 15 | `wheelToButton` | u8 (default 10) |
| 16 | `buttonToWheel` | u8 (default 10) |
| 24–31 | main light record | 8 bytes, below |
| 32–39 | logo light record | all zero on the generic class |
| 40–47 | sleep times | 4 × u16 LE: `time_bt`, `deepTime_bt`, `time_24`, `deepTime_24` (UI clamps to 60 × min…max, i.e. seconds from minutes) |
| 50 | X sensitivity | u8, default 100 |
| 51 | Y sensitivity | u8, default 100 |
| 52 | lift-off cutoff | u8 |
| 53 | angle snap | u8 (0 = off) |
| 54–59 | 2 × RGB | battery/charging indicator colors |
| 60 | charging switch | 0/1 |

**Light record (8 bytes)**: `[type, 4−speed, brightness, flags, R, G, B, 0]`

| type | effect |
|---|---|
| 0 off · 1 always-on · 2 breathe · 3 neon · 4 wave · 5 dazzling · 6 laser · 7 music-follow · 8 screen-color · 9 music-follow-2 | |

`flags` = `0x08` (dazzle/multicolor) or `0x07` (single color); wave direction in the high nibble
(`right 0, left 1, down 2, up 3`). Speed is stored inverted (`4 − speed`). The software lists a `LightSnake`
type for GM025 that has **no encoder** in the JS — treat as unmapped.

### 5.2 Report rate — `SET 0x04` / `GET 0x84` `[HW]` (corrected from 0x03/0x83)
`SET`: `[0x04, profile, code, …]` (BIT7). `GET`: reply byte 2 (per profile). Writes through option block 0 byte 9 verified.

| Hz | 1000 | 500 | 250 | 125 |
|---|---|---|---|---|
| code | `0x01` | `0x02` | `0x04` | `0x08` |

Option block 0 uses the same codes, plus `0x81`/`0x82`/`0x84` for 8000/4000/2000 Hz (not offered for this mouse).
The MKM071 table row exposes 125/250/500/1000 only.

### 5.3 Profile — `SET 0x05` / `GET 0x85` `[HW]` (corrected from 0x04/0x84)
`SET`: `[0x05, index, …]`. `GET`: reply byte 1. Verified. The listing says 5 profiles (0-4); the firmware accepts at least 0-7 (default-initialized beyond 4).
After a profile change GearMaster re-sends option blocks 1 then 0 (~200 ms apart).

### 5.4 Option block 1 (DPI) — `SET 0x54` / `GET 0xD4` `[SW]`
| Offset | Field |
|---|---|
| 1 | profile index |
| 2 | current DPI stage |
| 3 | stage count |
| 8–23 | 8 × u16 LE DPI values (X) |
| 24–39 | 8 × u16 LE DPI values (Y); with `banXY` the UI writes the same table twice |
| 40–63 | 8 × RGB (3 bytes each) stage indicator colors |

DPI values are written as plain integers in 50-DPI steps (`[INF]`: the UI passes raw DPI, no scaling visible).
A value of `0` marks an unused stage. MKM071 row: 6 stages, 50–8000, step 50, defaults
`800/1600/2400/3200/4000/4800`, default stage index 1.

### 5.5 Button matrix `[SW]`
16 slots × 4 bytes = 64 bytes. Written in 2 pages of 56 bytes:
`header = [0x0A, profile, 0x40, 0x00, page, 0, 0, chk]`, then 56 bytes; `0x50` writes one slot:
`[0x50, profile, slot, 0,0,0,0, chk] + [4-byte action @ byte 8]`.

Slot encodings (4 bytes):

| Bytes | Meaning |
|---|---|
| `00 00 00 00` | disabled |
| `00 mods key key2` | keyboard: modifier bit-code (`alt E2`, `ctrl E0`, `shift E1`, `win E3`) + HID usage(s) |
| `01 00 F0 00` / `F1` / `F2` | left / right / middle |
| `01 00 F3 00` / `F4` | back / forward |
| `01 00 F5..F8 00` | wheel left / right / forward / back |
| `01 00 F5 01`, `01 00 F5 FF` | scroll up / down |
| `14 00 00 00` | DPI cycle |
| `09 mode idx 00` | macro `idx`; mode 0 = repeat-N, 1 = touch/toggle, 2 = hold (verified on hardware; GearMaster's "on_off" name is misleading) |
| `03/06/08/0A/0B/0D/0E/12/13/14/16/02 …` | media/system functions (table `WA` in the JS) |

Generic default matrix (used until the device is read): slots 0–4 = L, R, M, Forward, Back; slot 5 = `0A 03 00 00`;
slot 6 = DPI; slots 12–15 = scroll/tilt. **The MKM071's real slot-to-button map (11 buttons, 7 side) must be read from the device.**

### 5.6 Macros `[HW]` (wired MKM071, 2026-10-14)
Mouse class opcodes: SET `0x16` (SET_MACRO_SIMPLE) / GET `0x96`. `0x0B`/`0x8B` are the old full-size pair and are not used.
Assignment: matrix slot `[9, mode, macro_idx, 0]`; mode 0 = repeat N times (N = macro repeat count), 1 = touch/toggle (tap to start repeating, tap again to stop), 2 = hold (repeats while the key is held, stops on release). Both verified with evdev logs (200 ms macro): mode 1 repeated ~1.85 s then stopped on the second tap; mode 2 repeated while held. In both, stopping mid-cycle releases the key immediately (truncated key-down: 13-42 ms instead of 100 ms) - no stuck key, the cycle is cut rather than finished.
Storage: 20 slots (`MACROMAX`), 256 bytes each.

- **Read** `[0x96, idx, page]`, page 0..3: each reply is 64 raw bytes (no opcode echo); concatenate the 4 replies for the 256-byte buffer. An empty slot reads all zero. A slot that was never written reads all `0xFF` (erased flash); after a write the bytes behind the terminator also read `0xFF`, which is why an empty slot reads zeros followed by `FF`.
- **Write** `[0x16, idx, page, total_len, last_flag, 0, 0, chk]` + 56 data bytes per page; pages are consecutive 56-byte slices of the buffer, only up to the last page with non-zero data; `total_len` = bytes used; `last_flag` = 1 on the final page; BIT7 checksum. Clearing a macro = one page of zeros with `total_len` 0. The mouse acknowledges but the ack carries no data.
- **Buffer**: `u16 LE repeat`, then events; four zero bytes end the macro. Event = key/button usage byte, then a flag byte with bit 7 = down, low 7 bits = short delay (1..127). If the short delay is 0 the event takes 4 bytes: `[usage][flags][delay u16 LE]`. Short-delay events are 2 bytes. `0xF9` = relative move: `[F9][delay][dx i8][dy i8]`. Mouse buttons use the matrix codes `F0..F4`. Modifier keys are plain usages (shift 0xE1).
- Verified: "hi" (`01 00 0b 8a 0b 0a 0c 8a 0c 0a`: repeat 1, h down 10, h up 10, i down 10, i up 10) written to slot 0 and assigned to thumb key 4 as `[9,0,0,0]` typed "hi". Delay unit = 1 ms, verified: `down:a,delay:100,up:a,delay:1000,down:b,delay:5000,up:b` measured 0.100 s / 1.000 s / 5.000 s between key events (short 1-byte form and 4-byte u16 form both exact, timing jitter < 1 ms at evdev). Mouse events in macros verified (2026-10-15): `move:20:0,delay:200,move:0:20,delay:200,mdown:forward,delay:300,mup:forward,delay:200,click:back` produced evdev dx=+20 exactly, then 200 ms later dy=**-20** (the dy byte is inverted vs evdev: a positive byte moves the cursor UP, so use a negative dy for down), then BTN_EXTRA (0xF4 = forward) down 200 ms later and up 300 ms after that, then BTN_SIDE (0xF3 = back) down 200 ms later and up 10 ms after. All gaps exact to ~1 ms; a move event with delay 0 takes the same trailing u16 delay form as keys. Moves are exact (no pointer-acceleration scaling at evdev).
- Multi-page macro verified (2026-10-15): `text:the quick brown fox jumps over the lazy dog` at 5 ms = 174 bytes / 4 pages typed correctly on key press and read back identically (96 events, used_bytes 174). Bytes 224..255 of the 256-byte slot are never written (4 pages x 56 = 224) and read back as stale junk (`ff 22 09 02 12 ba ...`); the parser stops at the 4-zero terminator and ignores them. Slot capacity is therefore limited by the page protocol to 224 bytes of content in this tool (GearMaster's limit is 248).

---

## 6. Unsolicited events (`watchVender`) `[SW]`
Input report bytes `[1..3] = t0,t1,t2`:

| t0 | t1 | t2 | Event |
|---|---|---|---|
| 1 | | | profile switched |
| 2 | | 0 | report rate switched |
| 12 | stage | 0 | **DPI stage switched** (t1 = new index) |
| 3 | | 9 / 4 | power-save mode toggled |
| 4–7 / 8–11 | | | light effect / side-light effect switched |
| 13 | 0 | 0 | reset |
| 19 | 0 | 0 | sleep toggle |
| 15 | 1/0 | 0 | start/stop of an internal sequence (music-light) |

---

## 7. Capability matrix for the MKM071 row (id 1833), as GearMaster exposes it

| Capability | Read | Write | Via | Hardware status |
|---|---|---|---|---|
| Device ID | yes | – | `0xF1` / `0x8F` | verified (1833) |
| Firmware version | yes | – | `0x8F` | verified |
| Active profile | yes | yes | `0x85` / `0x05` | verified |
| Report rate 125–1000 | yes | yes | block 0 byte 9 | verified (125 Hz measured) |
| DPI table, 6 stages | yes | yes | `0xD4`/`0x54` | verified |
| DPI stage colors | yes | yes | block 1 | verified (visible on wheel LED) |
| Current DPI stage | yes | yes | block 1 + event `12` | verified |
| Debounce | yes | yes | block 0 byte 10 | writes; effect not measurable |
| Sleep (2.4G + BT) | yes | yes | block 0 bytes 40–47 | writes; units/effect unverified |
| Lift-off, angle snap | yes | yes | block 0 bytes 52/53 | writes; no observed effect |
| X/Y sensitivity | yes | yes | block 0 bytes 50/51 | X verified 0.53x; Y consistent with 0.5x |
| Button remap (16 slots) | yes | yes | `0xD0`/`0x50` | verified |
| FN layer | fixed default | no | `0xD1`/`0x51` | read-only |
| Macros | yes | yes | `0x96`/`0x16` | verified |
| RGB lighting | – | stage colours only | block 1 | wheel LED = status + DPI stage colour |
| Battery level | Bluetooth only | – | GATT Battery Service | wired opcodes do not provide it; dongle unknown |

GM025 (id 1847) additionally has RGB effects (off/always-on/breathe/wave/neon/snake), 24000 max DPI,
12000-DPI default top stage.

---

## 8. Open questions (status 2026-10-15)
Resolved on hardware: id is 1833 (no RGB); 64-byte feature reports, report ID 0, vendor interface 2; profiles (5 listed, firmware accepts 0-7+, names not stored); DPI units are the values shown in the UI; Bluetooth exposes the vendor channel as HID report 6 with no hidraw node.
Still open:
1. Dongle configuration framing (`dangledevtype`, `[0xFE, n]` and wrapper bytes): needs a GearMaster USB capture on Windows or disassembly of iot_driver.exe.
2. Battery source on the dongle (driver `Status24.battery`); Bluetooth battery works via GATT.
3. Sleep timer units and effect; debounce, lift-off and angle-snap real effects; flags 12-13; charging switch (byte 60, write not applied).
4. Bluetooth vendor output characteristic framing (leading `0x66`); whether `66 0f 01/00` is an LED indicator on/off.
5. Whether profiles 5+ are real storage; upper bound of `next`.

## 2.2 Hardware-verified (wired 3151:4026, libusb iface 2, report ID 0, 2026-10-14)
- Framing confirmed: 64-byte feature report, byte7 = 0xFF - sum(b0..b6). SET_REPORT, ~10 ms, GET_REPORT.
- 0x8F reply = device-table model ID (1833 = MKM071), NOT a firmware version.
- 0xF1 on the application firmware only echoes the request; do not use for identity.
- 0x84 profile -> 0. 0xD3 (opt0): rate 1000, debounce 1, sleep 30 s / 5 s, sensitivity 100/100, light = neon white (RGB present despite table isRgb:false).
- 0xD4 (opt1): 6 stages 800/2400/3200/6400/9600/12000, active stage index 4, per-stage colors. Max DPI 12000 (table says 8000).
- 0xD0 matrix reply has NO opcode echo; slots start at byte 0, 4 bytes each:
  [01 00 F0 00] left, [08 00 03 00] unknown fn (type 8, val 3), [01 00 F2 00] middle, [14 00 00 00] DPI cycle. Slot order is not the generic default. [INF]
- Dongle (3151:4027) wired-style frames return zeros; dongle framing still unknown.
- One 0xD0 read returns the WHOLE matrix (16 slots); GearMaster reads it once. So the mouse has no further pages.
- Slot [8,0,3,0] = profile_cycle (type 8 = profile fn: [8,0,0..3,0] value/up/down/cycle, [8,0,4,n] exchange). Type 20 = DPI fn ([20,0,0|1|2|4,0] cycle/up/down/shift).
- Generic default matrix in GearMaster: L,R,M,Fwd,Back,[10,3,0,0],DPI,0x5,wheel F9 +1/-1, F5 +1/-1. MKM071 slot order differs (right click is slot 9) - physical mapping needs button-press testing.
- Battery: driver proto Device.battery / Status24.battery come from the Rust driver, not from the mouse JS class (getBattery is a stub for mice). 0x82 is the keyboard query; on the wired mouse it only echoes (next line).
- **Profile `next` does not wrap or clamp at 4 (2026-10-15, wired)**: with slot 11 = `[8,0,1,0]` and the active profile set to 4, one press moved the active profile (query 0x85) to **5**, the sixth index, and it stayed there. In profile 5 the thumb key then typed `4` (its own, default, slot-11 mapping). So the profile index is not limited to 0..4 by the firmware's `next` action; how far it goes and what profiles 5+ contain is still unmapped. `--set-profile` (0x05) is the way back.
- **Profiles 5-7 exist as default-initialized profiles; `previous` clamps at 0 (2026-10-15, wired)**: 0xD0/0xD4/0x84 for profiles 5, 6, 7 return valid data (default layout, DPI 800/2400/3200/6400/9600/12000, current stage 2, same stage colours, 1000 Hz). Unknown whether they are real flash storage or a template served for any index; upper bound unknown. `previous` (`[8,0,2,0]`, slot 10) from profile 2 gave 2 -> 1 -> 0 (profile_watch.py, ~1.4 s between presses) and stayed at 0, so it does not wrap to the top. `next` is asymmetric: no wrap, no clamp at 4. Practical consequence: the "5 profiles" limit is software-side (GearMaster); a stray `next` can strand the mouse in profile 5+, where it is recoverable with `--set-profile 0` or the profile-cycle button (visits only 0-2).
- **FN-layer matrix `0xD1` (2026-10-15, wired)**: returns the same factory-default layout (`0100f000 08000300 0100f200 14000000 0100f400 0100f300 0100f200 00001e00 00001f00 0100f100 00002000 00002100 0100f901 0100f9ff 0100f501 0100f5ff`) for every profile argument 0..4, even when that profile's base matrix is customised (profiles 1 and 2). Changing profile 0 slot 14 in the base matrix (0x50) did not change `0xD1`, so it is not an alias of profile 0 or of the active profile. A `0x51` write to slot 14 was accepted without error but changed neither matrix (NOT APPLIED). Conclusion: on this unit `0xD1` is a read-only default layout; there is no editable FN layer, and no button is assigned to switch to one.
- Wired test (2026-10-14): 0x82 battery reply = echo of the request (82 00.. 7d), i.e. NOT supported on the wired mouse firmware. Superseded: the 0xD1 finding above (fixed default layout, not an alias) is final.

## 2.3 Write path verified (wired, 2026-10-14)
- 0x50 set-slot works: frame [0x50, profile, slot, 0,0,0,0, chk] + action[4] at bytes 8..11, BIT7 checksum, SET_REPORT on iface 2 via libusb. Slot 7 -> [0,0,34,0] made thumb key 1 emit KEY_5; restore returned KEY_1. Read-back via 0xD0 matched. Takes effect immediately; persists across replug (below).
- Slot map (MKM071, 16 slots): 0 left, 1 profile_cycle, 2 standalone side button (default middle; typed '=' in profile 1), 3 dpi_cycle, 4 forward (BTN_EXTRA), 5 back (BTN_SIDE), 6 wheel click (middle), 7/8/10/11 thumb keys 1-4 (KEY_1..4), 9 right, 12/13 wheel up/down (F9 +1/-1), 14/15 F5 +-1 (no tilt on this mouse; unused).
- evtest does not show DPI/profile buttons; they are firmware-internal.
- Persistence: slot write survived unplug/replug (stored in flash). Restore also worked.
- DPI write frame (from GearMaster setMouseOption1; later verified on hardware): [0x54, profile, current_stage, stage_count, 0,0,0, chk] + 56 bytes copied from the 0xD4 read (8 X u16, 8 Y u16, 8 RGB). Colours must be echoed or they are blanked. mkm071_write.py --set-dpi STAGE,DPI does a read-modify-write.
- Option block 0 write (0x53) verified: light record (bytes 24..31) rewritten and read back OK; restored OK. No visible effect: the wheel LED is not driven by that record (GearMaster also marks MKM071 as no-RGB). Original record: 03 03 04 00 FF FF FF 00 (neon, speed 1, brightness 4).
- The LED below the wheel shows the DPI stage colour (opt1 colours, bytes 40..63 of 0xD4). The DPI button (slot 3) cycles current_stage in hardware; 0xD4 byte 2 reflects it live (stage 3 -> 5 after two presses). So active stage is readable, and stage colours/active stage are the "RGB" feature. Writing them (mkm071_write.py --set-stage-color / --set-stage) was verified later: a stage colour set to orange showed on the wheel LED.

## 2.4 Opcode correction (mouse class, 2026-10-14)
- GearMaster's mouse class uses: GET_INFOR 0x8F (model id), GET_REV 0x80, SET_REPORT 0x04 / GET_REPORT 0x84 (rate; byte1 = profile asked about), SET_PROFILE 0x05 [5, n] / GET_PROFILE 0x85 (current profile in reply byte 1), matrix 0x50/0xD0, option blocks 0x53/0xD3 and 0x54/0xD4.
- Earlier probe versions read the "profile" with 0x84; that only echoed the requested profile (always 0). The real current profile is also in option block 0 byte 8. Fixed in probe: OP_GET_PROFILE = 0x85.
- A second, keyboard-class enum exists in the JS (SET_REPORT 1, PROFILE 2/0x82, BATTERY 0x83); the mouse class has no battery opcode.

## 2.5 Profiles (wired, 2026-10-14)
- Five profiles (indexes 0-4) read back from 0xD0 (matrix) and 0xD4 (DPI block, byte 1 = profile index). Each has its own 16-slot matrix and DPI block; 0xD0/0xD4 take the profile in byte 1 and do not change the active profile.
- Report rate is per profile too: 0x84 with byte 1 = profile returns the rate code in byte 2 (1 = 1000 Hz on all five).
- On this unit profiles 0, 3 and 4 hold the default layout; profile 1 remaps the thumb keys and others to number-row keys (HID 0x23-0x2E); profile 2 holds z/1/2/3/4-style keys, q and space. Slot 1 is profile_cycle in every profile.
- DPI tables, stage colours and active stage read identical in all five profiles.
- Writes can target a profile without switching it: 0x50 and 0x54 carry the profile in byte 1. Switching is 0x05 [5, n], confirmed by reading 0x85.
- mkm071_write.py backups contain profiles 0-4 only (profiles 5-7 are not backed up); --restore writes back every profile that differs, and --profile N limits an edit or restore to one profile.

## 2.6 Profile switching from buttons (wired, 2026-10-14)
- Hardware profile_cycle ([8,0,3,0]) cycles profiles 0, 1, 2 only, then wraps to 0, although profiles 3 and 4 exist and can be selected with 0x05.
- Slot 2 (standalone side button) set to [8,0,3,0] switched the profile: type-8 functions work from any slot.
- [8,0,4,4] ("exchange 5" / jump to profile 4 per GearMaster's table) did nothing when pressed. Jump-to-profile is not supported by this firmware, or uses different bytes.
- Slot map correction: slot 2 is the standalone side button, slot 6 is the wheel click.
- [8,0,1,0] ("next") from profile 2 reached profile 3, so Next is not limited to the 0-2 loop that cycle uses. Profile switching from any button works with cycle, next and previous; jump does not. `next` has no wrap or clamp (4 -> 5) and `previous` stops at 0 (see the 2026-10-15 profile note in 2.2).

## 2.7 Option block 0 writes (wired, 2026-10-14)
Writer: `mkm071_write.py --set-opt0 FIELD=VAL,...` (read-modify-write of the 0xD3 reply, header 0x53, BIT7 checksum; every other byte echoed). `rate_check.py` counts evdev packets/counts to observe effects. All writes read back OK and `--restore` returns the original block.

| Field (byte) | Result |
|---|---|
| Report rate (9: 1=1000, 2=500, 4=250, 8=125 Hz) | **Verified.** rate=125 measured 125 packets/s (8 ms gap); restore returned 1000 Hz. 0x04 per-profile path not needed, untested. |
| X sensitivity (50) | **Verified.** 100 -> 50 cut total \|dx\| 151018 -> 80194 (~0.53x); \|dy\| unaffected. Y (51): see the note below (consistent with 0.5x). |
| Sleep timers (40/44 u16 LE; 42/46 deep) | Writes and reads back (30 -> 60 and back). Units and real effect unverified. |
| Debounce (10) | Writes and reads back (1 -> 6 and back). Effect not measurable by us; clicks still normal. |
| Lift cutoff (52) | Writes and reads back; **no observed effect** at 3-5 mm by hand or on 3 sheets of paper (lift 0 and 3); cardboard gave no events at any value (untrackable surface). Valid range 0-3 is a guess. |
| Angle snap (53) | Writes and reads back; **no observed effect** (user repeated slow tilted line tests, |dy| unchanged). Value was already 1 before the test. |
| Flags 12-13, battery colours 54-59, charging switch 60 | Untested. |

Behaviour: all fields persist in the mouse (flash) like other settings. No tested value caused a disconnect.

- Y sensitivity (byte 51) written to 50: |dy| per second came out at about 0.5x the average of two baselines (baselines differed ~40% from hand speed), consistent with a 0.5x scale; not isolated from hand-speed variation. Restored to 100.

## 2.8 Dongle 3151:4027 findings (2026-10-14)
- Three HID interfaces, laid out like the wired mouse. If 0: boot mouse (ep 0x81), report `[buttons][X s16][Y s16][wheel][pan]`, 7 bytes, no report ID. If 1: composite keyboard/consumer/vendor (ep 0x82): ID 3 consumer, ID 2 system, ID 7 keyboard (thumb keys arrive as ID 7 reports, e.g. `07 00 00 1e ...`), ID 5 vendor input (24 bits, page 0xFFFF usage 1; not seen yet, presumably the DPI/profile press report). If 2: vendor page 0xFFFF usage 2, one 64-byte feature report, ID 0, no input report. ep 0x83 exists but stayed silent for 5 s.
- Wired-style frames (including 0x8F) via SET_REPORT/GET_REPORT on If 2 returned all zeros at waits of 10 ms, 50 ms, 300 ms, 500 ms and 1 s, with and without reading ep 0x83. So the dongle needs different framing or a prior step.
- GearMaster's driver (iot_driver.exe, source path `dj_dev_api/dangle_common.rs`) lists 3151:4027 with `dongle_common = true` (usage 2, page 0xFFFF, interface 2). It keeps a `DangleCommonStatus` {is_online, is_write_finish, keyboard_status, keyboard_id, mouse_status, mouse_id} and a `Status24` {is_ready_read, battery}. So writes on the dongle are followed by a poll of is_write_finish, and reads wait for is_ready_read; battery comes from this status.
- The renderer sends `SendMsg {devicepath, msg, checksumtype, dangledevtype}` with dangledevtype 0 = wired, 1 = keyboard behind the dongle, 2 = mouse behind the dongle, so the driver wraps the 64-byte frame for the dongle. There is also a 2.4G-only frame `[0xFE, len]` ("set next pack length") sent before some writes. The wrapper bytes are not visible in the JS; they need a USB capture of GearMaster on Windows or disassembly of iot_driver.exe.
- Not done: any write to the dongle. Reads were only the standard 0x8F query.

**Dock (2026-10-15):** `lsusb -t` with the dongle in the dock shows no new USB device, and docking produced no events, so the dock is a passive pass-through (power tap plus its own button and LEDs). Its lighting is not controllable from Linux and is not part of the protocol.

**Lighting (2026-10-15, user observation):** the only light on the mouse is the wheel (the logo is opaque paint). In wired mode it pulses green while charging; on 2.4G it flashes green once on connect and then stays off. It behaves as a fixed status indicator, so the light record (opt0 bytes 24..31, no visible effect), logo light (32..39), light_off / wheel_light_off flags and battery colors (54..59) are likely unused or hardware-dependent on this model. DPI stage colors DO show: the wheel light shows the stage color when the DPI changes (user observation), so the wheel is a status indicator plus a DPI-stage indicator, and the stage colors (opt1) are the one lighting setting that is addressable.

## 2.9 Vendor input report 5 (dongle, 2026-10-14)
- The keyboard interface of the dongle (If 1, hidraw) delivers a 4-byte vendor input report `05 <type> <value> 00` when the DPI or profile button is pressed. Each event is followed by `05 0F 00 00` (end/ack).
  - type `0x0C`: DPI stage. value = new stage index (`05 0c 00 00`, `05 0c 01 00`, `05 0c 02 00` for three presses, stages 0,1,2).
  - type `0x01`: profile. value = new profile index (`05 01 02 00`, `05 01 00 00`, `05 01 01 00`: profile 2, then 0, then 1).
  - These arrive unsolicited and need no command to the dongle, so a Linux tool can follow live DPI stage and profile changes over 2.4G just by reading hidraw.
- No battery or online/offline event appeared in a 20 s listen with DPI and profile presses only. Not tried: turning the mouse off and on, the dock, long idle.
- Asking for input report 5 (HIDIOCGINPUT) returns zeros. Sending `[FE 08]` (NONE checksum, as GearMaster does before some writes) and then the 0x8F query still returned all zeros at 20 ms to 1 s.
- Negative result: switching the mouse off, switching it from USB to 2.4G, and placing it on or removing it from the dock produced no report 5 events (or any other traffic on that interface). So battery and online state are not pushed on report 5; if the dongle exposes them they must be requested through the vendor feature channel (If 2), whose framing is still unknown.

## 2.10 Bluetooth identity (2026-10-15, bt_ids.json)

- **Bluetooth LE HID over GATT**, public name `MKM071`, random address, appearance 0x03C2 (mouse), Modalias `usb:v3151p4028d0100`. So the PIDs are 4026 wired, 4027 2.4G dongle, 4028 Bluetooth. Services: Generic Access, Generic Attribute, Device Information (0x180A), **Battery Service (0x180F)**, HID (0x1812). Manufacturer data key 0x0006 value `03 00 80`.
- **Battery over Bluetooth**: bluetoothctl reports `Battery Percentage: 0x64 (100)` from the standard GATT Battery Service, so the battery percentage is readable on Linux without the vendor protocol (also what upower/desktops use). Only the full-charge value (100) has been seen.
- **One HID device, six report IDs** (descriptor 246 bytes): ID 1 keyboard (64-bit input, 8-bit output LEDs), ID 2 consumer control (16-bit input), ID 3 system control (8-bit), ID 4 keyboard (120-bit input; the thumb keys arrive on a keyboard device), ID 5 mouse (56 bits: 5 buttons, X/Y s16, wheel, pan), **ID 6 vendor, usage page 0xFF55 usage 0x0202, 65-byte input and 65-byte output reports (no feature report)**. Linux creates three input nodes: `MKM071 Keyboard`, `MKM071 Mouse` and a bare `MKM071` that maps the vendor usage to ABS_MISC and never produces key events.
- **Config channel over Bluetooth**: report 6 is the likely vendor channel (output to write, input for replies). Nothing has been sent to it. The hidraw node list was empty in the capture; whether /dev/hidraw exists for it is not yet known. Framing is unknown (the 2.4G path needs the `[0xFE, n]` length frame; BLE may differ). Never touch the GATT OTA path (`upgradeOTAGATT`).
- The `EV_LED NUML` event seen on the keyboard node is the host writing the Num Lock LED state to the keyboard interface; it is not mouse traffic.

- **GATT layout (bluetoothctl list-attributes)**: HID service 0x1812 (handle 0x001b) has Protocol Mode, Report Map, HID Information, HID Control Point, the boot mouse/keyboard characteristics and **eight Report characteristics (0x2A4D)**. Handles 0x22, 0x26, 0x2A, 0x2E, 0x32 and 0x36 have a CCC descriptor (input, notify); 0x3A and 0x3D have none (output). Matching the report map: 0x22 = ID 1 keyboard, 0x26 = ID 2 consumer, 0x2A = ID 3 system, 0x2E = ID 4 keyboard, 0x32 = ID 5 mouse, 0x36 = ID 6 vendor input; 0x3A and 0x3D are the two outputs (keyboard LEDs and vendor ID 6; which is which is to be confirmed from their Report Reference). Device Information has only PnP ID, manufacturer and model number (no firmware revision). Battery Service has Battery Level (0x2A19) with notify. There is no custom vendor GATT service: the vendor channel is HID report 6. BlueZ lists these over D-Bus, but no /dev/hidraw node exists for the Bluetooth device.

- **GATT reads (2026-10-15, bt_gatt_read.sh, read-only)**: Manufacturer Name `ROYUAN`; Model Number `pan1080xa3` (looks like a Panchip PAN1080 part, inference only); PnP ID `02 51 31 28 40 00 01` = vendor source USB-IF, VID 0x3151, PID 0x4028, version 0x0100; Battery Level 0x64; HID Information `01 01 00 03` (bcdHID 1.01, country 0, flags remote-wake + normally-connectable); Protocol Mode 1 (report protocol). Report References: 0x22 = ID 1 input, 0x26 = ID 2 input, 0x2A = ID 3 input, 0x2E = ID 4 input, 0x32 = ID 5 input, **0x36 = ID 6 input (vendor, notify)**, 0x3A = ID 1 output (keyboard LEDs), **0x3D = ID 6 output (vendor write)**. BlueZ allowed all these reads, so the HID characteristics are accessible over D-Bus. Nothing has been written or subscribed on the vendor characteristics yet.

- **Vendor notifications over Bluetooth (2026-10-15)**: subscribing to the vendor input characteristic (handle 0x36, `notify on` in bluetoothctl) was accepted, and DPI-button presses arrive as 65-byte notifications that start with `0x66` (not the report ID): `66 0c <stage> 00 ...` (stage index; 19 presses cycled 4,5,0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3,4, i.e. six stages) followed by `66 0f 01 00 ...` and `66 0f 00 00 ...`. The rest of each 65-byte value is zero. The `0c` stage event matches the 2.4G report 5 (`05 0c <stage> 00`); the 2.4G capture showed only `0f 00` after it, here `0f 01` then `0f 00`. Hypothesis (unconfirmed): `0f` is a DPI-indicator on/off event for the wheel LED. Profile-button presses (second capture) arrive as `66 01 <profile> 00 ...` (profiles 1, 2, 0, 1: the cycle visits 0, 1, 2 as on the wired link), each also followed by `66 0f 01` and `66 0f 00`, so the `0f 01`/`0f 00` pair follows any DPI or profile press. The leading `0x66` is unexplained; the frame is probably `[0x66][64-byte payload]`. Passive notifications work without a hidraw node, so a Linux tool can follow live DPI changes over Bluetooth through BlueZ GATT. Nothing has been written to the vendor output characteristic (handle 0x3D).
