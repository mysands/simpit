"""launch_xplane.py — Launch X-Plane breaking out of any parent job object."""
import os
import sys
import subprocess

def main():
    xplane_folder = os.environ.get("XPLANE_FOLDER", "").strip()
    sim_exe       = os.environ.get("SIM_EXE_NAME", "").strip()
    if not xplane_folder:
        print("ERROR: XPLANE_FOLDER not set", file=sys.stderr); return 1
    if not sim_exe:
        print("ERROR: SIM_EXE_NAME not set", file=sys.stderr); return 1
    if not xplane_folder.endswith(("\\", "/")):
        xplane_folder += "\\"
    xp_exe = xplane_folder + sim_exe
    if not os.path.exists(xp_exe):
        print(f"ERROR: not found: {xp_exe}", file=sys.stderr); return 1
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if p.info["name"] and p.info["name"].lower() == sim_exe.lower():
                print(f"{sim_exe} already running."); return 0
    except Exception:
        pass

    # CREATE_BREAKAWAY_FROM_JOB: escape PyInstaller's job object — required
    #   on Windows or X-Plane's MicroProfile init crashes (token restrictions).
    # DETACHED_PROCESS: no console, no parent handle inheritance.
    # CREATE_NEW_PROCESS_GROUP: own process group — survives parent exit.
    flags = 0x01000000 | 0x00000008 | 0x00000200

    print(f"Launching: {xp_exe}")
    subprocess.Popen(
        [xp_exe],
        cwd=xplane_folder,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    print("Done.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
