"""Hardware / dependency checks for the dashboard and first-run wizard."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from dogsapka_clipper.processing.ffmpeg_utils import find_tool, nvenc_available, popen_kwargs


@dataclass
class GpuInfo:
    name: str = ""
    utilization: float | None = None   # percent
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None


def gpu_info() -> GpuInfo | None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run([smi, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             **popen_kwargs()).stdout.strip().splitlines()
        if not out:
            return None
        name, util, used, total = [x.strip() for x in out[0].split(",")]
        return GpuInfo(name, float(util), float(used), float(total))
    except Exception:
        return None


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True
    fix: str = ""


@dataclass
class SystemReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.required)


def tool_version(name: str) -> str:
    path = find_tool(name)
    if not path:
        return ""
    try:
        cmd = path.split(" ") + (["--version"] if name == "streamlink" else ["-version"])
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **popen_kwargs()).stdout
        return out.splitlines()[0][:80] if out else "installed"
    except Exception:
        return ""


def run_system_checks(data_root, twitch_status: str = "not_configured") -> SystemReport:
    rep = SystemReport()
    v = tool_version("ffmpeg")
    rep.checks.append(CheckResult("FFmpeg", bool(v), v or "Not found",
                                  fix="Install FFmpeg (https://ffmpeg.org) or reinstall DOGSAPKA - the installer bundles it in the 'tools' folder."))
    v = tool_version("ffprobe")
    rep.checks.append(CheckResult("FFprobe", bool(v), v or "Not found", fix="FFprobe ships with FFmpeg."))
    v = tool_version("streamlink")
    rep.checks.append(CheckResult("Streamlink", bool(v), v or "Not found",
                                  fix="Install Streamlink (https://streamlink.github.io). Needed for recording live streams; "
                                      "simulation mode works without it."))
    g = gpu_info()
    nv = nvenc_available() if find_tool("ffmpeg") else False
    rep.checks.append(CheckResult("GPU acceleration", True,
                                  (f"{g.name} - NVENC {'available' if nv else 'not usable'}" if g else
                                   "No NVIDIA GPU detected - CPU encoding (libx264) will be used"), required=False))
    free = shutil.disk_usage(data_root).free / 1e9
    rep.checks.append(CheckResult("Disk space", free >= 10, f"{free:.1f} GB free in {data_root}",
                                  fix="At least 10 GB free space is recommended for recordings and exports."))
    try:
        import faster_whisper  # noqa: F401
        ai = CheckResult("AI transcription (faster-whisper)", True,
                         "Installed - the selected Whisper model downloads automatically on first use", required=False)
    except ImportError:
        ai = CheckResult("AI transcription (faster-whisper)", False, "Not installed - subtitles disabled",
                         required=False, fix="pip install faster-whisper")
    rep.checks.append(ai)
    try:
        import cv2  # noqa: F401
        rep.checks.append(CheckResult("Face detection (OpenCV)", True, f"OpenCV {cv2.__version__}", required=False))
    except ImportError:
        rep.checks.append(CheckResult("Face detection (OpenCV)", False, "Not installed - centred crop will be used",
                                      required=False, fix="pip install opencv-python-headless"))
    msg = {"connected": "Connected (user account)", "app_token": "Configured (app access)",
           "not_connected": "Client ID set - account not connected", "not_configured": "Not configured"}[twitch_status]
    rep.checks.append(CheckResult("Twitch connection", twitch_status in ("connected", "app_token"), msg, required=False,
                                  fix="Settings → Twitch: enter your application's Client ID and connect your account."))
    return rep
