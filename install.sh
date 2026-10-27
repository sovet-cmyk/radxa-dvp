#!/usr/bin/env bash
set -euo pipefail

DVP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DVP_SERVICE="dvp-touch-sender.service"
DVP_POWER_PATH="dvp-touch-poweroff.path"

if [[ "$EUID" -eq 0 ]]; then
  echo "Run this as the normal DVP user, without sudo." >&2
  exit 1
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This installer requires the DVP 64-bit touch platform." >&2
  exit 1
fi
if [[ "$DVP_ROOT" =~ [[:space:]] ]]; then
  echo "Move this folder to a path without spaces before installing." >&2
  exit 1
fi
if ! getent group input >/dev/null; then
  echo "The Linux input group does not exist on this OS." >&2
  exit 1
fi

DVP_MISSING=()
python3 -c 'import evdev' >/dev/null 2>&1 || DVP_MISSING+=(python3-evdev)
command -v evtest >/dev/null 2>&1 || DVP_MISSING+=(evtest)
command -v nmcli >/dev/null 2>&1 || DVP_MISSING+=(network-manager)
if ((${#DVP_MISSING[@]})); then
  echo "Installing missing dependencies: ${DVP_MISSING[*]}"
  sudo apt-get update
  sudo apt-get install -y "${DVP_MISSING[@]}"
else
  echo "Required input and network dependencies are already installed."
fi

DVP_USER="$(id -un)"
DVP_GROUP="$(id -gn)"
sudo usermod -aG input "$DVP_USER"
chmod +x "$DVP_ROOT/touch_sender.py" "$DVP_ROOT/setup_wifi.sh" \
  "$DVP_ROOT/configure_pair.sh"
chmod +x "$DVP_ROOT/setup_appliance.sh" "$DVP_ROOT/restore_desktop.sh"

DVP_TEMP_SERVICE="$(mktemp)"
trap 'rm -f -- "$DVP_TEMP_SERVICE"' EXIT
sed -e "s|@DVP_USER@|$DVP_USER|g" \
    -e "s|@DVP_GROUP@|$DVP_GROUP|g" \
    -e "s|@DVP_ROOT@|$DVP_ROOT|g" \
    "$DVP_ROOT/dvp-touch-sender.service.in" > "$DVP_TEMP_SERVICE"
sudo install -m 0644 "$DVP_TEMP_SERVICE" "/etc/systemd/system/$DVP_SERVICE"
sudo install -m 0644 "$DVP_ROOT/dvp-touch-poweroff.service" "/etc/systemd/system/dvp-touch-poweroff.service"
sudo install -m 0644 "$DVP_ROOT/dvp-touch-poweroff.path" "/etc/systemd/system/$DVP_POWER_PATH"
sudo systemctl daemon-reload
sudo systemctl enable --now "$DVP_POWER_PATH"
sudo systemctl enable --now "$DVP_SERVICE"

echo
echo "DVP touch interface installed and enabled at boot."
echo "Configure Wi-Fi next: bash $DVP_ROOT/setup_wifi.sh setup"
echo "Then reboot once so your terminal user receives input-device permission."
echo "Service log: journalctl -u $DVP_SERVICE -f"

