# libratbag and Piper vs. the MKM071

What libratbag can express, what it requires from a device, and how the MKM071's protocol fits
it. Written for whoever implements the C driver. Mouse facts come from [protocol.md](protocol.md)
(section 0 wins) and the Python library in `src/mkm071`. libratbag facts come from its source:
libratbag commit `8235b5b` (2026-09-17) and Piper commit `5d3c884` (2026-08-12). File references
like `libratbag-private.h` are in libratbag's `src/`.

Status words used below: **verified** = written and read back on the mouse with a visible or
measured effect; **reads back** = written and read back, effect not seen; **decoded** = layout
known from GearMaster, never written; **unknown** = not found.

## Summary

The wired MKM071 fits libratbag's model well. Profiles, DPI stages, report rate, debounce, angle
snapping, button remapping and macros all have a libratbag equivalent, and the mouse can be read
completely, so no write-only workarounds are needed. There is one blocker to check first, and
there are three places where libratbag can express less than the mouse does.

| Area | libratbag model | MKM071 | Fit |
| --- | --- | --- | --- |
| Transport | hidraw only, feature reports through `HIDIOCSFEATURE`/`HIDIOCGFEATURE` | 64-byte feature report, report ID 0, interface 2; protocol.md says no hidraw node, only hiddev | **Blocker until checked** |
| Matching | one `.device` file per `DeviceMatch`, driver named in it | 3151:4026 is shared by several Rongyuan models; model ID from 0x8F | Good: one family file with per-model sections, like sinowealth |
| Profiles | N profiles, one active; optional names, default, enable/disable | 5 profiles (firmware answers 0-7), active via 0x85/0x05, no names | Good |
| Resolutions | per profile, fixed count, DPI list ≤ 300 entries, active/default, separate X/Y | 6 stages per profile, 50-12000 step 50, active stage stored, X/Y tables stored | Good |
| Report rate | per profile, list ≤ 8 | 125/250/500/1000; verified path is a shared block | Partial: global setting |
| Debounce, angle snapping | per profile; debounce list ≤ 8 values in ms | one shared byte each; units and effect unconfirmed | Partial: global, unverified |
| Buttons | per profile: none, button N, special, key, macro | 16 slots per profile, 14 in use | Good |
| Macros | per button, key press/release/wait, ≤ 256 events | 20 shared slots, 1 ms delays, key + mouse button + move events, repeat/toggle/hold modes | Partial: subset only |
| LEDs | per profile, off/on/cycle/breathing, colour | one wheel LED showing the active DPI stage's colour; the light record does nothing | Poor: no per-stage colour in libratbag |
| Not in libratbag | | X/Y sensitivity (verified), lift-off, sleep timers | Lost |
| Wireless | receivers and Bluetooth work if a hidraw node and the protocol are known | dongle framing unknown; Bluetooth vendor channel uses output/input reports | Wired only for now |

## Hard requirements

### 1. A hidraw node for the config interface

ratbagd finds devices by enumerating the `hidraw` subsystem and reads the IDs from `HID_ID`
(`ratbagd.c`, `get_product_id()` in `libratbag.c`). A driver then picks one of the device's hidraw
nodes with `ratbag_find_hidraw(device, match)`, which walks the hidraw siblings under the same USB
device and keeps the first one the `match` callback accepts. Feature reports only go to
`hidraw[0]` (`ratbag_hidraw_raw_request()`). There is no libusb path anywhere in libratbag.

So interface 2 (vendor page 0xFFFF, usage 2, one 64-byte feature report, no report ID) must have
its own `/dev/hidrawN`. protocol.md records it as "hiddev only, no hidraw", for the dongle in
section 2.1 and for the wired mouse in the capability tracker. That observation is suspicious:
in the kernel's `hid_connect()` (mainline `drivers/hid/hid-core.c`) hidraw is connected on its
own, independently of hidinput and hiddev, whenever `HID_CONNECT_HIDRAW` is in the connect mask,
which it is for the generic HID driver. A hiddev node shows that `hid_connect()` ran for that
interface. A descriptor with only feature
reports still gets a hidraw node. More likely explanations: the node was there but hidapi did not
list it, or it was missing on one particular kernel. This is the first thing to check on hardware:

```sh
for d in /sys/class/hidraw/hidraw*; do
  printf '%s  ' "$d"; grep HID_ID "$d/device/uevent" | tr '\n' ' '
  cat "$d/device/../bInterfaceNumber" 2>/dev/null
done
ls -l /dev/usb/hiddev* 2>/dev/null
```

If interface 2 has a node, the rest of this document applies directly. Next, a `HidrawTransport`
for the Python library (`HIDIOCSFEATURE(65)` with `buf[0] = 0`, then `HIDIOCGFEATURE(65)`) would
prove the protocol works over hidraw before any C is written. libratbag uses the same layout:
`ratbag_hidraw_set_feature_report(device, 0, buf, 65)` puts the report number in `buf[0]`.

If it really has no node, a libratbag driver is not possible without a kernel fix (a quirk, or
working out why hidraw does not bind), and that would be the upstream conversation to have first.

Running the Python CLI and ratbagd at the same time will not work: the CLI detaches the kernel
driver from interface 2, which removes the hidraw node from under ratbagd.

### 2. One device file for the whole 3151:4026 family

`DeviceMatch` must be unique across all device files (upstream's `test/duplicate-check.py` fails
the build otherwise), and the GearMaster device table gives 3151:4026 to the MKM071 (model 1833),
GM025 (1847), GM024 (1975), GM023 (1829) and GW24 (1771). So the file has to describe the family,
with the model picked at probe time. The sinowealth driver already does this, keyed on a firmware
string (`[Driver/sinowealth/devices/V127]`). Here the key would be the 0x8F model ID:

```ini
[Device]
Name=Rongyuan-family mouse (wired)
DeviceMatch=usb:3151:4026
Driver=rongyuan
DeviceType=mouse

# LTC MKM071; the firmware holds 12000 DPI although GearMaster's table says 8000
[Driver/rongyuan/devices/1833]
DeviceName=LTC MKM071
Profiles=5
Buttons=14
DpiRange=50:12000@50
Leds=0
```

A new `[Driver/rongyuan]` section needs its own parser in `libratbag-data.c`
(`init_data_sinowealth()` is the model to copy). The probe returns `-ENODEV` for a model ID
without a section, so other models are left alone until someone verifies them.

### 3. A C protocol layer plus driver

libratbag's README asks for the whole known protocol in a separate file when the protocol is
separate from the driver (as with HID++). Here that means `rongyuan.c/.h`, porting `frame.py`,
`commands.py`, `macro.py` and the `model.py` codecs, plus `driver-rongyuan.c` implementing
`probe`, `remove`, `commit` and `set_active_profile`, registered in `libratbag.c` and `meson.build`.
The safety rules carry over: an opcode allow-list, never `0x01`, `7F 55 AA ...`, `BA ..`, and a
read-modify-write of every option block so unknown bytes are kept.

## Area by area

### Device identity

| libratbag | MKM071 |
| --- | --- |
| `name` from `HID_NAME` | USB product string |
| `firmware_version` (optional string) | none found: 0x8F returns the model ID, bytes 3-7 (`00 00 06 00 70`) unexplained. Leave unset |
| `DeviceType=mouse` | yes |

### Profiles

| libratbag | MKM071 | Notes |
| --- | --- | --- |
| `num_profiles`, same counts for all (`ratbag_device_init_profiles`) | 5 (0-4) | firmware also answers 5-7 with defaults; whether they are real storage is unknown. Expose 5 |
| exactly one active profile (probe sanity check fails otherwise) | 0x85 reads it, 0x05 sets it; **verified** | if the mouse is on profile 5+ at probe time (a `next` press from 4 does that), no exposed profile is active and the device would be rejected. Recommended: fail the probe with a log message telling the user to switch back (`mkm071 --yes profile set 0`) rather than writing during probe |
| `set_active_profile` callback | 0x05 `[5, n]`, **verified** | GearMaster re-sends the DPI block then option block 0 about 200 ms after a switch; our switches worked without that |
| name (`profile->name`; NULL means unsupported) | not stored in the mouse | leave NULL; `ratbag_profile_set_name()` then reports no capability |
| `RATBAG_PROFILE_CAP_SET_DEFAULT` | unknown | don't advertise |
| `RATBAG_PROFILE_CAP_DISABLE` | no known field | the hardware cycle only visits 0-2, which hints at a hidden profile count; open |
| `RATBAG_PROFILE_CAP_WRITE_ONLY` | not needed | everything can be read back |

### Resolutions (DPI stages)

| libratbag | MKM071 | Notes |
| --- | --- | --- |
| fixed count per profile | 6 stages (header byte 3; the layout has room for 8) | expose 6 |
| `dpis[]`, ≤ 300 entries | 50-12000 in steps of 50 = 240 values | use `ratbag_resolution_set_dpi_list()`, not `..._from_range()`, whose step grows above 1000 DPI. Read the range from the device file |
| `dpi_x`, `dpi_y`, `CAP_SEPARATE_XY_RESOLUTION` | separate X and Y tables stored | **verified** with X = Y only; different values untested. Advertise after a hardware check |
| `is_active` | current stage, header byte 2; **verified**, changes live with the DPI button | |
| `is_default` | no separate field | follow sinowealth: `is_default = is_active` |
| `CAP_DISABLE` | stage count byte; DPI 0 marks an unused stage | only trailing stages could be disabled; untested. Don't advertise yet |
| (none) | per-stage colour (bytes 40-63), **verified**, the only visible lighting | no libratbag equivalent; see LEDs |

Writes must be read-modify-write of the whole 0x54 block: the colours are blanked if they are not
echoed (protocol.md 2.3).

### Report rate

libratbag keeps `hz` and `rates[]` per profile; Piper shows 125/250/500/1000 buttons, exactly the
MKM071's set. The mouse has two places:

- option block 0 byte 9 (shared by all profiles): **verified**, 125 Hz measured as 125 packets/s;
- 0x84/0x04 with a profile index: reads 1000 on every profile; writes untested.

Which one the firmware applies when profiles switch is unknown. Until a hardware check settles it
(set profile 1 to 125 Hz through 0x04, switch profiles, measure with `tools/rate_check.py`), expose
the shared byte: report the same rate on every profile, and on a change mark all profiles dirty
and write byte 9 once.

### Debounce and angle snapping

| libratbag | MKM071 | Notes |
| --- | --- | --- |
| `debounce` in ms, `debounces[]` ≤ 8 values, per profile | option block 0 byte 10, 0-10, shared; this unit reads 1 | reads back; no measurable effect, unit unconfirmed ("likely ms"). Sinowealth precedent: a global debounce goes on the first profile only. Offer at most 8 values (e.g. 0, 1, 2, 4, 6, 8, 10) once the unit is known |
| `angle_snapping` (int, -1 = unsupported) | byte 53, 0/1, shared | reads back; no observed effect. Expose as a switch on the first profile, or leave at -1 until an effect is seen |

### Settings libratbag has no API for

These stay in the Python CLI:

| Setting | Where | Status |
| --- | --- | --- |
| X/Y sensitivity (10-200 %) | option block 0 bytes 50-51 | **verified** (0.53x at 50) |
| lift-off distance | byte 52 | reads back, no effect seen |
| sleep timers (BT and 2.4G, light and deep) | bytes 40-47 | reads back, units unconfirmed |
| light-off flags, battery colours, charging switch | bytes 12-13, 54-60 | untested / write ignored |

The driver must echo all of these unchanged whenever it writes option block 0.

### Buttons

libratbag has one action per button: `NONE`, `BUTTON` (number), `SPECIAL` (enum), `KEY` (evdev
keycode) or `MACRO`, plus `UNKNOWN` for anything it cannot represent. A button advertises the
types it accepts with `ratbag_button_enable_action_type()`.

Expose 14 buttons with index = slot (0-13). Slots 14 and 15 hold `F5 ±1`, horizontal-scroll
codes on a mouse with no tilt wheel; keep them out of the UI and never write them. Piper needs an
SVG with `button0`..`button13` (see "Piper" below).

| libratbag action | Slot bytes | MKM071 status |
| --- | --- | --- |
| `NONE` | `00 00 00 00` | decoded |
| `BUTTON` 1-5 (left, right, middle, back, forward) | `01 00 F0..F4 00` | **verified** |
| `KEY` (keycode, no modifiers) | `00 00 usage 00` | **verified** (plain key) |
| `KEY` with modifiers (libratbag stores it as a short macro: `ratbag_action_keycode_from_macro()`) | `00 mods usage 00` | decoded; modifier encoding unknown. libratbag's `MODIFIER_*` bits are the HID bitmask, and the sinowealth firmware uses the same layout, which is weak evidence that this firmware does too. The CLI makes the same assumption |
| `KEY` with a consumer keycode (`KEY_VOLUMEUP` ...) | `03 00 lo hi` | decoded; use `ratbag_hidraw_get_consumer_usage_from_keycode()` |
| `SPECIAL` `WHEEL_UP` / `WHEEL_DOWN` | `01 00 F9 01` / `01 00 F9 FF` | read from the factory layout; writes untested |
| `SPECIAL` `WHEEL_LEFT` / `WHEEL_RIGHT` | probably `01 00 F5 ±1` | direction unknown, no tilt hardware: don't advertise |
| `SPECIAL` `RESOLUTION_CYCLE_UP` | `14 00 00 00` | read from the factory layout |
| `SPECIAL` `RESOLUTION_UP` / `_DOWN` | `14 00 01 00` / `14 00 02 00` | decoded; whether they wrap is unknown |
| `SPECIAL` `RESOLUTION_ALTERNATE` | `14 00 04 00` ("DPI shift") | decoded; which DPI the shift uses is unknown |
| `SPECIAL` `RESOLUTION_CYCLE_DOWN`, `_DEFAULT` | none | not supported |
| `SPECIAL` `PROFILE_CYCLE_UP` | `08 00 03 00` | **verified**, but only visits profiles 0-2 |
| `SPECIAL` `PROFILE_UP` | `08 00 01 00` | **verified**, but no wrap and no clamp: 4 → 5 strands the mouse outside the exposed profiles. Recommended: don't advertise it, or the user can end up with a device ratbagd rejects (see Profiles) |
| `SPECIAL` `PROFILE_DOWN` | `08 00 02 00` | **verified**, stops at 0 |
| `SPECIAL` `PROFILE_CYCLE_DOWN`, `DOUBLECLICK`, `RATCHET_MODE_SWITCH`, `SECOND_MODE`, `BATTERY_LEVEL` | none known | not supported. The FN layer (0xD1) is read-only, so there is no second mode |
| `MACRO` | `09 mode index 00` | **verified**; see Macros |
| `UNKNOWN` | anything else: two-key slots `00 mods k1 k2`, profile jump `08 00 04 n` (did nothing), fire key (type 11), FN key (type 10), launch-app types 6/18, system power type 2 | show as unknown and preserve on commit |

### Macros

This is the biggest mismatch.

| | libratbag | MKM071 |
| --- | --- | --- |
| Ownership | each button has its own macro | 20 shared slots; a button points at one with `09 mode index 00`, and several buttons may share a slot |
| Events | `KEY_PRESSED`, `KEY_RELEASED` (evdev keycodes), `WAIT` (ms); ≤ 256 events | key down/up (HID usage), mouse button down/up (`F0..F4`), relative move (`F9 dx dy`), delays in 1 ms units up to 65535 |
| Length | 256 events | 220 bytes of content: roughly 100 key events with short delays |
| Playback | play once | repeat count (u16 in the buffer), plus mode 0 repeat N, 1 toggle, 2 hold |
| Name | `name`, `group` | none |

Recommended mapping:

- **Reading.** Decode the slot a button points at. If the macro has repeat 1, mode 0, and only
  key and delay events, expose it as `MACRO`, converting HID usages to keycodes. Mouse-button
  events could map to `BTN_LEFT`..`BTN_EXTRA` keycodes, but Piper's recorder only captures the
  keyboard. Anything else (repeat > 1, toggle, hold, moves) becomes `UNKNOWN`, so a commit never
  silently rewrites a macro the user built with the CLI.
- **Writing.** Slots have to be allocated. With 5 profiles × 14 buttons there can be more macro
  buttons than the 20 slots, so `commit` must fail with a capability error past 20 distinct
  macros. Identical macros may share a slot. The driver may only overwrite a slot that is empty
  (all `00` or `FF`) or used only by buttons it is rewriting, otherwise it would destroy
  unassigned macros the user stored with the CLI.
- **Order.** Write the macro pages (0x16) before pointing a button at the slot (0x50), and check
  the size before sending anything.
- Two encoder questions are open: a short (≤ 127 ms) delay after a move decodes as half its
  value (roadmap Phase 5), and the page-boundary terminator fix still needs to be checked on the
  mouse.

### LEDs

libratbag LEDs are per profile, with modes off / on / cycle / breathing, a colour, colour depth,
brightness and a duration. The MKM071 has one LED under the wheel. It is a status light (green
charging pulse, a flash on 2.4G connect) that shows the **active DPI stage's colour** when the
stage changes. The light record in option block 0 (type, speed, brightness, RGB) is stored but
does nothing visible on this model, and GearMaster lists the MKM071 as non-RGB.

The colour that matters is a per-resolution property, and libratbag has no per-resolution colour.
The options:

1. **Expose no LEDs** (`Leds=0`). Honest and simple; the stage colours stay in the CLI
   (`mkm071 dpi color`). Recommended.
2. Expose one LED in mode `ON` whose colour reads and writes the active stage's colour. It works,
   but the LED appears to change colour when the user switches stages, and editing other stages is
   impossible.
3. Propose a per-resolution colour to libratbag upstream. A bigger change, but other mice with
   DPI-indicator LEDs would benefit.

Never expose the light record: it would be a control with no effect.

### Battery and wireless

- libratbag has no battery API; the only battery item is the `BATTERY_LEVEL` special action. Over
  Bluetooth, UPower already reads the GATT Battery Service, so nothing is lost.
- **2.4G dongle (3151:4027).** libratbag handles receivers when it can talk through them over
  hidraw, but this dongle's relay framing (`FE n`, the `dangledevtype` wrapper, `is_write_finish`
  polling) is unknown and wired-style frames return zeros. Leave it out of `DeviceMatch` until a
  GearMaster USB capture explains it.
- **Bluetooth LE (3151:4028).** `DeviceMatch=bluetooth:3151:4028` is possible in principle.
  However, the vendor channel is HID report 6 as 65-byte output and input reports, not a feature
  report, so it would need a second transport in the driver (`ratbag_hidraw_output_report()` plus
  input reads), and its framing (leading `0x66`) is unknown. protocol.md also records no hidraw
  node for the Bluetooth device. BlueZ normally creates one through uhid, so check that the same
  way as the wired interface.

### Commit, timing and probe cost

| libratbag | MKM071 |
| --- | --- |
| changes are cached and marked dirty; `commit` writes them | every write applies at once and persists in flash; there is no commit opcode |
| `probe` must read the full state | per profile: matrix 0xD0, DPI block 0xD4, rate 0x84 (15 queries for 5 profiles), plus 0x8F, 0x85, 0xD3, and 4 pages per referenced macro. At ~10-20 ms per query that is well under a second |
| errors: `RATBAG_ERROR_DEVICE`, `_CAPABILITY`, `_VALUE` | the Python library's `WriteNotApplied` maps to `RATBAG_ERROR_DEVICE`; range errors map to `_VALUE` |

Recommended commit order: macro pages (0x16), button slots (0x50, one per dirty button),
DPI blocks (0x54, one read-modify-write per dirty profile), option block 0 (0x53, one
read-modify-write), then `set_active_profile` (0x05), which libratbag calls itself after
`commit`. Read each one back as `mkm071.device` does; the firmware gives no other
acknowledgement. Each exchange is SET_REPORT, about 10 ms wait, GET_REPORT.

### Piper

- Piper shows report rate (125-1000 buttons, greyed out for unsupported rates), a debounce
  dropdown from `debounces[]` and an angle-snapping switch (disabled when `angle_snapping == -1`),
  so all three appear once the driver fills them.
- Device drawings are SVGs in Piper's `data/svgs/`, matched by `DeviceMatch` in `svg-lookup.ini`.
  Without one Piper uses `fallback.svg`. A drawing needs ids `button0`..`button13` (matching the
  slot indexes) and leader lines per its `data/svgs/README.md`. With a shared PID, one drawing
  covers every model that has no drawing of its own, so the MKM071 drawing would show for GM025
  owners too. Worth raising upstream.

## What gets lost in Piper, and what Piper wants that the mouse lacks

The mouse can do, but libratbag cannot express:
- X/Y sensitivity, lift-off distance, sleep timers;
- per-stage indicator colours;
- macro repeat counts, toggle and hold modes, mouse moves and mouse buttons inside macros;
- two-key slots, fire key, launch-app functions.

libratbag offers, but the mouse lacks:
- profile names, default profile, enabling and disabling profiles;
- `RESOLUTION_CYCLE_DOWN` and `PROFILE_CYCLE_DOWN`;
- LED effects, and a firmware version string.

## Checks that need the mouse, in order

1. The hidraw node for interface 2 (blocks everything). Then a hidraw transport in the Python
   library, run against the real mouse.
2. Report rate: does 0x04 per profile take effect, or only option block 0 byte 9?
3. Modifier byte encoding in key slots (`key:ctrl+c`).
4. Separate X/Y DPI values; the stage count byte (resolution disable).
5. Debounce unit (needed for libratbag's ms list).
6. DPI up/down (wrap or clamp?) and DPI shift (which DPI?).
7. Wheel slot writes (`F9 ±1`), `NONE` (`00 00 00 00`), consumer keys.
8. Whether profiles 5-7 are real storage, and whether a profile-count field exists.

Items 2-8 are already in [roadmap.md](roadmap.md) Phase 5 or follow from it.
