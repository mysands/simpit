#!/usr/bin/env bash
# enable_custom_scenery.sh
set -euo pipefail
: "${XPLANE_FOLDER:?XPLANE_FOLDER not set}"
: "${SIM_EXE_NAME:?SIM_EXE_NAME not set}"

DISABLED="${XPLANE_FOLDER%/}/Custom Scenery DISABLED"
ENABLED="${XPLANE_FOLDER%/}/Custom Scenery"

if pgrep -x "$SIM_EXE_NAME" >/dev/null 2>&1; then
    echo "ERROR: X-Plane is running — quit before toggling scenery" >&2
    exit 1
fi

if [ ! -d "$DISABLED" ]; then
    echo "Custom Scenery already enabled (or folder missing)."
    exit 0
fi

if [ -d "$ENABLED" ]; then
    echo "ERROR: Both 'Custom Scenery' and 'Custom Scenery DISABLED' exist" >&2
    exit 1
fi

mv "$DISABLED" "$ENABLED"
echo "Custom Scenery enabled."
