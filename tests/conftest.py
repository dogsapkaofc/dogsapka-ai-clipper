import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dogsapka_clipper.processing.ffmpeg_utils import find_tool  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not find_tool("ffmpeg"), reason="FFmpeg not installed")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    if not find_tool("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    from dogsapka_clipper.simulation.simulator import generate_sample_stream
    p = tmp_path_factory.mktemp("sim") / "sample.mp4"
    return generate_sample_stream(p, duration=40, highlights=[22.0], width=640, height=360)


@pytest.fixture
def db(tmp_path):
    from dogsapka_clipper.db.database import Database
    # File DB, like the app: ":memory:" shares one sqlite connection between the test thread and
    # job-worker threads (StaticPool), which is not thread-safe and made test_jobs flaky.
    d = Database(tmp_path / "test.sqlite3")
    yield d
    d.close()
