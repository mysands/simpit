"""
simpit_slave
============
Headless agent that runs on each slave machine.

Responsibilities:
* Listen for signed UDP/TCP commands from SimPit Control.
* Execute scripts (cascaded from Control or local) safely.
* Report state via the probe engine on demand.
* Broadcast a SLAVE_ONLINE notification on startup.

Non-responsibilities:
* No GUI. Ever. (Logs to stdout/file only.)
* No script registration. The slave never edits its own list of scripts.
* No knowledge of which scripts exist beyond what's on disk in
  ``cascaded/`` (pushed by Control) and ``local/`` (operator-managed).

Entry point: ``python -m simpit_slave``  (see :mod:`simpit_slave.__main__`).
"""
from . import agent, data, executor, inspector

__version__ = "0.1.0"

__all__ = ["data", "executor", "inspector", "agent", "__version__"]
