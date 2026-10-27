#!/usr/bin/env bash
set -euo pipefail

DVP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DVP_TARGET="graphical.target"

if [[ "$EUID" -eq 0 ]]; then
  echo "Run this as the normal DVP user, without sudo." >&2
  exit 1
fi

if [[ -f "$DVP_ROOT/.previous_systemd_target" ]]; then
  DVP_SAVED_TARGET="$(tr -d '[:space:]' < "$DVP_ROOT/.previous_systemd_target")"
  case "$DVP_SAVED_TARGET" in
    graphical.target|multi-user.target) DVP_TARGET="$DVP_SAVED_TARGET" ;;
  esac
fi

if [[ -f /boot/extlinux/extlinux.conf.before-dvp ]]; then
  sudo cp -a /boot/extlinux/extlinux.conf.before-dvp /boot/extlinux/extlinux.conf
fi
for DVP_BOOT_FILE in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  if [[ -f "$DVP_BOOT_FILE.before-dvp" ]]; then
    sudo cp -a "$DVP_BOOT_FILE.before-dvp" "$DVP_BOOT_FILE"
  fi
done
for DVP_IDENTITY_FILE in /etc/hostname /etc/hosts; do
  if [[ -f "$DVP_IDENTITY_FILE.before-dvp" ]]; then
    sudo cp -a "$DVP_IDENTITY_FILE.before-dvp" "$DVP_IDENTITY_FILE"
  fi
done

sudo systemctl set-default "$DVP_TARGET"
for DVP_DM in lightdm sddm gdm3; do
  if systemctl list-unit-files "$DVP_DM.service" --no-legend 2>/dev/null | \
     grep -q "^$DVP_DM.service"; then
    sudo systemctl enable "$DVP_DM.service" >/dev/null
    break
  fi
done

echo "Desktop boot target restored to: $DVP_TARGET"
echo "The DVP touch sender remains enabled."
echo "Reboot with: sudo reboot"
