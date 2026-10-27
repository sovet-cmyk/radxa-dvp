#!/usr/bin/env bash
set -euo pipefail

DVP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DVP_PAIRING_FILE="$DVP_ROOT/pairing.json"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./configure_pair.sh {1|2|3|sample}
  ./configure_pair.sh {1|2|3|sample} WIFI_PASSWORD TOUCH_KEY

Use the same unit number and optional custom credentials on the matching
processing unit and screen-control unit. Unit 3/sample retains DVP-TOUCH.
EOF
  exit 2
}

if [[ "$#" -ne 1 && "$#" -ne 3 ]]; then
  usage
fi

DVP_UNIT_ID="$1"
case "$DVP_UNIT_ID" in
  1)
    DVP_SSID="DVP-TOUCH1"
    DVP_DEFAULT_PASSWORD="DVP1-7Kp4Nw9Qx2R8m6V"
    DVP_DEFAULT_KEY="dvp3-touch-unit1-4K8p2N7x9Q6m"
    ;;
  2)
    DVP_SSID="DVP-TOUCH2"
    DVP_DEFAULT_PASSWORD="DVP2-6Rm8Qx3Nw7Kp5T2"
    DVP_DEFAULT_KEY="dvp3-touch-unit2-6R8n3W5y1T9k"
    ;;
  3|sample)
    DVP_UNIT_ID="sample"
    DVP_SSID="DVP-TOUCH"
    DVP_DEFAULT_PASSWORD="t7Kp4Nw9Qx2R8m6V"
    DVP_DEFAULT_KEY="dvp3-touch-v1-7F4m9Q2x8K6p"
    ;;
  *) usage ;;
esac

DVP_WIFI_PASSWORD="${2:-$DVP_DEFAULT_PASSWORD}"
DVP_TOUCH_KEY="${3:-$DVP_DEFAULT_KEY}"

if (( ${#DVP_WIFI_PASSWORD} < 8 || ${#DVP_WIFI_PASSWORD} > 63 )); then
  echo "Wi-Fi password must contain 8 to 63 characters." >&2
  exit 1
fi
if (( ${#DVP_TOUCH_KEY} < 16 )); then
  echo "Touch key must contain at least 16 characters." >&2
  exit 1
fi
if [[ "$DVP_WIFI_PASSWORD" == *$'\n'* || "$DVP_TOUCH_KEY" == *$'\n'* ]]; then
  echo "Credentials cannot contain a newline." >&2
  exit 1
fi

python3 - "$DVP_PAIRING_FILE" "$DVP_UNIT_ID" "$DVP_SSID" \
  "$DVP_WIFI_PASSWORD" "$DVP_TOUCH_KEY" <<'PY'
import json
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
payload = {
    "unit_id": sys.argv[2],
    "ssid": sys.argv[3],
    "wifi_password": sys.argv[4],
    "touch_shared_key": sys.argv[5],
}
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
temporary.replace(path)
PY

echo "Configured this screen-control unit for $DVP_SSID."
echo "Run ./setup_appliance.sh (or apply Wi-Fi and restart the service) to activate it."
