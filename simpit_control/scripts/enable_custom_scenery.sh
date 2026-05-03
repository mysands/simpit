#!/usr/bin/env bash
# enable_custom_scenery.sh — POSIX equivalent of the .bat.
#
# Symmetric inverse of disable_custom_scenery.
#
# Spec:
#   1. Look under XPLANE_FOLDER for "Custom Scenery DISABLED".
#        exists  -> proceed
#        missing -> ERROR
#   2. Look for "Custom Scenery".
#        exists  -> rename to "Custom Scenery DEFAULT" (preserves
#                   baseline; refuses if "Custom Scenery DEFAULT"
#                   already exists)
#        missing -> skip
#   3. Rename "Custom Scenery DISABLED" -> "Custom Scenery".
#
# Required env: XPLANE_FOLDER
# Optional env: SIM_EXE_NAME (used as a safety check)
set -u

log() { printf '[enable_custom_scenery] %s\n' "$*"; }
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
        err "$SIM_EXE_NAME is running; quit X-Plane before enabling scenery"
        exit 1
    fi
fi

# ── Step 1: confirm DISABLED exists ─────────────────────────
if [ ! -d "$DISABLED" ]; then
    err "\"Custom Scenery DISABLED\" not found; nothing to re-enable"
    exit 1
fi

# ── Step 2: Custom Scenery -> Custom Scenery DEFAULT ────────
if [ -d "$ENABLED" ]; then
    if [ -e "$DEFAULT" ]; then
        err "both \"Custom Scenery\" and \"Custom Scenery DEFAULT\" exist; cannot preserve baseline"
        err "remove or rename one of them manually before re-running"
        exit 1
    fi
    log 'preserving baseline: "Custom Scenery" to "Custom Scenery DEFAULT"'
    if ! mv -- "$ENABLED" "$DEFAULT"; then
        err 'rename of "Custom Scenery" failed'
        exit 1
    fi
else
    log 'no current "Custom Scenery"; nothing to preserve as DEFAULT'
fi

# ── Step 3: Custom Scenery DISABLED -> Custom Scenery ───────
log 'renaming "Custom Scenery DISABLED" to "Custom Scenery"'
if ! mv -- "$DISABLED" "$ENABLED"; then
    err 'rename of "Custom Scenery DISABLED" failed'
    exit 1
fi

log 'OK: scenery enabled'
exit 0
