#!/bin/bash
# Read-only: reads standard GATT values (device info, HID info, Report Reference
# descriptors, battery level) from the paired MKM071. Writes and notifies nothing.
# Usage: bash tools/bt_gatt_read.sh AA:BB:CC:DD:EE:FF [hci0] > captures/gatt_read.txt 2>&1
#        (mouse connected over Bluetooth; find the address with `bluetoothctl devices`)
# The service/characteristic handles below were recorded on one MKM071 and may differ on others.
if [[ ! $1 =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
  echo "usage: $0 <mouse bluetooth address> [adapter, default hci0]" >&2
  exit 1
fi
MAC=${1^^}
D=/org/bluez/${2:-hci0}/dev_${MAC//:/_}
S=$D/service001b
{
  echo "menu gatt"; sleep 1
  for a in $D/service0014/char0015 $D/service0014/char0017 $D/service0014/char0019 \
           $D/service0010/char0011 $S/char001c $S/char0020 \
           $S/char0022/desc0025 $S/char0026/desc0029 $S/char002a/desc002d \
           $S/char002e/desc0031 $S/char0032/desc0035 $S/char0036/desc0039 \
           $S/char003a/desc003c $S/char003d/desc003f; do
    echo "select-attribute $a"; sleep 0.7
    echo "read"; sleep 1.2
  done
  echo "back"; echo "exit"; sleep 1
} | bluetoothctl
