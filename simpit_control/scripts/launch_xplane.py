"""
launch_xplane.py — Launch X-Plane directly from the slave agent process.
Required env: XPLANE_FOLDER, SIM_EXE_NAME
"""
import os
import sys


def main():
    xplane_folder = os.environ.get("XPLANE_FOLDER", "").strip()
    sim_exe       = os.environ.get("SIM_EXE_NAME", "").strip()

    if not xplane_folder:
        print("ERROR: XPLANE_FOLDER not set", file=sys.stderr)
        return 1
    if not sim_exe:
        print("ERROR: SIM_EXE_NAME not set", file=sys.stderr)
        return 1

    if not xplane_folder.endswith(("\\", "/")):
        xplane_folder += "\\"

    xp_exe = xplane_folder + sim_exe

    if not os.path.exists(xp_exe):
        print(f"ERROR: not found: {xp_exe}", file=sys.stderr)
        return 1

    # Check if already running
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            if proc.info["name"] and \
               proc.info["name"].lower() == sim_exe.lower():
                print(f"{sim_exe} is already running.")
                return 0
    except Exception:
        pass

    print(f"Launching: {xp_exe}")

    # os.startfile() = ShellExecute("open") — identical to double-clicking
    # the exe in Explorer. Uses the interactive desktop session automatically,
    # needs no environment variables, no subprocess spawning.
    os.startfile(xp_exe)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
