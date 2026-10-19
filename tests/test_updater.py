import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

import updater


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_newer_compares_numerically():
    assert updater.newer("0.4.10", "0.4.9") and updater.newer("0.5.0", "0.4.12")
    assert not updater.newer("0.4.7", "0.4.7") and not updater.newer("0.4.6", "0.4.7")


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto and codesign are macOS tools")
def test_unsigned_download_leaves_current_app_in_place(tmp_path):
    current = tmp_path / "Apps" / "ScoutSheet.app"
    (current / "Contents").mkdir(parents=True)
    (current / "Contents" / "marker").write_text("old")
    fake = tmp_path / "src" / "ScoutSheet.app"
    (fake / "Contents").mkdir(parents=True)
    subprocess.run(["ditto", "-c", "-k", "--keepParent", fake, tmp_path / "u.zip"], check=True)
    with pytest.raises(subprocess.CalledProcessError):  # codesign --verify refuses it
        updater.install(current, (tmp_path / "u.zip").as_uri(), sha(tmp_path / "u.zip"))
    assert (current / "Contents" / "marker").read_text() == "old"
    assert [p.name for p in current.parent.iterdir()] == ["ScoutSheet.app"]  # work folder cleaned up


def windows_zip(tmp_path):
    with zipfile.ZipFile(tmp_path / "u.zip", "w") as z:
        z.writestr("ScoutSheet/ScoutSheet.exe", "new")
    current = tmp_path / "Apps" / "FM26-Scout"
    current.mkdir(parents=True)
    (current / "FM26-Scout.exe").write_text("old")
    return current, tmp_path / "u.zip"


def test_windows_update_is_staged_next_to_the_app(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    current, z = windows_zip(tmp_path)
    new, target, work = updater.install(current, z.as_uri(), sha(z))
    assert (new / "ScoutSheet.exe").read_text() == "new" and (work / "swap.ps1").exists()
    assert target == current.parent / "ScoutSheet" and (current / "FM26-Scout.exe").read_text() == "old"  # swapped after exit


def test_windows_update_with_a_bad_checksum_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    current, z = windows_zip(tmp_path)
    with pytest.raises(ValueError, match="checksum"):
        updater.install(current, z.as_uri(), "0" * 64)
    assert [p.name for p in current.parent.iterdir()] == ["FM26-Scout"]


@pytest.mark.skipif(sys.platform != "win32", reason="the swap script is PowerShell")
def test_windows_swap_replaces_the_folder_once_the_app_exits(tmp_path):
    apps = tmp_path / "Apps"
    current, work = apps / "FM26-Scout", apps / ".work"
    current.mkdir(parents=True)
    new = work / "new" / "ScoutSheet"
    new.mkdir(parents=True)
    shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "whoami.exe"), new / "ScoutSheet.exe")  # exits at once
    (work / "swap.ps1").write_text(updater.SWAP_PS1, encoding="utf-8")
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", work / "swap.ps1",
                    "-ProcId", str(app.pid), "-Cur", current, "-New", new, "-Target", apps / "ScoutSheet",
                    "-Work", work], check=True, timeout=60)
    assert [p.name for p in apps.iterdir()] == ["ScoutSheet"]
    assert (apps / "ScoutSheet" / "ScoutSheet.exe").exists()
