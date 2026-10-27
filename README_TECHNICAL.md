# Radxa Zero 3W wireless touch sender

> Technical reference: for final headless appliance installation and recovery,
> use `README.md` in this package.

This folder turns the Radxa into a dedicated USB-touch-to-Wi-Fi bridge:

    screen touch USB -> USB host -> paired DVP touch Wi-Fi -> processing UI

HDMI/Skywire carries the picture separately. The Radxa does not process video,
record, or run detection. The Pi remains the only application computer.

## Connections

- Power the Radxa through its power/OTG USB-C port from a stable 5 V supply.
- Connect the screen's touch USB to the Radxa's USB host port.
- Keep the screen and Radxa grounds common when they use separate regulators.
- HDMI does not carry the USB touch data.

For the antenna connector version of the Zero 3W, attach the correct U.FL/MHF1
antenna and select the external antenna with `sudo rsetup` before range testing.
The external connector is not selected by default.

Do not connect the screen's 5 V output to another powered 5 V source. Use one
defined source for each 5 V rail and verify the Radxa port roles against the
board markings before powering the assembly.

## Install on the Radxa

Use the Radxa's local keyboard/display or a temporary network connection:

```bash
cd ~/DVP/radxa_touch
chmod +x install.sh setup_wifi.sh configure_pair.sh
./install.sh
./configure_pair.sh 1
bash setup_wifi.sh setup
sudo reboot
```

The installer adds the current user to the `input` group, installs
`python3-evdev`, and enables `dvp-touch-sender.service`. The reboot applies the
new group membership. `setup_wifi.sh setup` saves the Pi hotspot but does not
disconnect the Radxa's current Wi-Fi.

The installer also enables `dvp-touch-poweroff.path`. When the user confirms PWR
in the Pi UI, the sender validates a command from the current Pi/session and
writes a private runtime marker. The root-owned helper then shuts down the Radxa.
The UDP-facing sender itself remains an unprivileged process.

During normal service operation, `grab_device: true` gives the sender exclusive
access to the touchscreen. Touches therefore control the Pi DVP interface but
do not also move or click the Radxa desktop. Diagnostic `--monitor` mode never
grabs the device. Stop the service before using that mode.

## Check the USB touchscreen before Wi-Fi

After reboot, list the automatically detected candidates:

```bash
cd ~/DVP/radxa_touch
python3 touch_sender.py --list-devices
sudo systemctl stop dvp-touch-sender
python3 touch_sender.py --monitor
```

Touch the four corners. The terminal should show normalized values near 0 or 1
and `DOWN`, `MOVE`, then `UP`. Press Ctrl+C when done, then restore the service:

```bash
sudo systemctl start dvp-touch-sender
```

If more than one touchscreen-like device is present, set `device_name_contains`
in `config.json` to a stable part of the correct device name. Avoid permanently
setting `/dev/input/eventN` unless necessary because that number can change.

## Connect to the Pi hotspot

First start the matching paired hotspot. Then, on this unit:

```bash
cd ~/DVP/radxa_touch
bash setup_wifi.sh start
bash setup_wifi.sh status
ping -c 3 10.42.0.1
```

Starting the profile can disconnect a terminal using the Radxa's existing
`wlan0` connection. The service reconnects automatically at boot. Inspect it
locally with:

```bash
systemctl status dvp-touch-sender --no-pager
journalctl -u dvp-touch-sender -n 50 --no-pager
```

The operator UI shows `TOUCH READY`, `TOUCH SEARCHING`, `TOUCH LOST`, or
`TOUCH ERROR` without exposing board or network details. Inspect both service
journals if setup needs deeper diagnosis.

## Coordinated poweroff test

With touch controls responding, tap PWR and then confirm. The processing unit sends the command
before closing its recordings; both computers should halt. This does not remove
battery voltage, so use the enclosure's master switch after both systems have
halted. Inspect the previous boot if needed:

```bash
journalctl -u dvp-touch-sender -b -1 --no-pager
journalctl -u dvp-touch-poweroff.service -b -1 --no-pager
```

## Orientation and calibration

Normally the Linux input driver supplies the correct minimum and maximum axis
values automatically. Change only these fields in `config.json` if the local
`--monitor` test proves they are needed:

- `rotation`: `0`, `90`, `180`, or `270`.
- `invert_x` / `invert_y`: reverse one final display axis.
- `grab_device`: keep `true` in the assembled unit so touch does not control the
  Radxa desktop; set it to `false` only for troubleshooting.
- `x_min`, `x_max`, `y_min`, `y_max`: optional measured raw limits; leave all
  four as `null` for automatic values.

After editing the file, restart the sender:

```bash
sudo systemctl restart dvp-touch-sender
```

`destination_host` and `destination_port` remain in `config.json`. The shared
key is overridden by ignored `pairing.json` after `configure_pair.sh` runs. Use
the same unit number and any custom credentials on both matching devices.

