# MKM071 test helper scripts (not stored as separate docs)

These small read-only helpers were used for the hardware tests. They are in the local working dir `~/Downloads/ltcmouse` and are easy to recreate:

- `key_timing.py`: evdev logger that prints gaps between key events (used to verify macro delay units; run with sudo).
- `mouse_events.py`: evdev logger for button and motion events with timing (macro mouse events).
- `rate_check.py`: counts packets per second and total |dx|, |dy| (report rate and sensitivity tests).
- `profile_watch.py`: polls 0x85 at 10 Hz and prints profile changes (in this project as `claude/profile_watch.py`).
- `bt_ids.py`, `bt_gatt_read.sh`: read-only Bluetooth identity and GATT reads through bluetoothctl.
- `dongle_enum.py`, `dongle_try.py`: read-only dongle descriptor enumeration and framing attempts (`claude/mkm071_dongle_*.py`).

Writer and probe: `claude/mkm071_write.py` (gated writer: dry run by default, backup, read-back, `--restore`) and `claude/mkm071_probe.py` (read-only). Protocol notes: `claude/mkm071-protocol.md`, section 0 is the current state.
