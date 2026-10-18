import os, sys

import PyInstaller.__main__

sep = ";" if sys.platform == "win32" else ":"
icon = "assets/icon.ico" if sys.platform == "win32" else "assets/icon.icns"  # regenerate with assets/make_icon.py (needs Pillow)
sign = ["--codesign-identity", os.environ["CODESIGN_IDENTITY"], "--osx-entitlements-file", "assets/entitlements.plist"] \
    if sys.platform == "darwin" and os.environ.get("CODESIGN_IDENTITY") else []  # set by CI when Apple secrets exist
PyInstaller.__main__.run([
    "launcher.py", "--name", "FM26-Scout", "--noconsole", "--onedir", "--noconfirm", "--clean", "--icon", icon,
    "--add-data", f"static{sep}static",
    "--collect-all", "fmsave",
    "--collect-submodules", "uvicorn",
    "--hidden-import", "multipart",
    # Optional extras of duckdb/fmsave the app never imports: fewer files for macOS to scan on first launch.
    "--exclude-module", "pandas", "--exclude-module", "PIL",
    *sign,
    *(["--add-data", f"VERSION{sep}."] if os.path.exists("VERSION") else []),  # written by CI from the tag
])
