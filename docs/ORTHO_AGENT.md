# Ortho scenery cache agent

## Overview

`simpit_ortho_agent` is a per-machine background helper that keeps a
moving "bubble" of Ortho4XP texture atlases warm in the local rclone
VFS cache, so scenery is already on local disk before X-Plane asks for
it. It runs on **every** X-Plane machine (master, CENTERLEFT, RIGHT):

* It subscribes to the X-Plane master's position over RREF UDP
  (lat/lon/groundspeed/**ground track** — `hpath`, not heading, so a
  crosswind crab can't skew the prefetch direction).
* Around the current position *and* a 45-second along-track projection
  it computes a **keep set**: rings of atlases (default 4 rings → a
  9×9 block) on the atlas grid at each scenery folder's base zoom,
  including any higher-zoom airport patches inside the ring.
* A single worker thread **primes** each keep-set atlas (sequential
  8 MB reads to EOF — with `--vfs-cache-mode full` a read IS a cache
  fill) and then **re-touches** it every `touch_interval_seconds` so
  its LRU slot stays fresh.
* There is deliberately **no evictor**: rclone's rc API cannot evict a
  single file (`vfs/forget` is metadata-only). Atlases that leave the
  keep set simply stop being touched, and rclone's own LRU at the
  `--vfs-cache-max-size` cap removes the coldest files. The agent
  never writes into the cache directory and never issues rc calls
  other than `vfs/stats`. (A bulk eviction burst once deleted
  memory-mapped .dds files mid-flight → fatal EXCEPTION_IN_PAGE_ERROR;
  see the mount notes below.)

States: `SIM_OFFLINE` (no RREF for >10 s → pause, cache untouched),
`IDLE` (parked, keep set stable → touches only), `ACTIVE` (priming).

Why per-machine: the rclone VFS cache is strictly local — priming
requires a local read on the machine that owns the cache, and the
master serves RREF to any number of subscribers, so each agent works
alone with zero coordination. The agent is machine-local
infrastructure (like the OS page cache), not command/script logic: no
new Control→slave protocol messages.

## How to run

1. Make sure the ortho mount is up (the installer's
   `ortho_mount.bat` runs at logon — window titled "SimPit Ortho
   Mount"). If the mount is down and `supervise_mount` is true, the
   agent launches rclone itself as a fallback and waits for the drive
   (the drive letter only appears after rclone reconciles its cache —
   ~3 min at 200 GB — so a slow appearance is normal). It never
   double-mounts: an answering rc port means a mount already exists.
2. Start the agent:

   ```bat
   python -m simpit_ortho_agent [-v] [--config PATH]
   ```

   or the frozen build `simpit-ortho-agent.exe` (build with
   `build_ortho_agent.bat`). Default config location:
   `%APPDATA%\simpit-ortho-agent\ortho_agent.json`; the log
   (`agent.log`) sits next to it.
3. Deployment on Windows is an **at-logon Task Scheduler task in the
   interactive session**, NOT a service — the agent only needs file
   reads and localhost/LAN UDP, and services live in an isolated
   session. Linux/macOS: portable code, deployment stub only in v1
   (a systemd user unit calling `simpit-ortho-agent` works).

## Configuration

`ortho_agent.json` (JSON per RULES.md §6 rule 8 — machine-distributed,
script-rewritten config). Single loader:
`simpit_common.ortho_config`. Load order via `load_effective()`:

1. local cached copy (bootstrap — also tells the agent where the fleet
   folder is),
2. fleet base `<fleet_config_dir>\ortho_agent.json` (written by
   Control's Ortho Cache dialog). `fleet_config_dir` is site-specific
   and defaults to **empty = fleet distribution off**, so a setup
   without a NAS never probes the network; on this fleet set it to
   `\\RandhawaNAS\XPlane12\simpit` (a sibling of the scenery share,
   outside Custom Scenery so X-Plane's scan never sees it),
3. per-machine overlay `ortho_agent.<hostname>.json` (lowercase, only
   the keys present override).

The fleet config is re-read on every SIM_OFFLINE→ACTIVE transition, so
Control edits reach running agents at the next sim session. Endpoint
fields (master IP/port, mount root) need an agent restart.

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | per-machine kill switch (overlay-friendly) |
| `master_ip` / `xp_udp_port` | `127.0.0.1` / `49000` | RREF endpoint. The localhost default is the recommended setting on every machine: external-visual instances serve live position/gs/hpath identical to the master's (verified in flight 2026-07-19), so no cross-machine UDP or per-machine IP config is needed |
| `mount_root` | `X:/` | rclone mount drive |
| `remote_rel_root` | `""` | mount-relative path down to Custom Scenery |
| `cache_max_gb` | `160` | mount cache cap (460 on CENTERLEFT) |
| `rc_addr` | `127.0.0.1:5572` | rclone rc (health checks only) |
| `cache_dir` | `""` | rclone `--cache-dir` (`E:\rclone-cache` on CENTERLEFT) |
| `supervise_mount` | `true` | launch rclone if the mount is down |
| `active_zoom` | `18` | scenery-set folder label to prefer (18/16); the Z16/Z18 toggle script should rewrite this |
| `n_rings` | `4` | keep-set ring radius in atlas units (9×9 block ≈ 44 km at BI17, ~0.9 GB) |
| `lookahead_seconds` | `45` | along-track projection |
| `poll_hz` | `1` | position sample rate / engine tick |
| `touch_interval_seconds` | `60` | keep-warm re-touch cadence |
| `prime_mbps` | `24` | primer read-bandwidth cap, MB/s (0 = off). Keep it modest: unthrottled bursts starve X-Plane's reads on the shared cache drive → micro-stutters (measured in flight 2026-07-19); staying ahead only needs ~5–8 MB/s |
| `heading_offset_deg` | `0` | reserved (v2 side-view bias — defined, not applied) |
| `fleet_config_dir` | `""` | folder of the authoritative fleet copy + overlays; empty = local-only |

## Examples

Run with a specific config and verbose logging:

```bat
python -m simpit_ortho_agent --config D:\simpit\ortho_agent.json -v
```

Typical log during flight:

```
12:04:11 INFO  simpit.ortho.engine: SIM_OFFLINE -> ACTIVE
12:04:11 INFO  simpit.ortho.index: indexed zOrtho4XP_Z18_+42-073: 234 atlases, zooms [19, 18, 17, 16] (base 16)
12:04:12 INFO  simpit.ortho.primer: primed zOrtho4XP_Z18_+42-073/textures/192848_156880_BI19.dds (11.2 MB in 0.13s)
```

Verify the whole chain on any machine (no repo needed — copy
`tests/live/ortho_checks.py` + `tests/live/verify_live.py`):

```bat
python verify_live.py ortho_agent.json
```

On a dev checkout the same checks run against the production tilemath
and index code: `pytest -m live tests/live -v`.

## Troubleshooting

* **`mount X:/ did not appear within 60s`** — rclone is probably still
  reconciling its cache (minutes at hundreds of GB). The agent keeps
  retrying; check the "SimPit Ortho Mount" window / `ortho_mount.log`.
* **State stuck in `SIM_OFFLINE`** — no RREF from the configured
  endpoint (default: this machine's own X-Plane on `127.0.0.1:49000`).
  X-Plane must be running locally — or, if `master_ip` points at
  another machine, UDP must be allowed through the firewall/rasrouter;
  `verify_live.py` prints the exact failure.
* **Atlases keep getting evicted** (`cache-write` check fails, or
  primed files vanish) — the mount's `--vfs-cache-max-age` is wrong
  (must be huge, e.g. `8760h`; `0` purges immediately) or the cap is
  smaller than the working set. Never "fix" this by deleting cache
  files while X-Plane runs.
* **Sim crashes with `EXCEPTION_IN_PAGE_ERROR`** — an eviction burst
  hit memory-mapped textures. Keep `--vfs-cache-poll-interval` SHORT
  (2 m): overshoot ≈ poll interval × NAS throughput, and short polls
  keep evictions small and cold.
* **Nothing primes over water / unbuilt areas** — expected: squares
  with no `zOrtho4XP_*` folder contribute nothing to the keep set.
* **Wrong folder zoom being warmed** — `active_zoom` selects only the
  preferred *folder label*; the agent falls back to the other label
  per square (hybrid profile), and actual atlas zooms always come from
  the folder's own textures index.
