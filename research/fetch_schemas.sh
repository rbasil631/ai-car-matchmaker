#!/usr/bin/env bash
# Fetch the A2UI v0.9.1 spec schemas our tests validate against.
# Pinned to google/A2UI@main spec v0_9_1 (see specs/001-car-matchmaker/research.md).
set -euo pipefail
DIR="$(dirname "$0")/a2ui-schemas"
mkdir -p "$DIR"
BASE="https://raw.githubusercontent.com/google/A2UI/main/specification/v0_9_1"
curl -fsSL "$BASE/json/server_to_client.json" -o "$DIR/server_to_client.json"
curl -fsSL "$BASE/json/common_types.json"     -o "$DIR/common_types.json"
curl -fsSL "$BASE/catalogs/basic/catalog.json" -o "$DIR/catalog.json"
echo "A2UI schemas fetched into $DIR"
