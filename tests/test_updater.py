import subprocess
import sys

import pytest

import updater


def test_newer_compares_numerically():
    assert updater.newer("0.4.10", "0.4.9") and updater.newer("0.5.0", "0.4.12")
    assert not updater.newer("0.4.7", "0.4.7") and not updater.newer("0.4.6", "0.4.7")


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto and codesign are macOS tools")
def test_unsigned_download_leaves_current_app_in_place(tmp_path):
    current = tmp_path / "Apps" / "FM26-Scout.app"
    (current / "Contents").mkdir(parents=True)
    (current / "Contents" / "marker").write_text("old")
    fake = tmp_path / "src" / "FM26-Scout.app"
    (fake / "Contents").mkdir(parents=True)
    subprocess.run(["ditto", "-c", "-k", "--keepParent", fake, tmp_path / "u.zip"], check=True)
    with pytest.raises(subprocess.CalledProcessError):  # codesign --verify refuses it
        updater.install(current, (tmp_path / "u.zip").as_uri())
    assert (current / "Contents" / "marker").read_text() == "old"
    assert [p.name for p in current.parent.iterdir()] == ["FM26-Scout.app"]  # work folder cleaned up
