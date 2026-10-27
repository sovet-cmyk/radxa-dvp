#!/usr/bin/env bash
set -euo pipefail

DVP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DVP_PREVIOUS_TARGET="$DVP_ROOT/.previous_systemd_target"

if [[ "$EUID" -eq 0 ]]; then
  echo "Run this as the normal DVP user, without sudo." >&2
  exit 1
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This package requires the DVP 64-bit touch platform." >&2
  exit 1
fi

if [[ "$#" -gt 0 ]]; then
  bash "$DVP_ROOT/configure_pair.sh" "$@"
fi

chmod +x "$DVP_ROOT/install.sh" "$DVP_ROOT/setup_wifi.sh" \
  "$DVP_ROOT/configure_pair.sh" \
  "$DVP_ROOT/touch_sender.py" "$DVP_ROOT/restore_desktop.sh"

# install.sh adds dependencies only when missing and installs the sender plus
# authenticated poweroff helper as systemd units.
"$DVP_ROOT/install.sh"

if [[ ! -f "$DVP_PREVIOUS_TARGET" ]]; then
  systemctl get-default > "$DVP_PREVIOUS_TARGET"
fi
for DVP_IDENTITY_FILE in /etc/hostname /etc/hosts; do
  if [[ -f "$DVP_IDENTITY_FILE" && ! -e "$DVP_IDENTITY_FILE.before-dvp" ]]; then
    sudo cp -a "$DVP_IDENTITY_FILE" "$DVP_IDENTITY_FILE.before-dvp"
  fi
done

# Preserve the boot configuration once, then suppress the routine console.
# Extlinux is handled first; cmdline.txt remains a safe fallback.
if [[ -f /boot/extlinux/extlinux.conf ]]; then
  if [[ ! -e /boot/extlinux/extlinux.conf.before-dvp ]]; then
    sudo cp -a /boot/extlinux/extlinux.conf /boot/extlinux/extlinux.conf.before-dvp
  fi
  sudo sed -Ei '/^[[:space:]]*APPEND / {
    s#(^|[[:space:]])console=[^[:space:]]+# #g
    s#(^|[[:space:]])earlycon(=[^[:space:]]+)?([[:space:]]|$)# #g
    s#(^|[[:space:]])earlyprintk=[^[:space:]]+# #g
    s#(^|[[:space:]])splash([[:space:]]|$)# #g
    s#(^|[[:space:]])quiet([[:space:]]|$)# #g
    s#(^|[[:space:]])logo\.nologo([[:space:]]|$)# #g
    s#(^|[[:space:]])loglevel=[^[:space:]]+# #g
    s#(^|[[:space:]])systemd\.show_status=[^[:space:]]+# #g
    s#(^|[[:space:]])rd\.systemd\.show_status=[^[:space:]]+# #g
    s#(^|[[:space:]])rd\.udev\.log_level=[^[:space:]]+# #g
    s#(^|[[:space:]])vt\.global_cursor_default=[^[:space:]]+# #g
    s|$| quiet logo.nologo loglevel=0 systemd.show_status=false rd.systemd.show_status=false rd.udev.log_level=0 vt.global_cursor_default=0|
  }' /boot/extlinux/extlinux.conf
else
  for DVP_BOOT_FILE in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [[ -f "$DVP_BOOT_FILE" ]]; then
      if [[ ! -e "$DVP_BOOT_FILE.before-dvp" ]]; then
        sudo cp -a "$DVP_BOOT_FILE" "$DVP_BOOT_FILE.before-dvp"
      fi
      sudo sed -Ei '1 {
        s#(^|[[:space:]])console=[^[:space:]]+# #g
        s#(^|[[:space:]])earlycon(=[^[:space:]]+)?([[:space:]]|$)# #g
        s#(^|[[:space:]])earlyprintk=[^[:space:]]+# #g
        s#(^|[[:space:]])splash([[:space:]]|$)# #g
        s#(^|[[:space:]])quiet([[:space:]]|$)# #g
        s#(^|[[:space:]])logo\.nologo([[:space:]]|$)# #g
        s#(^|[[:space:]])loglevel=[^[:space:]]+# #g
        s#(^|[[:space:]])systemd\.show_status=[^[:space:]]+# #g
        s#(^|[[:space:]])rd\.systemd\.show_status=[^[:space:]]+# #g
        s#(^|[[:space:]])rd\.udev\.log_level=[^[:space:]]+# #g
        s#(^|[[:space:]])vt\.global_cursor_default=[^[:space:]]+# #g
      }' "$DVP_BOOT_FILE"
      for DVP_TOKEN in quiet logo.nologo loglevel=0 systemd.show_status=false \
        rd.systemd.show_status=false rd.udev.log_level=0 vt.global_cursor_default=0; do
        if ! grep -Eq "(^|[[:space:]])${DVP_TOKEN}([[:space:]]|$)" "$DVP_BOOT_FILE"; then
          sudo sed -i "1 s|[[:space:]]*$| $DVP_TOKEN|" "$DVP_BOOT_FILE"
        fi
      done
      break
    fi
  done
fi

# No desktop login is required. The touch bridge is a boot service.
sudo systemctl enable NetworkManager dvp-touch-sender dvp-touch-poweroff.path >/dev/null
sudo systemctl set-default multi-user.target

# Use product-neutral network/console identification in normal operation.
sudo hostnamectl set-hostname dvp-interface
if grep -Eq '^127\.0\.1\.1[[:space:]]+' /etc/hosts; then
  sudo sed -Ei 's|^127\.0\.1\.1[[:space:]]+.*|127.0.1.1\tdvp-interface|' /etc/hosts
fi

# Save the DVP hotspot profile without interrupting the current internet
# connection. At reboot it has the highest autoconnect priority.
bash "$DVP_ROOT/setup_wifi.sh" setup

echo
echo "DVP touch appliance configuration is complete."
if grep -qs 'radxa-zero3-external-antenna' /boot/extlinux/extlinux.conf 2>/dev/null; then
  echo "External antenna overlay was found in the boot configuration."
else
  echo "IMPORTANT: run 'sudo rsetup' and enable the external-antenna overlay before reboot."
fi
echo "Reboot after the paired DVP touch hotspot is available: sudo reboot"
echo "The desktop will not start; dvp-touch-sender starts without login."
echo "Recovery from a console: $DVP_ROOT/restore_desktop.sh"
