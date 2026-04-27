"""
simpit_common.probes
====================
Extensible state-query primitives.

Background
----------
The original codebase encoded "what's the current state of X?" as
hardcoded checks scattered across the slave and master (e.g. a toggle
button stored its state in a JSON file and trusted that). That's brittle:
external changes (a folder renamed by hand, a process killed) make the
saved state lie. We replace it with **derived state** — the slave reads
the ground truth on demand.

To keep that flexible without proliferating one-off code paths, every
state check is expressed as a *probe*: a small, declarative description
of what to look at. Probes are values stored in ``batfiles.json`` and
sent over the wire; the slave evaluates them. Adding a new state type
means adding a new probe primitive here — nothing else changes.

Wire shape
----------
A probe is a dict::

    {"type": "<probe_type>", "params": {...}}

Probe params may reference environment variables via ``${NAME}`` syntax
(e.g. ``${XPLANE_FOLDER}``). Substitution happens at evaluation time on
the slave, using the env passed by Control with the EXEC/STATUS request.

Adding a new probe
------------------
1. Define an evaluator function with signature
   ``def _eval_<name>(params: dict, env: dict[str, str]) -> ProbeResult``.
2. Register it in :data:`_PROBES`.

Evaluators must never raise on bad input — they return an error
:class:`ProbeResult` instead. The probe engine should be robust against
typos in ``batfiles.json`` because the *user* writes those entries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import platform as sp_platform


# ── Result shape ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProbeResult:
    """Outcome of evaluating a probe.

    `value` is the human/UI-friendly label (e.g. "RUNNING", "OFFLINE",
    "scenery: ON"). `ok` is False only when the probe couldn't be
    evaluated at all (bad config, missing param). A probe that runs
    successfully and finds 'no, that process isn't running' returns
    ok=True with value="not_running".
    """
    ok:    bool
    value: str
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def err(cls, msg: str) -> "ProbeResult":
        return cls(ok=False, value="error", detail={"error": msg})


# ── Env substitution ─────────────────────────────────────────────────────────
_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand(s: str, env: dict[str, str]) -> str:
    """Replace ``${NAME}`` references in `s` from `env`.

    Unknown names are left as-is rather than raising — easier to debug
    visually when something is misnamed (you see ``${XPLANE_FOLDR}`` in
    the error rather than a generic KeyError).
    """
    if not isinstance(s, str):
        return s
    return _ENV_RE.sub(lambda m: env.get(m.group(1), m.group(0)), s)


def resolve_params(params: dict, env: dict[str, str]) -> dict:
    """Return a copy of `params` with ``${VAR}`` references substituted.

    Walks nested dicts and lists so probe params with structured values
    (e.g. a list of paths) all get expanded. Non-string leaves are
    passed through unchanged.

    Used on the Control side to pre-resolve probe params before sending
    a STATUS request — the slave then evaluates literal params and never
    needs to know what ``${XPLANE_FOLDER}`` means. Keeping the
    substitution registry on Control alone avoids shipping the env block
    on every 5-second STATUS poll, and reinforces the rule that Control
    is the sole source of truth for machine-specific values.

    The slave-side evaluators still call :func:`_expand` defensively so
    a probe constructed by older Control versions, or via the manual
    inspector path, still resolves correctly.
    """
    if not isinstance(params, dict):
        return params

    def _walk(v):
        if isinstance(v, str):
            return _expand(v, env)
        if isinstance(v, dict):
            return {k: _walk(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_walk(item) for item in v]
        return v

    return {k: _walk(v) for k, v in params.items()}


# ── Evaluators ───────────────────────────────────────────────────────────────
def _eval_path_exists(params: dict, env: dict[str, str]) -> ProbeResult:
    """True iff a path exists. Works for files, directories, symlinks."""
    raw = params.get("path")
    if not isinstance(raw, str) or not raw:
        return ProbeResult.err("path is required")
    p = Path(_expand(raw, env))
    return ProbeResult(ok=True,
                       value="present" if p.exists() else "absent",
                       detail={"path": str(p)})


def _eval_folder_exists(params: dict, env: dict[str, str]) -> ProbeResult:
    """True iff a path exists AND is a directory.

    Distinct from path_exists so users can detect "the file got created
    where the folder used to be" cases — useful when scripts rename
    folders to files-with-similar-names.
    """
    raw = params.get("path")
    if not isinstance(raw, str) or not raw:
        return ProbeResult.err("path is required")
    p = Path(_expand(raw, env))
    return ProbeResult(ok=True,
                       value="present" if p.is_dir() else "absent",
                       detail={"path": str(p)})


def _eval_file_contains(params: dict, env: dict[str, str]) -> ProbeResult:
    """True iff a file contains a literal substring.

    Used for hosts-file-style checks: "does /etc/hosts contain
    '# X-Plane update block'?" Matching is literal substring (NOT regex)
    because user-supplied regex would be a footgun. Encoding errors
    surface as ok=False with a clear message rather than crashing the
    inspector thread.
    """
    raw_path = params.get("path")
    needle   = params.get("contains")
    if not isinstance(raw_path, str) or not raw_path:
        return ProbeResult.err("path is required")
    if not isinstance(needle, str) or not needle:
        return ProbeResult.err("contains is required")
    p = Path(_expand(raw_path, env))
    needle_expanded = _expand(needle, env)
    if not p.is_file():
        return ProbeResult(ok=True, value="absent",
                           detail={"path": str(p), "reason": "file missing"})
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ProbeResult.err(f"cannot read {p}: {e}")
    found = needle_expanded in text
    return ProbeResult(ok=True,
                       value="present" if found else "absent",
                       detail={"path": str(p), "needle": needle_expanded})


def _eval_process_running(params: dict, env: dict[str, str]) -> ProbeResult:
    """True iff a process with the given name is running.

    Delegates to :func:`simpit_common.platform.process_running` so
    case-folding and Windows-extension tolerance are consistent across
    the codebase.
    """
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return ProbeResult.err("name is required")
    expanded = _expand(name, env)
    running = sp_platform.process_running(expanded)
    return ProbeResult(ok=True,
                       value="running" if running else "not_running",
                       detail={"name": expanded})


def _eval_script_exit_code(params: dict, env: dict[str, str]) -> ProbeResult:
    """Run a script and return its exit code as the probe value.

    Escape hatch for state checks that don't fit the structured probes —
    the user writes a script that prints to stdout and returns 0/1, and
    the probe surfaces that. We deliberately don't capture stdout here
    (we'd rather force users to use a structured probe than build complex
    string-matching logic into this primitive).

    Note: this primitive is implemented by the **slave's** probe engine,
    not here; this evaluator function is a placeholder that returns an
    error if invoked client-side. It exists in the registry so probe
    validation accepts the type.
    """
    return ProbeResult.err(
        "script_exit_code probes must be evaluated by the slave executor"
    )


# ── Registry ─────────────────────────────────────────────────────────────────
ProbeFn = Callable[[dict, dict[str, str]], ProbeResult]

_PROBES: dict[str, ProbeFn] = {
    "path_exists":       _eval_path_exists,
    "folder_exists":     _eval_folder_exists,
    "file_contains":     _eval_file_contains,
    "process_running":   _eval_process_running,
    "script_exit_code":  _eval_script_exit_code,
}


def known_probe_types() -> list[str]:
    """Sorted list of registered probe type names — useful for validation."""
    return sorted(_PROBES.keys())


def evaluate(probe: dict, env: dict[str, str] | None = None) -> ProbeResult:
    """Evaluate a probe described by a config dict.

    Doesn't raise on malformed input — returns ProbeResult.err so the
    inspector thread can keep running and the UI can show the error
    string per-probe rather than crashing the whole status panel.
    """
    if not isinstance(probe, dict):
        return ProbeResult.err("probe must be an object")
    typ = probe.get("type")
    if not isinstance(typ, str):
        return ProbeResult.err("probe type missing or non-string")
    fn = _PROBES.get(typ)
    if fn is None:
        return ProbeResult.err(f"unknown probe type: {typ!r}")
    params = probe.get("params") or {}
    if not isinstance(params, dict):
        return ProbeResult.err("probe params must be an object")
    return fn(params, env or {})


def register(name: str, fn: ProbeFn) -> None:
    """Register a custom probe at runtime.

    Public API so application code (e.g. a plugin) can extend the engine
    without modifying this file. Names are case-sensitive and must not
    collide with existing entries.
    """
    if name in _PROBES:
        raise ValueError(f"probe type already registered: {name!r}")
    _PROBES[name] = fn
