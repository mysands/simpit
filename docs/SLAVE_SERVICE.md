# Running the slave as a service

The slave agent is a long-running process. Running it from a terminal
is fine for testing, but for production you want it to start on boot,
restart on failure, and survive RDP/SSH disconnects.

This guide covers the canonical patterns on each platform.

---

## Linux: systemd

Create `/etc/systemd/system/simpit-slave.service`:

```ini
[Unit]
Description=SimPit slave agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=simpit
Group=simpit
ExecStart=/usr/bin/simpit-slave --no-prompt
# Restart on failure but back off so we don't hammer the system
# if the agent is crashing on every start.
Restart=on-failure
RestartSec=10s

# Hardening: the agent doesn't need any of these.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/simpit/.config/simpit-slave
ProtectKernelTunables=yes
ProtectKernelModules=yes

[Install]
WantedBy=multi-user.target
```

Setup:

```bash
sudo useradd -r -m -d /home/simpit -s /usr/sbin/nologin simpit
# Provision the key BEFORE starting the service:
sudo -u simpit simpit-slave --no-prompt   # will fail; copy the path it prints
# Manually drop simpit.key into the printed path, then:
sudo systemctl daemon-reload
sudo systemctl enable --now simpit-slave
sudo systemctl status simpit-slave
```

Logs:

```bash
journalctl -u simpit-slave -f
```

---

## Windows: Task Scheduler

Recommended over NSSM for most users — Windows already provides this.

1. Open **Task Scheduler** → **Create Task** (not "Basic Task").
2. **General** tab:
   - Name: `SimPit Slave`
   - Run whether user is logged on or not
   - Run with highest privileges (only if the slave needs admin to
     run scripts that modify hosts file etc.)
3. **Triggers** tab → New:
   - Begin the task: At system startup
   - Delay: 30 seconds (gives network time to come up)
4. **Actions** tab → New:
   - Action: Start a program
   - Program: `C:\Path\To\Python\python.exe`
   - Arguments: `-m simpit_slave --no-prompt`
5. **Conditions** tab:
   - Uncheck "Start the task only if the computer is on AC power"
   - (Other defaults are fine.)
6. **Settings** tab:
   - Allow task to be run on demand
   - If the task fails, restart every 1 minute, attempt 3 times

Provision the key BEFORE first start: drop `simpit.key` into
`%APPDATA%\simpit-slave\simpit.key` for whichever user account the
task runs as.

Logs go to wherever you redirect stdout/stderr. The simplest fix is to
run via a wrapper batch file:

```bat
@echo off
cd /d "%LOCALAPPDATA%\simpit-slave"
"C:\Path\To\Python\python.exe" -m simpit_slave --no-prompt >> agent.log 2>&1
```

…and point the task at that wrapper.

---

## macOS: launchd

Create `~/Library/LaunchAgents/com.simpit.slave.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.simpit.slave</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/simpit-slave</string>
    <string>--no-prompt</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/tmp/simpit-slave.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/simpit-slave.err</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.simpit.slave.plist
launchctl list | grep simpit
```

---

## Common gotchas

* **Service starts before the network is up.** systemd handles this
  via `network-online.target`. On Windows, the trigger delay handles
  it. On macOS, `KeepAlive` will retry until the network comes up.
* **Service can't find the key.** Each platform stores user-profile
  data in a different place. Run the slave once interactively first
  to create the directory structure, drop the key in, then enable
  the service.
* **Firewall blocks the ports.** UDP 49100 and TCP 49101 by default.
  Allow them on the local subnet only.
* **Permissions on `simpit.key`.** On POSIX it should be 0600 owned
  by the service user. The agent's own `save_key` does this; if you
  drop the file in by hand, run `chmod 600 simpit.key` after.
