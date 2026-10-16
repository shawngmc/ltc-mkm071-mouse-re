# Roadmap: Python library, CLI and libratbag notes

The aim is to turn the scripts in `tools/` into an installable Python package with a CLI, and to write
an implementation spec detailed enough to add the MKM071 to libratbag/Piper. Each item is marked by
whether it needs the physical mouse. Most of the work does not, because a simulated device can stand in.

Starting point: [tools/mkm071_probe.py](../tools/mkm071_probe.py) holds framing, `UsbLink`, `query` and the
parsers. [tools/mkm071_write.py](../tools/mkm071_write.py) holds the frame builders, the macro encoder,
backup/restore and a `main()` that does every operation inline. The protocol is in [protocol.md](protocol.md)
(section 0 wins). No raw captures are committed, so test fixtures start from the hex quoted in protocol.md.

## Phase 1: Package skeleton (no hardware)

Done (not yet signed off on hardware; see Phase 5). The old and new code were compared on random
inputs and by running `tools/mkm071_write.py` from before and after against the simulated mouse:
same output, same frames, same device state.

- [x] `pyproject.toml`, package `mkm071` under `src/`, Python ≥ 3.9, dependency `pyusb`; extras `hid`
      (hidapi) and `dev` (pytest, ruff). Console script `mkm071` (only `info` so far; the rest is Phase 3).
- [x] Modules, moved from the two scripts rather than rewritten:
  - `frame.py`: opcodes, checksum, `build_frame` (queries only), `build_command` (gated writes),
    `check_frame` and the never-send list.
  - `transport.py`: `Transport` protocol, `UsbTransport` (alias `UsbLink`), `query`, `send_command`,
    `read_macro`.
  - `commands.py`: the write-frame builders and spec parsers (`build_set_*`, `parse_opt0_spec`,
    `parse_light_spec`). Not in the original plan; split out so `model.py` stays decoding only.
  - `model.py`: the dict parsers for the probe's JSON, plus dataclasses `ButtonAction`, `Matrix`,
    `LightRecord`, `Opt0`, `DpiBlock`, `Macro`, `Profile`, `DeviceState`. Block round trips are byte-exact.
  - `macro.py`: encoder, decoder, spec parser, page frames.
  - `device.py`: `Mkm071` with reads, verified writes (`WriteNotApplied` on a failed read-back),
    `backup()` and `restore()`.
  - `backup.py`: `make_backup`, `save_backup`, `load_backup`, `restore_plan`; schema version 2,
    version 1 backups still load.
  - `events.py`: `decode_event` for dongle report 5 and Bluetooth `66 …` notifications.
  - `descriptor.py`: `parse_report_descriptor`, sysfs lookup, the dongle descriptor fixtures.
- [x] Safety rules in the library: `send_command` refuses the never-send list and unknown opcodes;
      `Mkm071` refuses writes before the model check and outside profiles 0..`MAX_PROFILE`.
- [x] `tools/mkm071_probe.py` and `tools/mkm071_write.py` are now thin command lines over the package;
      the other scripts are unchanged and still import `mkm071_probe`.
- [ ] Left for later: macro events are still dicts, not a `MacroEvent` type. Experiment scripts that
      call `link.send_feature_report` directly (`mkm071_fn_test.py`, `dongle_try.py`) bypass
      `check_frame`.

## Phase 2: Tests and a simulated mouse (no hardware)

Done: 211 tests, plus 1 expected failure for an open question, on Python 3.9 to 3.13. Run
`pytest`; `tools/mkm071_probe.py --selftest` and `tools/mkm071_write.py --selftest` now run the same suite.

- [x] Both `selftest()` functions ported to pytest and split per module (`tests/test_*.py`).
- [x] `tests/fakedevice.py` simulates the wired mouse as the hardware notes describe it: reply
      shapes (no echo on 0xD0/0xD1/0x96, header echo on 0xD3/0xD4, echo-only 0xF1/0x82, zeros for
      unknown opcodes), profiles 0-7 with 5-7 default-initialized, read-only FN layer, charging switch
      that ignores writes, macro erase to 0xFF with stale bytes 224-255, and a dongle mode that answers
      zeros. It rejects bad checksums and anything on the never-send list.
- [x] Golden tests with expected bytes written out from protocol.md: every write builder, the light
      record, macro short/long delay forms, mouse moves, the "hi" and 174-byte 4-page macros.
- [x] Backup, change and restore round trips (all profiles, one profile, version 1 backups, failure
      part-way); refusal tests for 0x01, `7F 55 AA …`, `BA FF`, `BA C0` and unknown opcodes.
- [x] `tools/` scripts tested end to end on the simulated mouse (probe `--dump`, every writer command,
      `--restore`, refusals).
- [x] CI: `.github/workflows/ci.yml` runs ruff and pytest on Python 3.9 to 3.13.

Fixed along the way:
- Macro size limit is now 220 bytes of content, so content and its 4-byte terminator always fit in
  the 224 bytes the 4 pages write (was 248, which needed an untested fifth page). Protocol.md updated.
- `macro_frames` always sends the page holding the terminator. Before, content ending exactly on a
  56-byte page boundary left the terminator in an unsent (erased, 0xFF) page. This is one page more
  than GearMaster sends in that case; see Phase 5.
- A macro whose last event has no trailing delay counts the two implied zero bytes against the limit.
- `save_backup` never overwrites: two backups in the same second used to share a file name, so the
  second run's automatic backup replaced the first.
- The probe's `--profiles` "identical_to" never matched, because byte 1 of the DPI block is the
  profile index; it is now skipped.

Open (expected failure in `tests/test_macro.py`): a mouse move followed by a delay of 1-127 ms
encodes the delay as is but decodes it halved. Both sides are GearMaster ports; see Phase 5.

## Phase 3: CLI (built without hardware, signed off with it)

Built and tested against the simulated mouse (`tests/test_cli.py`); not yet run on the mouse.

- [x] `mkm071` subcommands: `info`, `dump [--json]`, `backup`, `restore FILE`, `profile get|set`,
      `button list|set`, `dpi list|set|stage|color`, `rate get|set`, `option list|set`, `light set`,
      `macro list|show|write|clear`, `watch`. `-p N` edits a profile without switching to it.
- [x] Readable action syntax (`left`, `key:ctrl+c`, `macro:3:hold`, `dpi:cycle`, `profile:next`,
      `media:volup`, `disabled`, `raw:..`) in `actions.py`, with physical slot names (`thumb_1`,
      `dpi_button`, ...). Encodings never written on hardware print a warning. A shared HID key
      table (`keys.py`) serves both the button and macro syntax.
- [x] Safety flow: dry run by default, `--yes` to write, automatic backup to
      `$XDG_STATE_HOME/mkm071/backups` before writing, read-back check (exit status 1 if not
      applied), `--force` needed to remap the left button, writes refused while the mouse sits on
      profile 5+.
- [x] `watch`: wired polling (queries only) and `--dongle` (reads hidraw report 5, sends nothing).
- [x] udev rule `udev/70-mkm071.rules` (`uaccess` for 3151:4026 and 3151:4027, usb and hidraw).
- [ ] `watch` over Bluetooth (GATT notifications on handle 0x36 through BlueZ D-Bus).
- [ ] Optional: declarative config, `mkm071 apply profile.toml`, which diffs against the device and
      writes only what changed (needs `tomli` on Python < 3.11).

## Phase 4: libratbag/Piper implementation notes (mostly no hardware)

[libratbag.md](libratbag.md) compares libratbag's model, requirements and Piper's UI with the
mouse, area by area, and records the recommended mapping. Checked against libratbag `8235b5b` and
Piper `5d3c884`.

- [x] Requirements: hidraw only (no libusb path), one `.device` file per `DeviceMatch`, and a
      separate C protocol layer plus driver.
- [x] Model mapping: 5 profiles without names, 6 resolutions (`is_default = is_active`, DPI list
      50-12000 step 50), report rate, debounce and angle snapping as global settings, 14 buttons
      (slots 14-15 hidden), `Leds=0`.
- [x] Button action table: every libratbag type and special mapped to slot bytes with its hardware
      status; `PROFILE_UP` not advertised (it strands the mouse on profile 5).
- [x] Macro mapping: only repeat-once key/delay macros as `MACRO`, everything else `UNKNOWN`;
      slot allocation that never overwrites macros libratbag does not own.
- [x] LED decision: expose no LEDs; per-stage colours have no libratbag equivalent (an upstream
      per-resolution colour is an option).
- [x] Commit order and timing; probe cost.
- [x] Device file draft: one family file for 3151:4026 with `[Driver/rongyuan/devices/<model id>]`
      sections, as sinowealth does with firmware strings.
- [x] Survey: libratbag has no VID 3151 support (the only `3151` in its source is an Asus command
      code); no other open tool for this protocol family turned up in a web search.
- [ ] **hidraw check on hardware** (blocks a driver): see libratbag.md, "A hidraw node for the
      config interface". Then add `HidrawTransport` to the library and run it against the mouse.
- [ ] Settle the open items libratbag.md lists under "Checks that need the mouse" (report rate
      placement, modifier byte, separate X/Y, debounce unit, DPI up/down/shift).
- [ ] Piper SVG for the MKM071 (`button0`..`button13`); raise the shared-PID drawing question upstream.
- [ ] Optional: a uhid emulator built from `tests/fakedevice.py`, so a C driver can be tested without
      the mouse. It would also create a real hidraw node.
- [ ] Write `rongyuan.c/.h` and `driver-rongyuan.c` (after the hidraw check passes).

## Phase 5: Needs the mouse

- [ ] Commit sanitized real dumps (`mkm071 dump --json` for every profile, plus raw hex) to
      `tests/fixtures/` and replace the fixtures taken from the docs.
- [ ] Sign off the new CLI on hardware: each subcommand as a dry run, then with `--yes`, then verify and restore.
- [ ] udev rule: `mkm071 info` and a `--yes` write without root (detaching the kernel driver through
      usbfs should be allowed with `uaccess`, unverified). `mkm071 watch --dongle` on the real dongle.
- [ ] Key slots with modifiers (`key:ctrl+c`): the CLI assumes the modifier byte is the HID bitmask
      (ctrl = 0x01, shift = 0x02, alt = 0x04, win = 0x08). Check what types, and compare with the
      "bit-code (alt E2, ctrl E0, ...)" note in protocol.md. Same for a second key (`key:a+b`).
- [ ] The hidraw check from Phase 4 on the real wired device.
- [ ] Untested writes: DPI cycle/up/down/shift slot actions, media and system keys, disable, modifier keys.
- [ ] Macro moves with a short delay (`move:5:0,delay:50`): measure the real gap with
      `mouse_events.py` to settle whether the encoder or the decoder is right.
- [ ] Macro ending on a page boundary (`text:aaaaaaaaaaaaa,down:b,delay:10`, 56 bytes): check that
      the extra terminator page is accepted and the macro plays and reads back.
- [ ] Profile limits: whether profiles 5 to 7 are real storage, whether a profile count or enable field
      exists, and whether the report rate is per profile.
- [ ] Remaining unknowns from protocol.md section 8: sleep timer units, real effect of debounce, lift-off
      and angle snap, flags 12-13, charging switch.
- [ ] Wireless: a USB capture of GearMaster on Windows for the dongle framing (`FE n`, the dangledevtype
      wrapper, is_write_finish polling, battery through Status24), and the Bluetooth vendor output (handle
      0x3D, `0x66` framing). Then add `DongleTransport`, `BleTransport` and battery reporting.

## Phase 6: Docs and housekeeping

- [ ] README: install (`pip install .`), udev rule, CLI quickstart, a library example; keep the tools
      table for the experiment scripts.
- [ ] Restructure protocol.md: current spec first, with the section 0 and 2.x corrections merged into
      sections 3 to 5 so readers no longer reconcile "corrected from" notes; move the research log to
      `docs/research-log.md`.
- [ ] Keep [capability-tracker.md](capability-tracker.md) in step with the library's feature list.
