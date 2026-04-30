"""
setup_xplane_task.py — Create SimPit\LaunchXPlane scheduled task.
Required env: XPLANE_FOLDER, SIM_EXE_NAME

Uses Windows Task Scheduler COM API directly — no subprocess spawning,
no pipe inheritance issues, no interactive prompts.
"""
import os
import sys

def main():
    xplane_folder = os.environ.get("XPLANE_FOLDER", "").strip()
    sim_exe       = os.environ.get("SIM_EXE_NAME", "").strip()

    print(f"[DEBUG] XPLANE_FOLDER={xplane_folder}")
    print(f"[DEBUG] SIM_EXE_NAME={sim_exe}")

    if not xplane_folder:
        print("ERROR: XPLANE_FOLDER not set", file=sys.stderr)
        return 1
    if not sim_exe:
        print("ERROR: SIM_EXE_NAME not set", file=sys.stderr)
        return 1

    # Auto-append trailing backslash
    if not xplane_folder.endswith(("\\", "/")):
        xplane_folder += "\\"

    xp_exe = xplane_folder + sim_exe
    print(f"[DEBUG] XP_EXE={xp_exe}")

    if not os.path.exists(xp_exe):
        print(f"ERROR: executable not found: {xp_exe}", file=sys.stderr)
        return 1
    print("[DEBUG] executable found OK")

    if sys.platform != "win32":
        print("ERROR: scheduled tasks are Windows-only", file=sys.stderr)
        return 1

    print("[DEBUG] registering task via Task Scheduler COM API...")
    try:
        import win32com.client
        import win32con

        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()

        # Get or create SimPit folder
        root = scheduler.GetFolder("\\")
        try:
            folder = scheduler.GetFolder("\\SimPit")
        except Exception:
            folder = root.CreateFolder("SimPit")
            print("[DEBUG] created SimPit task folder")

        # Build task definition
        task_def = scheduler.NewTask(0)

        # Principal: interactive token, highest available
        principal = task_def.Principal
        principal.LogonType = 3        # TASK_LOGON_INTERACTIVE_TOKEN
        principal.RunLevel  = 1        # TASK_RUNLEVEL_HIGHEST

        # Settings
        settings = task_def.Settings
        settings.Enabled                    = True
        settings.StopIfGoingOnBatteries     = False
        settings.DisallowStartIfOnBatteries = False
        settings.MultipleInstances          = 3  # TASK_INSTANCES_IGNORE_NEW
        settings.ExecutionTimeLimit         = "PT2H"

        # Action
        action = task_def.Actions.Create(0)   # TASK_ACTION_EXEC
        action.Path             = xp_exe
        action.WorkingDirectory = xplane_folder

        # Register (create or overwrite)
        TASK_CREATE_OR_UPDATE = 6
        TASK_LOGON_INTERACTIVE_TOKEN = 3
        folder.RegisterTaskDefinition(
            "LaunchXPlane",
            task_def,
            TASK_CREATE_OR_UPDATE,
            "", "", TASK_LOGON_INTERACTIVE_TOKEN
        )

        print("SUCCESS: Task SimPit\\LaunchXPlane created.")
        print(f"X-Plane: {xp_exe}")
        return 0

    except ImportError:
        pass  # win32com not available, fall through to PowerShell path

    print("[DEBUG] win32com not available, trying PowerShell cmdlet...")
    try:
        import subprocess
        ps_cmd = (
            f"$a = New-ScheduledTaskAction -Execute '{xp_exe}' "
            f"  -WorkingDirectory '{xplane_folder}';"
            f"$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew "
            f"  -ExecutionTimeLimit (New-TimeSpan -Hours 2);"
            f"$p = New-ScheduledTaskPrincipal -LogonType Interactive "
            f"  -RunLevel Highest -UserId $env:USERNAME;"
            f"Register-ScheduledTask -TaskName 'SimPit\\LaunchXPlane' "
            f"  -Action $a -Settings $s -Principal $p -Force | Out-Null;"
            f"exit 0"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL
        )
        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        print(f"[DEBUG] PowerShell exit code: {result.returncode}")
        if result.returncode == 0:
            print("SUCCESS: Task SimPit\\LaunchXPlane created.")
            return 0
        return 1

    except subprocess.TimeoutExpired:
        print("ERROR: PowerShell timed out after 30s", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
