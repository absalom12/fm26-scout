import sys

import PyInstaller.__main__

sep = ";" if sys.platform == "win32" else ":"
icon = "assets/icon.ico" if sys.platform == "win32" else "assets/icon.icns"  # regenerate with assets/make_icon.py (needs Pillow)
PyInstaller.__main__.run([
    "launcher.py", "--name", "FM26-Scout", "--noconsole", "--onedir", "--noconfirm", "--clean", "--icon", icon,
    "--add-data", f"static{sep}static",
    "--collect-all", "fmsave",
    "--collect-submodules", "uvicorn",
    "--hidden-import", "multipart",
])
