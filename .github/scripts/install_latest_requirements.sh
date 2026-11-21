#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install pytest-homeassistant-custom-component

version=$(python -c "from homeassistant.const import __version__; print(__version__)")
source="https://raw.githubusercontent.com/home-assistant/core/${version}/homeassistant"

aioesphomeapi=$(curl -fsSL "${source}/components/esphome/manifest.json" | python -c "import json, sys; print(next(item for item in json.load(sys.stdin)['requirements'] if item.startswith('aioesphomeapi')))")

python -m pip install -c "${source}/package_constraints.txt" -r requirements_test_latest.txt "${aioesphomeapi}"

echo "Testing against Home Assistant ${version} with ${aioesphomeapi}"
