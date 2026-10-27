#!/usr/bin/env bash
set -euo pipefail

DVP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DVP_PAIRING_FILE="$DVP_ROOT/pairing.json"
DVP_PROFILE="DVP-TOUCH"
DVP_SSID="DVP-TOUCH"
DVP_PASSWORD="t7Kp4Nw9Qx2R8m6V"
DVP_INTERFACE="${DVP_TOUCH_INTERFACE:-wlan0}"

if [[ -f "$DVP_PAIRING_FILE" ]]; then
  DVP_PAIRING_TEXT="$(python3 - "$DVP_PAIRING_FILE" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
for key in ("ssid", "wifi_password", "touch_shared_key"):
    value = data.get(key)
    if not isinstance(value, str) or not value or "\n" in value:
        raise SystemExit(f"Invalid {key} in {path}")
print(data["ssid"])
print(data["wifi_password"])
PY
)"
  mapfile -t DVP_PAIRING_VALUES <<< "$DVP_PAIRING_TEXT"
  if [[ "${#DVP_PAIRING_VALUES[@]}" -ne 2 ]]; then
    echo "Invalid pairing configuration: $DVP_PAIRING_FILE" >&2
    exit 1
  fi
  DVP_PROFILE="${DVP_PAIRING_VALUES[0]}"
  DVP_SSID="${DVP_PAIRING_VALUES[0]}"
  DVP_PASSWORD="${DVP_PAIRING_VALUES[1]}"
fi

usage() {
  echo "Usage: bash setup_wifi.sh {setup|start|stop|status}" >&2
  exit 2
}

command -v nmcli >/dev/null || {
  echo "NetworkManager/nmcli is required. Run install.sh first." >&2
  exit 1
}

action="${1:-}"
case "$action" in
  setup)
    while IFS= read -r old_profile; do
      if [[ "$old_profile" =~ ^DVP-TOUCH([12])?$ && "$old_profile" != "$DVP_PROFILE" ]]; then
        sudo nmcli connection delete "$old_profile" >/dev/null
      fi
    done < <(nmcli -g NAME connection show)
    if nmcli -g NAME connection show | awk -v name="$DVP_PROFILE" '$0==name {found=1} END {exit !found}'; then
      profile_type="$(nmcli -g connection.type connection show "$DVP_PROFILE")"
      if [[ "$profile_type" != "802-11-wireless" && "$profile_type" != "wifi" ]]; then
        echo "A non-Wi-Fi profile named $DVP_PROFILE exists; refusing to alter it." >&2
        exit 1
      fi
    else
      sudo nmcli connection add type wifi ifname "$DVP_INTERFACE" \
        con-name "$DVP_PROFILE" ssid "$DVP_SSID"
    fi
    sudo nmcli connection modify "$DVP_PROFILE" \
      connection.autoconnect yes connection.autoconnect-priority 999 \
      connection.autoconnect-retries 0 \
      802-11-wireless.mode infrastructure 802-11-wireless.band bg \
      802-11-wireless.powersave 2 \
      wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn \
      wifi-sec.psk "$DVP_PASSWORD" \
      ipv4.method auto ipv6.method disabled
    echo "Saved $DVP_SSID. Run 'bash setup_wifi.sh start' when the DVP hotspot is active."
    ;;
  start)
    echo "Connecting the DVP interface to $DVP_SSID; the current Wi-Fi SSH session may disconnect."
    sudo nmcli radio wifi on
    sudo nmcli connection up "$DVP_PROFILE"
    ;;
  stop)
    sudo nmcli connection down "$DVP_PROFILE"
    ;;
  status)
    echo "Configured touch network: $DVP_SSID"
    nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
    nmcli -f NAME,TYPE,DEVICE connection show --active
    ip -4 address show dev "$DVP_INTERFACE" || true
    ;;
  *) usage ;;
esac

