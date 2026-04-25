"""
simpit_control
==============
The GUI controller — the only place where a user actually clicks buttons.

This package is split into a non-UI core (data, slave_link, poller,
mock_slave) and a thin UI layer in :mod:`simpit_control.ui`. Tests drive
the core directly without a Tk loop, which is what makes iterative
debugging tractable.

A SimPit Control instance owns:
* ``slaves.json``    — registry of known slaves
* ``batfiles.json``  — every script registered, with cascade flags
* ``simpit.key``     — shared HMAC secret (also held by every slave)

It never owns scripts on disk in the way a slave does — Control's view
is purely metadata. The actual script content lives inside batfiles.json
when cascade=true; non-cascaded scripts reference paths on Control's
own machine.
"""
from . import data, mock_slave, poller, slave_link

__version__ = "0.1.0"

__all__ = ["data", "slave_link", "poller", "mock_slave", "__version__"]
