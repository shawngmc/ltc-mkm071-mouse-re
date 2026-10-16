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
feature with its read/write status. [docs/roadmap.md](docs/roadmap.md) is the plan for a Python
library, CLI and libratbag support. [docs/libratbag.md](docs/libratbag.md) maps the mouse onto
libratbag and Piper.

## Setup

Python 3.9 or later.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[hid,dev]'
.venv/bin/python -m pytest
sudo cp udev/70-mkm071.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger
.venv/bin/mkm071 info               # after replugging the mouse; without the rule, use sudo
```

The udev rule gives the logged-in user access to the wired mouse and the dongle. Without it,
the tools need root. The evdev observers in `tools/` read `/dev/input` and still need root.
Each script's docstring holds its usage.

## Command line

`mkm071` works on the wired mouse (3151:4026). Every change is a dry run that prints the old
and new values; add `--yes` to write. Before writing it saves a full backup (profiles 0-4,
options, macros) under `~/.local/state/mkm071/backups` (`--backup-dir` to change), then reads the
change back and exits with status 1 if the mouse did not take it.

```sh
mkm071 info                                   # model, profile, rate, DPI
mkm071 dump [--json]                          # everything
mkm071 backup [--out FILE]
mkm071 restore FILE                           # writes only what differs; -p N for one profile
mkm071 profile get | set N
mkm071 button list | set SLOT ACTION          # SLOT: 0-15 or thumb_1, dpi_button, right, ...
mkm071 dpi list | set STAGE DPI | stage STAGE | color STAGE RRGGBB
mkm071 rate get | set HZ
mkm071 option list | set FIELD=VALUE ...      # debounce, sleep, x_sens, y_sens, lift, angle_snap
mkm071 macro list | show N | write N --text TEXT | write N --spec SPEC | clear N
mkm071 watch [--dongle]                       # live profile and DPI-stage changes
```

`-p N` before the command edits profile N without switching to it. Button actions are
`left right middle back forward`, `scroll:up|down`, `key:5`, `key:ctrl+c`, `macro:N[:toggle|hold]`,
`dpi:cycle|up|down|shift`, `profile:cycle|next|prev`, `media:volup` and similar, `disabled` and
`raw:B0,B1,B2,B3`. `mkm071 button --help` lists them all. Actions that have not been written on hardware
yet print a warning, and so does `profile:next`, which can move past profile 4. For example:

```sh
mkm071 macro write 0 --text "gg"              # dry run: shows the macro
mkm071 --yes macro write 0 --text "gg"
mkm071 --yes button set thumb_4 macro:0
mkm071 --yes -p 1 dpi set 0 1600
```

## Library

The protocol code is the `mkm071` package in `src/mkm071`:

```python
from mkm071 import Mkm071, ButtonAction

with Mkm071.open() as m:                      # wired 3151:4026; checks the model id
    print(m.read_dpi(m.active_profile()).stages)
    m.set_slot(7, ButtonAction.key(0x22))     # thumb key 1 types '5'; read back, raises if not applied
```

Every write reads the block first, changes only the requested field, and reads it back.
`m.backup()` and `m.restore(backup)` cover profiles 0-4, option block 0 and the macros.

## Tools

The scripts in `tools/` import `mkm071_probe` from that same folder, so keep them together.
`mkm071_probe.py` adds `src/` to the import path and re-exports the package's names, so the
scripts run from a checkout without installing anything (they still need `pyusb`).

**Config channel (wired unless noted)**

| Script | Sends | Purpose |
| --- | --- | --- |
| `mkm071_probe.py` | Queries only | Read-only probe: model, profile, button matrix, DPI, option blocks |
| `mkm071_write.py` | Gated writes | The original writer, superseded by `mkm071`; kept because protocol.md quotes its commands. Dry run by default, backup, read-back check, `--restore` |
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
