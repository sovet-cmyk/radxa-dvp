# DVP V3 Touch Interface

Version **1.4.0** — 27 October 2026

This repository turns the screen-side Radxa Zero 3W into a dedicated USB-touch
to Wi-Fi interface for DVP. It does not process or receive video. HDMI/Skywire
carries the picture separately; this service reads the Waveshare USB touchscreen,
normalizes its coordinates, and sends authenticated touch events to the DVP
processing unit.

Board and OS names are retained in this installation document for maintenance
accuracy. Normal boot, hostname, services, shutdown, and operation use DVP naming.

## Hardware connection

- Screen touch USB -> Radxa USB host port.
- Stable regulated 5 V -> screen and touch interface power inputs.
- All related grounds common.
- U.FL/IPEX MHF1 50-ohm dual-band antenna attached before power-up.
- Do not connect two separately regulated 5 V outputs together.

## Connect temporarily to internet

The desktop Wi-Fi menu is simplest. From Terminal, the equivalent commands are:

```bash
nmcli device wifi list
sudo nmcli device wifi connect "YOUR_WIFI_NAME" password "YOUR_WIFI_PASSWORD"
ping -c 3 github.com
```

## Enable the external antenna

The onboard antenna is selected by default. Before switching to headless mode:

```bash
sudo rsetup
```

In the text menu choose:

1. `Overlays` / `Configure Device Tree Overlay`;
2. `Manage overlays`;
3. highlight `radxa-zero3-external-antenna`;
4. press Space so it shows `*`;
5. choose `OK`/`Yes`, then press Esc until rsetup exits.

The overlay takes effect after reboot. This is the manufacturer-supported method:
<https://docs.radxa.com/en/zero/zero3/accessories/zero3w-antenna>.

## Fresh installation from GitHub

```bash
sudo apt-get update
sudo apt-get install -y git
cd /home/radxa
git clone https://github.com/sovet-cmyk/DVP-V3-Radxa.git
cd DVP-V3-Radxa
chmod +x install.sh setup_wifi.sh setup_appliance.sh configure_pair.sh restore_desktop.sh touch_sender.py
./setup_appliance.sh 1
```

Use `1` for the controller belonging to production set 1, `2` for production
set 2, or `3` for the sample. The script installs `evdev`, the touch and
authenticated-shutdown services, saves only the selected DVP Wi-Fi profile at
highest autoconnect priority, changes the hostname to `dvp-interface`, suppresses
routine boot output, and switches to the headless `multi-user.target`.

No desktop auto-login is required: the sender starts as a system service before
any user logs in. This is lighter and more reliable than loading a desktop merely
to start the bridge.

After the processing unit's hotspot is available:

```bash
sudo reboot
```

For an existing clone:

```bash
cd /home/radxa/DVP-V3-Radxa
git pull --ff-only
./setup_appliance.sh 1
sudo reboot
```

Use the number of the matching processing unit in place of `1`.

## Pairing the three physical sets

Run the same setup argument on both sides of each set:

| Physical set | Setup argument | Hotspot | Public bench password |
|---|---:|---|---|
| Production 1 | `1` | `DVP-TOUCH1` | `DVP1-7Kp4Nw9Qx2R8m6V` |
| Production 2 | `2` | `DVP-TOUCH2` | `DVP2-6Rm8Qx3Nw7Kp5T2` |
| Sample (`sovet3`) | `3` or `sample` | `DVP-TOUCH` | `t7Kp4Nw9Qx2R8m6V` |

Each selection also uses a different authenticated application key. Setup
removes other saved DVP touch profiles, so this controller does not roam onto
the other product's hotspot. It does not delete unrelated maintenance/internet
Wi-Fi profiles.

To replace the public credentials, use the exact same password and key on the
matching processing unit and screen-control unit:

```bash
./setup_appliance.sh 1 'NEW_WIFI_PASSWORD' 'NEW_TOUCH_KEY_AT_LEAST_16_CHARS'
```

The selection is stored in ignored `pairing.json`, survives `git pull`, and
overrides the public key in `config.json` without modifying tracked files.

## Resolution behavior

The touch interface deliberately does not contain `800x480` or `1920x1080`
screen geometry. It converts the touchscreen's kernel-reported axis range into
normalized `x` and `y` values from 0.0 to 1.0. The processing unit maps those
values onto its logical controls after scaling the picture to the active HDMI
mode. Therefore the same sender works in both cases:

| Video route | Active HDMI mode | Touch packet format |
|---|---:|---|
| Direct screen | 800x480 | normalized 0.0–1.0 |
| Skywire | 1920x1080 | normalized 0.0–1.0 |

Do not add a 1920x1080 multiplier to `touch_sender.py`; doing so would break the
working normalized protocol.

## Verify operation

```bash
systemctl is-active dvp-touch-sender
systemctl is-active dvp-touch-poweroff.path
journalctl -u dvp-touch-sender -n 50 --no-pager
nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
ping -c 3 10.42.0.1
```

Both DVP services should say `active`, and `wlan0` should use the selected
`DVP-TOUCH`, `DVP-TOUCH1`, or `DVP-TOUCH2` profile.

To test the physical touchscreen locally, temporarily stop the service:

```bash
cd /home/radxa/DVP-V3-Radxa
sudo systemctl stop dvp-touch-sender
python3 touch_sender.py --list-devices
python3 touch_sender.py --monitor
```

Touch all four corners. Values should approach 0 or 1 and events should show
`DOWN`, `MOVE`, and `UP`. Press Ctrl+C and restore normal operation:

```bash
sudo systemctl start dvp-touch-sender
```

`grab_device: true` is intentional. It prevents touches from also controlling a
local maintenance desktop while the bridge is running.

## Orientation and calibration

Normally all four manual limits remain `null` in `config.json`. Change only after
the monitor test proves it necessary:

- `rotation`: 0, 90, 180, or 270;
- `invert_x` / `invert_y`: reverse one final axis;
- `x_min`, `x_max`, `y_min`, `y_max`: measured raw limits;
- `device_name_contains`: stable device-name filter if multiple candidates exist.

After a change:

```bash
sudo systemctl restart dvp-touch-sender
```

## Coordinated shutdown

When the operator confirms `SHUT DOWN DVP?`, the processing unit sends a
session-bound authenticated request to the active touch peer. The unprivileged
sender validates it, creates a private runtime marker, and the root-owned helper
powers off this unit. The physical master switch is still required to remove
battery voltage after both systems halt.

## Security and product presentation

The built-in Wi-Fi passwords and touch keys are public bench values. Before
deployment, run the custom `setup_appliance.sh` form on both matching devices.

Headless setup removes the desktop, routine boot text, vendor-named hostname,
and vendor-named service descriptions from normal operator view. This does not
hide the platform from a person with source, shell/root, or physical access, and
it does not remove required third-party licensing.

## Desktop recovery

Connect a keyboard/monitor or use SSH, then run:

```bash
cd /home/radxa/DVP-V3-Radxa
./restore_desktop.sh
sudo reboot
```

The DVP sender remains enabled unless explicitly disabled.

## Validation

```bash
cd /home/radxa/DVP-V3-Radxa
python3 -m unittest discover -s tests -v
bash -n install.sh setup_wifi.sh setup_appliance.sh configure_pair.sh restore_desktop.sh
```

Before sealing the display enclosure, cold-boot both units and test corner taps,
press-and-hold zoom/pan, Wi-Fi reconnection, the external antenna range, and the
confirmed DVP shutdown sequence.
