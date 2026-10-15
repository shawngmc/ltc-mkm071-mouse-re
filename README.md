# LTC MKM071 mouse reverse engineering

Protocol notes and Linux tools for the LTC MKM071 gaming mouse (a Rongyuan-family device,
model ID 1833). The protocol was first worked out from GearMaster 470.1.80, the vendor's Windows
configuration app, and then checked on real hardware.

| Mode | USB ID | Config status |
| --- | --- | --- |
| Wired | 3151:4026 | Read and write work (vendor interface 2, 64-byte feature reports over libusb) |
| 2.4G dongle | 3151:4027 | Unsolicited events only; config framing unknown |
| Bluetooth LE | 3151:4028 | Passive GATT reads and notifications only |

Start with [docs/protocol.md](docs/protocol.md). Section 0 is the current state and takes
precedence over older sections. [docs/capability-tracker.md](docs/capability-tracker.md) lists every
feature with its read/write status.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/mkm071_probe.py --selftest
```

Most tools need root (or a udev rule) to reach the USB device or `/dev/input`. Each script's
docstring holds its usage.

## Tools

The scripts in `tools/` import `mkm071_probe` from that same folder, so keep them together.

**Config channel (wired unless noted)**

| Script | Sends | Purpose |
| --- | --- | --- |
| `mkm071_probe.py` | Queries only | Read-only probe: model, profile, button matrix, DPI, option blocks |
| `mkm071_write.py` | Gated writes | Button slots, DPI, stage colours, option block 0, macros, profile. Dry run by default; takes a backup and checks every write by reading it back; `--restore` |
| `mkm071_fn_test.py` | Gated write | Checks whether the FN-layer matrix (0xD1/0x51) is separate from the base matrix; restores automatically |
| `fn_scan.py` | Queries only | Compares the base and FN matrices for profiles 0-4 |
| `profile_watch.py` | Queries only | Polls the active profile at 10 Hz and prints every change |
| `dongle_enum.py` | Reads only | Dongle descriptors, report descriptors and GET_REPORT for each report ID |
| `dongle_try.py` | Reads, `FE` frame | Dongle report-5 listening and framing experiments |
| `bt_ids.py` | Nothing | Records the Bluetooth identity and HID descriptors of a paired mouse |
| `bt_gatt_read.sh` | GATT reads | Reads the standard GATT values through bluetoothctl; takes the mouse address |

**evdev observers (no device commands)**

| Script | Purpose |
| --- | --- |
| `key_timing.py` | Key event timestamps and gaps (macro delay units) |
| `mouse_events.py` | Button and motion events with timing (macro mouse events) |
| `rate_check.py` | Packets per second and motion totals (report rate, sensitivity) |

Put tool output in `captures/`. The writers save their backups there by default (`--backup-dir` to change). Git ignores everything in it, and also ignores backup JSONs anywhere in the repo.

## Safety

Only query opcodes and gated, reversible writes are used. Never send the bootloader frame,
`BA FF`, `BA C0`, factory reset `0x01`, or `upgradeOTAGATT`. `mkm071_write.py` backs up
profiles 0-4 only. A stray "next profile" press can leave the mouse in profile 5 or higher. To get back,
run `--set-profile 0`. See section 0 of the protocol doc.

## License

MIT, see [LICENSE](LICENSE).
