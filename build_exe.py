"""
Build ModDex into a single ModDex.exe.

Run this ONCE, on the Windows machine, from inside the ModDex folder:

    pip install pyinstaller
    python build_exe.py

Out comes  dist\\ModDex.exe  -- one file. Copy it (plus credentials.json if you
kept it external) to any shop tablet and double-click. No Python, no gcloud,
no login, no setup.

Note: an .exe can only be built on Windows. PyInstaller does not cross-compile,
which is why this script ships as source instead of a prebuilt binary.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEP = ";" if os.name == "nt" else ":"

# Folders that must ride along inside the exe.
BUNDLE = ["ui", "assets", "catalog"]


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:\n\n    pip install pyinstaller\n")
        return 1

    missing = [d for d in BUNDLE if not os.path.isdir(os.path.join(HERE, d))]
    if missing:
        print("Missing folders: %s\nRun this from inside the ModDex folder."
              % ", ".join(missing))
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "ModDex",
        "--noconfirm",
        "--clean",
        # console window stays visible: it is the only place a crash is readable,
        # and it is how the shop closes the app. Swap to --noconsole once stable.
        "--console",
    ]

    for d in BUNDLE:
        cmd += ["--add-data", "%s%s%s" % (os.path.join(HERE, d), SEP, d)]

    # Bake the credentials in so the exe is genuinely self-contained.
    # Leave it out and place credentials.json beside the exe if you would
    # rather be able to swap keys without rebuilding.
    cred = os.path.join(HERE, "credentials.json")
    if os.path.isfile(cred):
        cmd += ["--add-data", "%s%s." % (cred, SEP)]
        print("  + baking credentials.json into the exe")
    else:
        print("  ! no credentials.json found -- the exe will look for one beside it")

    cmd += ["--hidden-import", "sa_auth", os.path.join(HERE, "ModStudio.py")]

    print("\nBuilding... this takes a minute or two.\n")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print("\nBuild failed. The PyInstaller output above says why.")
        return r.returncode

    exe = os.path.join(HERE, "dist", "ModDex.exe")
    if os.path.isfile(exe):
        mb = os.path.getsize(exe) / 1048576.0
        print("\n  DONE  ->  %s  (%.1f MB)" % (exe, mb))
        print("\n  Double-click it to run. On first launch Windows may show a")
        print("  SmartScreen warning: More info -> Run anyway. That is expected")
        print("  for any unsigned exe.")
    else:
        print("\nBuild reported success but dist\\ModDex.exe is missing.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
