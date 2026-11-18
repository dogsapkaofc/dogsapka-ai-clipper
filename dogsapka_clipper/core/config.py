"""Typed application settings, persisted as JSON in the SQLite database."""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field, fields
from typing import Any


@dataclass
class TwitchSettings:
    client_id: str = ""
    # client_secret is stored in the secure token store, never here.
    poll_interval_s: int = 60
    use_chat: bool = True


@dataclass
class YouTubeLiveSettings:
    """Monitoring YouTube live streamers (official YouTube Data API v3; API key in the secure store).

    Quota (default 10 000 units/day): each live check costs 1 unit per channel + 1 per 50 videos;
    each chat poll costs ~5 units. The defaults keep a handful of channels well within the limit.
    """
    poll_interval_s: int = 120
    use_chat: bool = True
    chat_poll_s: int = 20


@dataclass
class RemoteSettings:
    """Remote control from the iPhone app (local network, paired devices only)."""
    enabled: bool = False
    port: int = 47820


@dataclass
class RecordingSettings:
    enabled: bool = True
    quality: str = "best"            # streamlink quality name
    buffer_seconds: int = 120        # 30..300
    segment_seconds: int = 4
    pre_event_s: int = 25
    post_event_s: int = 25
    min_clip_s: int = 15
    max_clip_s: int = 90             # 15..180
    max_storage_gb: float = 50.0
    cleanup_recordings_days: int = 3


@dataclass
class DetectionSettings:
    sensitivity: float = 0.5         # 0 (strict) .. 1 (sensitive)
    use_chat_activity: bool = True
    use_chat_reactions: bool = True
    use_audio: bool = True
    use_visual: bool = True          # lightweight scene analysis; existing explicit choices are retained
    weight_chat_activity: float = 35.0
    weight_chat_reactions: float = 15.0
    weight_audio: float = 30.0
    weight_visual: float = 20.0
    threshold: float = 60.0
    auto_threshold: bool = True
    cooldown_s: int = 15
    merge_window_s: int = 20


@dataclass
class OutputSettings:
    preset: str = "high"             # standard | high | ultra | custom
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 20
    video_bitrate_k: int = 0         # 0 = quality based (CRF/CQ)
    audio_bitrate_k: int = 160
    encoder: str = "auto"            # auto | libx264 | h264_nvenc
    output_dir: str = ""             # empty = <data>/exports
    profile: str = "tiktok_gaming"
    auto_render: bool = True
    workers: int = 1


@dataclass
class AISettings:
    transcription: bool = True
    whisper_model: str = "small"
    whisper_device: str = "auto"     # auto | cpu | cuda
    subtitle_language: str = "auto"  # auto | hu | en
    face_detection: bool = True
    censor_profanity: bool = True    # subtitles: F**K instead of FUCK (first/last letter kept)
    metadata_language: str = "en"
    title_style: str = "gaming"


@dataclass
class InterfaceSettings:
    language: str = "en"             # en | hu
    notifications: bool = True
    first_run_complete: bool = False
    auto_update_check: bool = True       # look for a new release at startup
    skipped_update_version: str = ""     # "skip this version" in the update dialog


@dataclass
class PublishingSettings:
    youtube_enabled: bool = False
    youtube_client_id: str = ""
    youtube_privacy: str = "private"
    youtube_schedule_enabled: bool = False
    youtube_schedule_times: list[str] = field(default_factory=lambda: ["09:00", "15:00", "21:00"])
    youtube_schedule_timezone: str = "Europe/Budapest"
    tiktok_enabled: bool = False
    tiktok_client_key: str = ""
    auto_publish: bool = False       # requires explicit opt-in + authorization
    # auto-upload only exports that passed QC; set when the user confirmed the rights for auto-upload
    auto_publish_confirmed: bool = False


@dataclass
class AppSettings:
    twitch: TwitchSettings = field(default_factory=TwitchSettings)
    youtube_live: YouTubeLiveSettings = field(default_factory=YouTubeLiveSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    ai: AISettings = field(default_factory=AISettings)
    interface: InterfaceSettings = field(default_factory=InterfaceSettings)
    publishing: PublishingSettings = field(default_factory=PublishingSettings)
    remote: RemoteSettings = field(default_factory=RemoteSettings)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppSettings":
        """Tolerant loader: unknown keys are ignored, wrong types fall back to defaults."""
        inst = cls()
        if not isinstance(data, dict):
            return inst
        for f in fields(cls):
            section = getattr(inst, f.name)
            values = data.get(f.name)
            if not isinstance(values, dict):
                continue
            for sf in fields(section):
                if sf.name not in values:
                    continue
                default = getattr(section, sf.name)
                val = values[sf.name]
                try:
                    if isinstance(default, bool):
                        val = (val if isinstance(val, bool) else False) if sf.name == "youtube_schedule_enabled" else bool(val)
                    elif isinstance(default, int):
                        val = int(val)
                    elif isinstance(default, float):
                        val = float(val)
                    elif isinstance(default, str):
                        val = str(val)
                    elif isinstance(default, list):
                        if not isinstance(val, list) or any(not isinstance(v, str) for v in val):
                            raise ValueError("Expected a list of strings")
                        val = list(val)
                except (TypeError, ValueError):
                    continue
                setattr(section, sf.name, val)
        from dogsapka_clipper.publishing.schedule import DEFAULT_TIMES, DEFAULT_TIMEZONE, validate_schedule
        try:
            validate_schedule(inst.publishing.youtube_schedule_times, inst.publishing.youtube_schedule_timezone)
            raw = data.get("publishing", {})
            if isinstance(raw, dict) and "youtube_schedule_times" in raw:
                validate_schedule(raw["youtube_schedule_times"], inst.publishing.youtube_schedule_timezone)
        except ValueError:
            inst.publishing.youtube_schedule_enabled = False
            inst.publishing.youtube_schedule_times = list(DEFAULT_TIMES)
            inst.publishing.youtube_schedule_timezone = DEFAULT_TIMEZONE
        inst.validate()
        return inst

    def validate(self) -> None:
        from dogsapka_clipper.publishing.schedule import validate_schedule
        p = self.publishing
        p.youtube_schedule_times = list(validate_schedule(p.youtube_schedule_times, p.youtube_schedule_timezone))
        r = self.recording
        r.buffer_seconds = _clamp(r.buffer_seconds, 30, 300)
        r.segment_seconds = _clamp(r.segment_seconds, 1, 10)
        r.min_clip_s = _clamp(r.min_clip_s, 5, 180)
        r.max_clip_s = _clamp(r.max_clip_s, max(15, r.min_clip_s), 180)
        r.pre_event_s = _clamp(r.pre_event_s, 0, r.buffer_seconds - 5)
        r.post_event_s = _clamp(r.post_event_s, 0, 120)
        d = self.detection
        d.sensitivity = _clamp(d.sensitivity, 0.0, 1.0)
        d.threshold = _clamp(d.threshold, 0.0, 100.0)
        for name in ("weight_chat_activity", "weight_chat_reactions", "weight_audio", "weight_visual"):
            setattr(d, name, _clamp(getattr(d, name), 0.0, 100.0))
        self.remote.port = _clamp(self.remote.port, 1024, 65535)
        y = self.youtube_live
        y.poll_interval_s = _clamp(y.poll_interval_s, 30, 3600)
        y.chat_poll_s = _clamp(y.chat_poll_s, 10, 120)
        o = self.output
        o.fps = 60 if o.fps >= 45 else 30
        o.width = _clamp(o.width, 240, 3840) // 2 * 2
        o.height = _clamp(o.height, 240, 3840) // 2 * 2
        o.crf = _clamp(o.crf, 10, 40)
        o.workers = _clamp(o.workers, 1, 8)
        if self.interface.language not in ("en", "hu"):
            self.interface.language = "en"


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


EXPORT_PRESETS = {
    "standard": dict(width=720, height=1280, fps=30, crf=23),
    "high": dict(width=1080, height=1920, fps=30, crf=20),
    "ultra": dict(width=1080, height=1920, fps=60, crf=19),
}


def apply_preset(out: OutputSettings, preset: str) -> None:
    out.preset = preset
    for k, v in EXPORT_PRESETS.get(preset, {}).items():
        setattr(out, k, v)
