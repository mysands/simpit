# Contributing to SimPit

First — thanks for considering contributing. SimPit is a small project
maintained on weekends, so please be patient and read this guide so we
can spend time on the work, not the protocol.

---

## Bugs vs ideas

* **Found a bug?** Open an issue using the **Bug report** template.
  Include OS, Python version, what you did, what you expected, and
  what happened. If you can attach `agent.log` from the slave or copy
  the activity-log panel from Control, that's gold.
* **Have an idea?** Open an issue using the **Feature request**
  template *before* writing any code. We'd rather discuss design first
  than reject a finished PR.

---

## Development setup

```bash
git clone https://github.com/yourname/simpit
cd simpit
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .[dev]
pytest                              # 234 passing tests
```

The repo is organized into three runnable packages:

```
simpit_common/   shared primitives (no GUI imports)
simpit_slave/    headless agent
simpit_control/  GUI controller
tests/           pytest suite, mirrors package layout
```

---

## What goes where

| If you're changing…           | The change probably belongs in… |
|-------------------------------|----------------------------------|
| Wire format / signing         | `simpit_common`                  |
| OS-specific behaviour         | `simpit_common.platform`         |
| New state-query primitive     | `simpit_common.probes`           |
| What a script does            | (a user's script, not core code) |
| How slaves run scripts        | `simpit_slave.executor`          |
| What STATUS reports           | `simpit_slave.inspector`         |
| Any UI text/widget            | `simpit_control.ui`              |
| Business logic of an action   | `simpit_control.ui.controller`   |
| Pure rendering rules          | `simpit_control.ui.viewmodels`   |

If you can't tell where a change belongs, that's often a sign the
boundary is wrong — open an issue and we'll talk through it.

---

## Coding style

* **Comments.** Every non-trivial function has a docstring explaining
  *why*, not just what. Existing code is the style reference.
* **No new dependencies** without a discussion. The runtime depends on
  exactly one external package (`psutil`); we want to keep that bar
  high.
* **Cross-platform.** No `winreg`, `win32api`, `os.startfile`, or other
  Windows-only APIs. Use `simpit_common.platform` helpers; if the
  abstraction is missing, add it there.
* **Security boundaries.** Subprocess always `shell=False`. Script
  names always validated by `simpit_slave.data.find_script` —
  never bypass that with raw paths.
* **Lint** with `ruff check .` before opening a PR.

---

## Tests

Every PR should include tests for new behaviour. Layout mirrors the
source:

```
tests/common/      pure-logic unit tests
tests/slave/       slave agent (loopback round-trips)
tests/control/     store, slave_link, poller, mock_slave
tests/ui/          view-models, controller, and Tk widgets (Xvfb on CI)
tests/integration/ Control ↔ real Slave end-to-end
```

To run UI tests on a headless machine:

```bash
xvfb-run -a pytest tests/ui
```

---

## Pull requests

1. Fork + branch from `main`.
2. Keep commits focused. One logical change per commit.
3. Update `CHANGELOG.md` under `## [Unreleased]`.
4. Open a PR. The CI must pass.
5. A maintainer will review. Expect questions about *why* a change
   was made and what alternatives you considered.

---

## Security disclosures

**Do NOT open a public issue for security bugs.** Email the maintainer
directly (address in the GitHub profile) and we'll coordinate a fix.
