"""Choosable data folder: priority order, moving existing data, rewriting stored paths."""
import json
import sys

import pytest

from dogsapka_clipper.core import paths as P
from dogsapka_clipper.db.database import Database
from dogsapka_clipper.db.models import Clip, ExportedVideo, Streamer


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("DOGSAPKA_DATA_DIR", raising=False)
    for var in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setattr(P.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(P, "_install_dir", lambda: install)
    return tmp_path, install


def test_priority_env_then_app_then_installer_then_default(env, monkeypatch):
    tmp, install = env
    assert P.default_root() == P.platform_default_root()
    (install / "data_location.txt").write_text("\ufeffD:\\Inst\\DOGSAPKA_AI_CLIPPER\n", encoding="utf-8")
    assert str(P.default_root()) == str(P.Path("D:\\Inst\\DOGSAPKA_AI_CLIPPER"))    # installer choice (BOM ok)
    P.set_data_location(tmp / "chosen")
    assert P.default_root() == tmp / "chosen"                                       # in-app choice wins
    monkeypatch.setenv("DOGSAPKA_DATA_DIR", str(tmp / "env"))
    assert P.default_root() == tmp / "env"                                          # env wins over all


def test_normalize_adds_folder_name(tmp_path):
    assert P.normalize_data_folder(tmp_path) == tmp_path / "DOGSAPKA_AI_CLIPPER"
    assert P.normalize_data_folder(tmp_path / "DOGSAPKA_AI_CLIPPER") == tmp_path / "DOGSAPKA_AI_CLIPPER"


def test_move_data_and_rewrite_database_paths(env):
    tmp, _ = env
    old = tmp / "C" / "DOGSAPKA_AI_CLIPPER"
    new = tmp / "D" / "DOGSAPKA_AI_CLIPPER"
    ap = P.AppPaths.create(old)
    clip_file = ap.clips / "alice" / "c1.mp4"
    clip_file.parent.mkdir(parents=True)
    clip_file.write_bytes(b"video")
    outside = tmp / "elsewhere" / "e.mp4"      # custom export folder outside the data folder: untouched
    db = Database(ap.database_file)
    with db.session() as s:
        st = Streamer(login="alice", authorized=True)
        s.add(st)
        s.flush()
        c = Clip(streamer_id=st.id, source_path=str(clip_file), thumbnail_path=str(ap.thumbnails / "t.jpg"))
        s.add(c)
        s.flush()
        s.add(ExportedVideo(clip_id=c.id, path=str(outside), srt_path=str(ap.exports / "x.srt")))
    db.close()

    P.set_data_location(new, move_from=old)
    assert P.apply_pending_move() is None
    assert not old.exists()
    assert (new / "clips" / "alice" / "c1.mp4").read_bytes() == b"video"
    assert json.loads(P.location_file().read_text())["path"] == str(new)     # move request cleared
    assert "move_from" not in json.loads(P.location_file().read_text())
    db = Database(new / "database" / "dogsapka.sqlite3")
    try:
        with db.session() as s:
            c = s.query(Clip).one()
            e = s.query(ExportedVideo).one()
            assert c.source_path == str(new / "clips" / "alice" / "c1.mp4")
            assert c.thumbnail_path == str(new / "thumbnails" / "t.jpg")
            assert e.path == str(outside)
            assert e.srt_path == str(new / "exports" / "x.srt")
    finally:
        db.close()


def test_move_without_pending_request_is_noop(env):
    assert P.apply_pending_move() is None
    P.set_data_location(env[0] / "x")
    assert P.apply_pending_move() is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS default location")
def test_macos_default_is_movies(env):
    assert P.platform_default_root().parent.name == "Movies"
