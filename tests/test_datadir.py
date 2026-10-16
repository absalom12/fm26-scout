import os
import pytest
from datadir import FILES, data_dir, list_saves, replace_data, resolve_save


def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FM26_DATA_DIR", str(tmp_path / "d"))
    assert data_dir() == tmp_path / "d" and data_dir().is_dir()


def test_list_saves_missing_folder_is_none(tmp_path):
    assert list_saves(tmp_path / "nope") is None


def test_list_saves_only_fm_newest_first(tmp_path):
    (tmp_path / "config.xml").write_text("x")
    a, b = tmp_path / "Chantilly (v02).fm", tmp_path / "Beta Lop - OL 2032.04.14.fm"
    a.write_bytes(b"1"); b.write_bytes(b"22")
    os.utime(a, (1000, 1000)); os.utime(b, (2000, 2000))
    assert [s["name"] for s in list_saves(tmp_path)] == [b.name, a.name]
    assert list_saves(tmp_path)[0]["size"] == 2


@pytest.mark.parametrize("name", ["Chantilly (v02).fm", "Beta Lop - OL 2032.04.14.fm", "é save.FM"])
def test_resolve_save_accepts_real_names(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")
    assert resolve_save(name, tmp_path) == (tmp_path / name).resolve()


@pytest.mark.parametrize("name", ["", "../x.fm", "sub/x.fm", "..\\x.fm", "x.txt", "missing.fm"])
def test_resolve_save_rejects(tmp_path, name):
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError):
        resolve_save(name, tmp_path)


def test_resolve_save_rejects_symlink_outside(tmp_path):
    outside = tmp_path / "outside.fm"; outside.write_bytes(b"x")
    saves = tmp_path / "saves"; saves.mkdir()
    (saves / "link.fm").symlink_to(outside)
    with pytest.raises(ValueError):
        resolve_save("link.fm", saves)


def test_replace_data_swaps_and_removes_stale(tmp_path):
    dst, src = tmp_path / "dst", tmp_path / "dst" / "tmp"
    src.mkdir(parents=True)
    (dst / "club.json").write_text("stale")
    (dst / "all_world_players.csv").write_text("old")
    (src / "all_world_players.csv").write_text("new")
    replace_data(src, dst)
    assert (dst / "all_world_players.csv").read_text() == "new"
    assert not (dst / "club.json").exists()
