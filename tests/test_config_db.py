from dogsapka_clipper.core.config import AppSettings, apply_preset
from dogsapka_clipper.core.paths import safe_filename
from dogsapka_clipper.core.logging_setup import redact
from dogsapka_clipper.db.database import SCHEMA_VERSION
from dogsapka_clipper.db.models import Clip, Highlight, MonitoringSession, Streamer


def test_settings_roundtrip_and_validation(db):
    s = AppSettings()
    s.recording.buffer_seconds = 1000
    s.recording.max_clip_s = 999
    s.output.fps = 50
    s.interface.language = "de"
    db.save_settings(s)
    loaded = db.load_settings()
    assert loaded.recording.buffer_seconds == 300
    assert loaded.recording.max_clip_s == 180
    assert loaded.output.fps == 60
    assert loaded.interface.language == "en"


def test_settings_tolerant_loading():
    s = AppSettings.from_dict({"recording": {"buffer_seconds": "abc", "pre_event_s": 20}, "bogus": 1})
    assert s.recording.buffer_seconds == 120 and s.recording.pre_event_s == 20


def test_presets():
    s = AppSettings()
    apply_preset(s.output, "standard")
    assert (s.output.width, s.output.height, s.output.fps) == (720, 1280, 30)
    apply_preset(s.output, "ultra")
    assert (s.output.width, s.output.height, s.output.fps) == (1080, 1920, 60)


def test_schema_and_relationships(db):
    assert db.current_version() == SCHEMA_VERSION
    with db.session() as s:
        st = Streamer(login="abc", authorized=True)
        s.add(st)
        s.flush()
        ms = MonitoringSession(streamer_id=st.id)
        s.add(ms)
        s.flush()
        h = Highlight(session_id=ms.id, event_ts=10, start_ts=0, end_ts=20, score=80, reasons=["x"])
        s.add(h)
        s.flush()
        s.add(Clip(streamer_id=st.id, highlight_id=h.id, source_path="/x.mp4"))
    with db.session() as s:
        st = s.query(Streamer).one()
        assert st.clips[0].highlight.score == 80
        s.delete(st)
    assert db.count(Clip) == 0 and db.count(MonitoringSession) == 0


def test_safe_filename():
    assert safe_filename('a<b>:c"/d\\e|f?*') == "a_b__c__d_e_f__"
    assert safe_filename("CON") == "_CON"
    assert safe_filename("   ") == "file"
    assert len(safe_filename("x" * 300)) <= 80


def test_log_redaction():
    t = redact("Authorization: Bearer abc123def access_token=zzz&refresh_token=yyy PASS oauth:secret")
    assert "abc123def" not in t and "zzz" not in t and "yyy" not in t and "secret" not in t


def test_secret_store_file_backend(tmp_path):
    from dogsapka_clipper.core.secrets import SecretStore
    st = SecretStore(tmp_path / "s.bin", use_keyring=False)
    st.set_json("tok", {"a": 1})
    assert SecretStore(tmp_path / "s.bin", use_keyring=False).get_json("tok") == {"a": 1}
    st.set("tok", None)
    assert st.get("tok") is None
