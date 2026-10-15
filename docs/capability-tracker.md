# MKM071 protocol capability tracker

Oct 13, 2026 · @Shawn McNaughton

Every row below starts Not started, or N/A where a value can only be read; update the Read and Write dropdowns as each capability is captured, decoded and verified over HID.

## Device and connection

Identity and transport details come first, since every later command depends on knowing which interface carries the config channel.

| Capability | Read | Write | Notes |
| --- | --- | --- | --- |
| USB VID/PID per mode (wired, 2.4G dongle) | Verified | N/A | Wired 3151:4026, 2.4G dongle 3151:4027, Bluetooth LE 3151:4028 |
| HID interfaces and report descriptors | Verified | N/A | Wired: 3 interfaces. Dongle: boot mouse, keyboard plus vendor FFFF/1 events, vendor FFFF/2 config. Bluetooth: one HID device with report IDs 1 to 6 (keyboard, consumer, system, keyboard, mouse, vendor 0xFF55/0x0202 with 65-byte input and output reports) |
| Config channel (report ID, length, feature vs output) | Verified | N/A | Feature report, 64 bytes, no report ID, interface 2; hiddev only, so it needs libusb |
| Command framing and checksum | Verified | Verified | Byte 7 = 0xFF minus the sum of bytes 0 to 6; SET\_REPORT, wait \~10 ms, GET\_REPORT. Verified wired only |
| Device name and model string | Decoded | N/A | 0x8F returns model ID 1833 (MKM071). Over Bluetooth the Device Information service gives manufacturer ROYUAN and model number pan1080xa3 |
| Firmware version (mouse, dongle) | Captured | N/A | No version field found; 0x8F bytes 3 to 7 (00 00 06 00 70) unexplained |
| Active connection mode (wired, 2.4G, Bluetooth) | Decoded | N/A | Wired versus 2.4G is visible from the USB PID (4026 versus 4027). Bluetooth not checked and may not expose the config channel |
| Dongle pairing state | Not started | Not started | Whether the dongle can be re-paired from software |

## Buttons

The listing claims 11 programmable buttons, 7 of them on the side; the physical-to-logical numbering is the first thing to confirm.

| Capability | Read | Write | Notes |
| --- | --- | --- | --- |
| Button count and physical ID map | Verified | N/A | 12 programmable buttons plus 4 wheel slots, 16 slots in all; slot map in the protocol doc, section 2.3 |
| Live button press events (input report) | Verified | N/A | Standard mouse and keyboard events in evtest. DPI and profile presses arrive as vendor report 5 on the dongle: 05 0C \<stage> 00 for DPI, 05 01 \<profile> 00 for profile, each followed by 05 0F 00 00. Over Bluetooth the DPI press arrives as a GATT notification on the vendor input characteristic (66 0C \<stage> 00 ..., then 66 0F 01 and 66 0F 00), and profile presses as 66 01 \<profile> 00 ... with the same trailing pair |
| Mouse-function assignment (left, right, middle, back, forward) | Verified | Verified | Slot = \[1, 0, code, 0\]; F0 left, F1 right, F2 middle, F3 back, F4 forward. Write is 0x50, persists after replug |
| Keyboard key assignment with modifiers | Verified | Verified | Slot = \[0, mods, key, key2\]. Tested a plain key (thumb key 1 to 5, then restored); modifiers not tested |
| Macro assignment | Verified | Verified | Slot = \[9, mode, macro index, 0\] with mode 0 repeat N, 1 touch/toggle, 2 hold. Verified with typed text, mouse clicks and moves. Macro storage and events are under Profiles |
| DPI cycle and DPI shift functions | Decoded | Not started | \[20,0,0\|1\|2\|4,0\] = cycle, up, down, shift. Slot 3 read as cycle; write not tried |
| Profile switch function | Verified | Verified | \[8,0,3,0\] cycle (visits 0 to 2) and \[8,0,1,0\] next work from any slot. Next does not wrap or clamp (4 goes to 5); previous \[8,0,2,0\] stops at 0, no wrap. Jump \[8,0,4,n\] did nothing. Slot 1 is cycle in every profile |
| Media, system and launch-app functions | Decoded | Not started | Types from GearMaster's function table: 3 consumer, 2 system, 6 and 18 launch; none on this mouse's current config |
| Disable button | Decoded | Not started | Slot \[0,0,0,0\] = disabled |
| Scroll wheel direction and tilt behavior | Verified | Not started | Slots 12 and 13 are wheel up and down (F9, +1 and -1). This mouse has no tilt; slots 14 and 15 (F5) are unused |
| Button debounce time | Verified | Decoded | Option block 0, byte 10 (value 1 on this unit, UI range 0-10). Writing 6 reads back and restores; clicks still normal, real effect not measurable |
| Per-profile button layouts | Verified | Verified | Five profiles, each with its own 16-slot map. Edit one without switching using --profile N |
| FN-layer matrix (0xD1 read, 0x51 write) | Verified | N/A | 0xD1 returns the same factory-default layout for every profile and did not follow a change to profile 0, so it is not an alias. A 0x51 write was accepted but changed nothing. Treat it as a read-only default layout; no button switches to it |

## Sensor and DPI

The listing names a PAW3311 sensor with 6 DPI stages up to 12000 and 1000 Hz polling; anything beyond those is a guess until the software shows it.

| Capability | Read | Write | Notes |
| --- | --- | --- | --- |
| Sensor model (PAW3311) | Not started | N/A | May only be inferable from specs, not queryable |
| DPI stage count (6 per listing) | Verified | Decoded | 6 stages read; the count is header byte 3 of the 0x54 write, echoed unchanged so far |
| DPI value per stage | Verified | Verified | Plain integers in 50 DPI steps. Stage 0 changed 800 to 1000 and restored; stages are 800/2400/3200/6400/9600/12000 |
| Active DPI stage | Verified | Verified | Header byte 2 of the 0x54 block. The hardware DPI button changes it live (3 to 5 after two presses); writing it moved the active stage, verified |
| Separate X and Y DPI | Verified | Decoded | Separate X and Y tables are stored; the writer sets both to the same value, like GearMaster |
| DPI range and step size (max 12000) | Decoded | Not started | The mouse holds 12000 although GearMaster's model row says 8000; step 50. The sensor's real limit is unchecked |
| DPI indicator color per stage | Verified | Verified | The LED below the wheel shows the active stage's color. Changing stage 0 to orange (FF8000) worked and is visible |
| Polling rate (up to 1000 Hz) | Verified | Verified | Option block 0, byte 9 (1/2/4/8 = 1000/500/250/125 Hz). Writing 125 Hz measured 125 packets/s on the wired link, restored to 1000. Per-connection differences not checked |
| Lift-off distance | Verified | Decoded | Option block 0, byte 52 (0 on this unit). Writes and reads back, but no observed effect: no change at 3-5 mm by hand or on paper at 0 and 3. Valid range unknown |
| Angle snapping and motion sync | Verified | Decoded | Angle snap is option block 0, byte 53. Writes and reads back, but no observed effect in repeated slow-line tests. No motion-sync field found |
| Sleep and idle timeout | Verified | Decoded | Option block 0, bytes 40 to 47: 30 s light sleep and 5 s deep, separately for Bluetooth and 2.4G. Writing 60 for both light timers reads back and restores; units and real effect not verified |
| X and Y sensitivity | Verified | Verified | Option block 0, bytes 50 and 51 (100 on this unit, range 10 to 200). Setting X to 50 measured about 0.53 times the \|dx\| of the default. Y at 50 gave about 0.5 times the \|dy\| per second of two baselines (consistent with 0.5x; the baselines themselves varied about 40% with hand speed) |

## Battery, power and connectivity

Most of these are read-only status values; the 600 mAh battery and the tri-mode radio are the two things the software is likely to report.

| Capability | Read | Write | Notes |
| --- | --- | --- | --- |
| Battery level (percent) | Decoded | N/A | Readable over Bluetooth through the standard GATT Battery Service (bluetoothctl info shows Battery Percentage; 100 seen at full charge, discharge not checked). Not readable over the wired link (0x82 only echoes) or the dongle (zeros) |
| Charging state (charging, full, discharging) | Not started | N/A | No readable source found. The dongle returns zeros, and option block 0 byte 60 (charging switch) read 0 while charging and ignored a write. Only visible as the wheel LED pulsing green on USB |
| Battery voltage | Not started | N/A | Only if exposed |
| Low-battery threshold and warning | Not started | Not started | Option block 0 has two battery colors (bytes 54 to 59, read as black) but no low-battery behavior has been seen on the wheel. Untested |
| Sleep and auto-off timeout | Verified | Decoded | Same fields as the Sensor and DPI row: option block 0 bytes 40 to 47. Units and real effect not verified |
| Wake behavior (button, motion) | Not started | Not started |  |
| Report on dongle: mouse connected or not | Not started | N/A | The dongle returns zeros to wired-style frames and its framing is unknown. It sends vendor report 5 for DPI and profile presses only; no connect, disconnect, power-switch or dock event was seen. Needs a GearMaster traffic capture |
| Signal strength or wireless channel | Not started | N/A |  |
| Bluetooth pairing slot and name | Captured | Not started | Pairs from the OS as MKM071 (BLE, random address, appearance mouse, 3151:4028). Pairing slots are not visible and probably use the hardware button. Name is fixed; changing it is untested |
| Dock detection (on dock or not) | N/A | N/A | The dock is passive: no USB device appears and docking produced no events |

## Lighting

The only light is one LED under the wheel; the logo is opaque paint. It is a status indicator (green pulse while charging on USB, one green flash when 2.4G connects) that also shows the stage color when the DPI changes. The per-stage colors are the one addressable setting. The dock is a passive pass-through (no USB device appears) with its own button and LEDs, so it is not part of the protocol.

| Capability | Read | Write | Notes |
| --- | --- | --- | --- |
| Mouse lighting zones (count and layout) | Verified | N/A | One LED under the wheel (the logo is opaque paint). It shows the active DPI stage color when the DPI changes, pulses green while charging on USB, and flashes green once on a 2.4G connect |
| Lighting effect mode (static, breathing, cycle, off) | Verified | Decoded | Option block 0, bytes 24 to 31. Writing it works (read back OK, restored) but the LED ignores it. The wheel behaves as a fixed status indicator, so this field is probably unused on this model |
| Static color (RGB value per zone) | Verified | Decoded | Light record bytes 28 to 30 have no visible effect. The per-stage colors under Sensor and DPI are the working color setting; changing one to orange showed on the wheel when switching to that stage |
| Brightness | Verified | Decoded | Byte 26, range 0 to 4. Stored; no visible effect seen on this mouse |
| Effect speed&#32; | Verified | Decoded | Byte 25 = 4 minus speed. Stored; no visible effect seen on this mouse |
| Lighting off timeout | Not started | Not started | The wheel has no idle lighting to time out. The light\_off and wheel\_light\_off flags (option block 0, bytes 12 to 13) are untested; the sleep timers are separate (see Battery) |
| Per-profile lighting | Decoded | Decoded | Stage colors live in the per-profile DPI block, so they follow the profile. The light record is in shared option block 0. Per-profile behavior not separately tested |
| Dock lighting (mode, color, brightness) | N/A | N/A | The dock is a passive pass-through: lsusb -t shows no new USB device with the dongle in the dock, and docking produced no events. Its LEDs follow its own button |
| Battery or status indication through lighting | Captured | Not started | Observed on the wheel LED: green pulse while charging on USB; one green flash on a 2.4G connect, then off. Looks fixed-function. Battery colors (bytes 54 to 59) read as black and the charging switch (byte 60) did not take a write |

## Profiles, memory and macros

The listing promises 5 saved profiles, so the main question is whether settings live in onboard memory or only in the Windows software.

| Capability | Read | Write | Notes |
| --- | --- | --- | --- |
| Profile count (5 per listing; firmware accepts 8+) | Verified | N/A | Five profiles (0 to 4) in the listing; profiles 5, 6 and 7 also read back (default-initialized, real storage unconfirmed, upper bound unknown). The hardware cycle button only visits 0, 1 and 2; a Next action reaches 3 |
| Active profile index | Verified | Verified | Read with 0x85, switch with 0x05. The hardware profile button changes it live, and so does a command |
| Full profile dump (all settings per profile) | Verified | Verified | Buttons and DPI block per profile are backed up and restored; option block 0 is shared. Restore verified on hardware |
| Profile name | N/A | N/A | Not stored in the mouse as far as the GearMaster code shows: the mouse protocol has no name opcode, and profile or config names live in the app's local config list. Not tested on hardware |
| Onboard memory layout and size | Not started | N/A | Address map, page size, checksum |
| Commit or save-to-flash command | N/A | N/A | None needed: slot and DPI writes apply immediately, and a slot write survived an unplug and replug |
| Macro storage (count, size, per-macro length) | Verified | Verified | 20 slots of 256 bytes, read as 4 pages (0x96) and written in 56-byte pages (0x16). Slot content is limited to 224 bytes by the page protocol (bytes 224 to 255 are never written). Four-page macro verified |
| Macro events (key down/up, delay, mouse action) | Verified | Verified | Key down/up, mouse buttons (F0 to F4) and relative moves (F9) verified. Delay unit is 1 ms, exact in both the 1-byte (1 to 127) and 4-byte u16 forms. The move dy byte is inverted: positive moves the cursor up |
| Macro repeat and stop modes | Verified | Verified | Slot action 9,mode,macro,0. Mode 0 repeats N times, mode 1 is touch/toggle (tap to start, tap to stop), mode 2 is hold (repeats while held). Stopping mid-cycle releases the key at once |
| Factory reset | N/A | Not started | Capture only; do not replay until a backup exists |

## Open questions and safety

Back up the device config before any write test, and treat firmware-update and factory-reset commands as capture-only.

- Does the config channel work over the dongle, wired USB, or both? Wired works. The dongle's feature channel exists but returns zeros to wired-style frames, so its framing is still unknown
- Are settings stored onboard, or does GearMaster re-send them at startup? Onboard: slot and macro writes took effect immediately and a slot write survived an unplug and replug
- Is the side-button grid exposed as 7 separate buttons or as a shifted layer? Four thumb keys are separate slots (7, 8, 10, 11). The FN-layer read (0xD1) returns a fixed default layout for every profile and ignores writes, so there is no editable FN layer
- Does Bluetooth expose any config channel, or only standard HID? Yes, a vendor channel exists: HID report 6 (usage page 0xFF55), 65-byte input and output, with no hidraw node. Passive notifications work through BlueZ (DPI stage, profile, then 0f 01/00). Nothing has been written to the output characteristic; the framing (leading 0x66) is unknown
- Does the dock communicate with the mouse, the dongle, or neither? Answered: neither. It shows no USB device and is passive
- Are there undocumented commands in the software, such as firmware update or diagnostics?
- Planned next: capture GearMaster on Windows (USB capture) to get the dongle framing, dongle battery and the Bluetooth vendor framing. Until then, battery is available only over Bluetooth GATT.
- Other remaining unknowns: sleep timer units, real effect of debounce, lift-off and angle snap, charging switch, and whether profiles 5 and up are real storage.

## Static analysis findings

GearMaster 470.1.80 builds every command in its JS, so most rows above already have an opcode and byte layout; none is verified on hardware, so the dropdowns are untouched. Full spec: [protocol.md](protocol.md).

| Capability | Opcode (set / get) | Where it lives | Confidence |
| --- | --- | --- | --- |
| Device ID | get 0xF1 | reply bytes 1–2, little-endian; MKM071 = 1833, GM025 = 1847 | High |
| Firmware version | get 0x8F | reply bytes 1–2 | High |
| Active profile | 0x04 / 0x84 | byte 1 | High |
| Report rate | 0x03 / 0x83 | byte 2; 1000=1, 500=2, 250=4, 125=8 | High |
| DPI table and stage colors | 0x54 / 0xD4 | 8 X + 8 Y values (u16 LE), 8 colors | Medium |
| Debounce, sleep, lift-off, angle snap, X/Y sensitivity | 0x53 / 0xD3 | option block 0, bytes 8–60 | Medium |
| Button remap | 0x0A, 0x50 / 0x8A, 0xD0 | 16 slots of 4 bytes | Medium |
| Macros | 0x16 / 0x96 | variable-length events | Low |
| RGB (mouse) | 0x53 block 0 | 8-byte light record; absent from the MKM071 row, present for GM025 | Medium |
| Checksum | all commands | byte 7 = 0xFF − (sum of bytes 0–6) | High |
| DPI, profile and rate change events | input report | byte 1 = 12 (DPI), 1 (profile), 2 (rate) | Medium |
| Battery level | not found | the driver supplies it; opcode unknown | Open |

One open question matters before anything else: the listing says PAW3311 with 12000 DPI and an RGB dock, which matches the GM025 table row, not the MKM071 row (8000 DPI max, no RGB). Reading the device ID will settle which firmware you have.
