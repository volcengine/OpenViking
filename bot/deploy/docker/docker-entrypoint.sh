#!/bin/bash
set -e

VIKINGBOT_DIR="/root/.vikingbot"
BRIDGE_SRC="/opt/vikingbot-bridge"
BRIDGE_DEST="$VIKINGBOT_DIR/bridge"

# Ensure base directories exist (in case volume is newly mounted)
mkdir -p "$VIKINGBOT_DIR/workspace" "$VIKINGBOT_DIR/sessions" "$VIKINGBOT_DIR/sandboxes" "$BRIDGE_DEST"

# Copy bridge files from image if not yet initialized on the volume
# (bridge is pre-built into /opt/vikingbot-bridge at image build time)
if [ -d "$BRIDGE_SRC" ] && [ ! -f "$BRIDGE_DEST/package.json" ]; then
    echo "[vikingbot] Initializing bridge files to $BRIDGE_DEST ..."
    cp -r "$BRIDGE_SRC/." "$BRIDGE_DEST/"
    echo "[vikingbot] Bridge initialized."
fi

exec vikingbot "$@"
