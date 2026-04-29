#!/usr/bin/env bash
# launch_xplane.sh  —  Start X-Plane (SimPit standard script)
# Required env: XPLANE_FOLDER  SIM_EXE_NAME
set -euo pipefail

: "${XPLANE_FOLDER:?XPLANE_FOLDER not set}"
: "${SIM_EXE_NAME:?SIM_EXE_NAME not set}"

XP_EXE="${XPLANE_FOLDER%/}/${SIM_EXE_NAME}"

if [ ! -f "$XP_EXE" ]; then
    echo "ERROR: not found: $XP_EXE" >&2
    exit 1
fi

if pgrep -x "$SIM_EXE_NAME" >/dev/null 2>&1; then
    echo "X-Plane already running."
    exit 0
fi

nohup "$XP_EXE" >/dev/null 2>&1 &
echo "Launched: $XP_EXE"
