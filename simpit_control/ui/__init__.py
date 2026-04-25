"""
simpit_control.ui
=================
The tkinter GUI layer.

Submodules
----------
* :mod:`theme`       - colors, fonts, the button factory
* :mod:`viewmodels`  - pure-logic state classes (no Tk imports — testable)
* :mod:`controller`  - operations: add slave, run script, sync (testable)
* :mod:`app`         - Tk root, top-level wiring
* :mod:`widgets`     - reusable bits (slave card, log panel, tooltip)
* :mod:`dialogs`     - modal dialogs for setup/add/edit

The split between viewmodels/controller (pure logic) and widgets/dialogs
(Tk) is deliberate. Anything that tests business logic — "what does the
slave card say when state is OFFLINE?", "what scripts get pushed when
this slave is selected?" — lives in viewmodels and controller and
doesn't need a display server. Anything that tests rendering uses Xvfb
in CI and a real display in development.
"""
