#!/usr/bin/env bash
# disable_custom_scenery.sh
set -euo pipefail
: "${XPLANE_FOLDER:?XPLANE_FOLDER not set}"
: "${SIM_EXE_NAME:?SIM_EXE_NAME not set}"

DISABLED="${XPLANE_FOLDER%/}/Custom Scenery DISABLED"
ENABLED="${XPLANE_FOLDER%/}/Custom Scenery"
DEFAULT="${ENABLED}/DEFAULT"

if pgrep -x "$SIM_EXE_NAME" >/dev/null 2>&1; then
    echo "ERROR: X-Plane is running — quit before toggling scenery" >&2
    exit 1
fi

if [ -d "$DISABLED" ]; then
    echo "Custom Scenery already disabled."
    exit 0
fi

if [ ! -d "$ENABLED" ]; then
    # Fresh install — create skeleton so XP12 doesn't complain
    mkdir -p "$DEFAULT"
    echo "Created empty Custom Scenery/DEFAULT for fresh install."
fi

mv "$ENABLED" "$DISABLED"
echo "Custom Scenery disabled."
