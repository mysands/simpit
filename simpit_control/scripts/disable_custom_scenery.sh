#!/usr/bin/env bash
# disable_custom_scenery.sh — POSIX equivalent of the .bat.
#
# Spec:
#   1. Look under XPLANE_FOLDER for "Custom Scenery".
#        exists  -> rename to "Custom Scenery DISABLED"
#        missing -> ERROR
#   2. Then look for "Custom Scenery DEFAULT".
#        exists  -> rename to "Custom Scenery"
#        missing -> mkdir empty "Custom Scenery DEFAULT", then rename to "Custom Scenery"
#
# End state: fresh "Custom Scenery" in place, previous active
# scenery preserved as "Custom Scenery DISABLED".
#
# Required env: XPLANE_FOLDER
# Optional env: SIM_EXE_NAME (used as a safety check)
set -u

log() { printf '[disable_custom_scenery] %s\n' "$*"; }
err() { printf 'ERROR: %s\n' "$*" >&2; }

# ── Pre-flight ──────────────────────────────────────────────
if [ -z "${XPLANE_FOLDER:-}" ]; then
    err "XPLANE_FOLDER not set"
    exit 1
fi

if [ ! -d "$XPLANE_FOLDER" ]; then
    err "XPLANE_FOLDER does not exist: $XPLANE_FOLDER"
    exit 1
fi

ENABLED="$XPLANE_FOLDER/Custom Scenery"
DISABLED="$XPLANE_FOLDER/Custom Scenery DISABLED"
DEFAULT="$XPLANE_FOLDER/Custom Scenery DEFAULT"

log "XPLANE_FOLDER=$XPLANE_FOLDER"

# Refuse to touch folders if X-Plane has them open
if [ -n "${SIM_EXE_NAME:-}" ]; then
    if pgrep -x -- "$SIM_EXE_NAME" >/dev/null 2>&1; then
        err "$SIM_EXE_NAME is running; quit X-Plane before disabling scenery"
        exit 1
    fi
fi

# Refuse to clobber an existing DISABLED
if [ -e "$DISABLED" ]; then
    err "\"Custom Scenery DISABLED\" already exists; remove it manually before re-running"
    exit 1
fi

# ── Step 1: Custom Scenery -> Custom Scenery DISABLED ───────
if [ ! -d "$ENABLED" ]; then
    err "\"Custom Scenery\" not found under XPLANE_FOLDER; cannot disable"
    exit 1
fi

log 'renaming "Custom Scenery" to "Custom Scenery DISABLED"'
if ! mv -- "$ENABLED" "$DISABLED"; then
    err "rename of \"Custom Scenery\" failed"
    exit 1
fi

# ── Step 2: Custom Scenery DEFAULT -> Custom Scenery ────────
if [ -d "$DEFAULT" ]; then
    log 'renaming "Custom Scenery DEFAULT" to "Custom Scenery"'
    if ! mv -- "$DEFAULT" "$ENABLED"; then
        err "rename of \"Custom Scenery DEFAULT\" failed"
        exit 1
    fi
else
    log '"Custom Scenery DEFAULT" not present; creating empty then renaming'
    if ! mkdir -- "$DEFAULT"; then
        err "mkdir of \"Custom Scenery DEFAULT\" failed"
        exit 1
    fi
    if ! mv -- "$DEFAULT" "$ENABLED"; then
        err "rename of newly created \"Custom Scenery DEFAULT\" failed"
        exit 1
    fi
fi

log 'OK: scenery disabled; previous content saved as "Custom Scenery DISABLED"'
exit 0
