import sys

import PyInstaller.__main__

sep = ";" if sys.platform == "win32" else ":"
PyInstaller.__main__.run([
    "launcher.py", "--name", "FM26-Scout", "--noconsole", "--onedir", "--noconfirm", "--clean",
    "--add-data", f"static{sep}static",
    "--collect-all", "fmsave",
    "--collect-submodules", "uvicorn",
    "--hidden-import", "multipart",
])
