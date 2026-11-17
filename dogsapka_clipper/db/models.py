"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Streamer(Base):
    __tablename__ = "streamers"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Twitch: the channel login. YouTube: "yt-<handle or channel id>" ("-" never occurs in Twitch logins).
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(16), default="twitch")      # twitch | youtube
    display_name: Mapped[str] = mapped_column(String(128), default="")
    twitch_user_id: Mapped[str] = mapped_column(String(32), default="")
    youtube_channel_id: Mapped[str] = mapped_column(String(64), default="")
    youtube_uploads_playlist: Mapped[str] = mapped_column(String(64), default="")
    youtube_handle: Mapped[str] = mapped_column(String(128), default="")
    live_video_id: Mapped[str] = mapped_column(String(32), default="")       # YouTube: current live video
    profile_image_url: Mapped[str] = mapped_column(String(512), default="")
    profile_image_path: Mapped[str] = mapped_column(String(512), default="")
    authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    stream_title: Mapped[str] = mapped_column(String(512), default="")
    category: Mapped[str] = mapped_column(String(256), default="")
    viewer_count: Mapped[int] = mapped_column(Integer, default=0)
    # adaptive detection baselines learned from previous sessions
    baseline: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_live_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sessions: Mapped[list["MonitoringSession"]] = relationship(back_populates="streamer", cascade="all, delete-orphan")
    clips: Mapped[list["Clip"]] = relationship(back_populates="streamer", cascade="all, delete-orphan")


class MonitoringSession(Base):
    __tablename__ = "monitoring_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    streamer_id: Mapped[int] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"), index=True)
    twitch_stream_id: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    category: Mapped[str] = mapped_column(String(256), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")   # active|ended|error
    error: Mapped[str] = mapped_column(Text, default="")
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)

    streamer: Mapped[Streamer] = relationship(back_populates="sessions")
    segments: Mapped[list["RecordingSegment"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    highlights: Mapped[list["Highlight"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class RecordingSegment(Base):
    __tablename__ = "recording_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    start_ts: Mapped[float] = mapped_column(Float)          # wall clock (epoch seconds)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    session: Mapped[MonitoringSession] = relationship(back_populates="segments")


class Highlight(Base):
    __tablename__ = "highlights"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id", ondelete="CASCADE"), index=True)
    event_ts: Mapped[float] = mapped_column(Float)          # epoch seconds of peak
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    session: Mapped[MonitoringSession] = relationship(back_populates="highlights")
    clip: Mapped["Clip | None"] = relationship(back_populates="highlight", uselist=False)


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[int] = mapped_column(primary_key=True)
    streamer_id: Mapped[int] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"), index=True)
    highlight_id: Mapped[int | None] = mapped_column(ForeignKey("highlights.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    source_path: Mapped[str] = mapped_column(String(1024))
    thumbnail_path: Mapped[str] = mapped_column(String(1024), default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    detection_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(256), default="")
    transcript: Mapped[dict] = mapped_column(JSON, default=dict)   # {"language":..,"segments":[..]}
    edit_state: Mapped[dict] = mapped_column(JSON, default=dict)   # trim, crop, layout overrides
    layout: Mapped[str] = mapped_column(String(32), default="")
    edit_status: Mapped[str] = mapped_column(String(32), default="pending")   # pending|processing|done|error
    export_status: Mapped[str] = mapped_column(String(32), default="none")    # none|rendering|done|error
    duplicate_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    streamer: Mapped[Streamer] = relationship(back_populates="clips")
    highlight: Mapped[Highlight | None] = relationship(back_populates="clip")
    exports: Mapped[list["ExportedVideo"]] = relationship(back_populates="clip")
    jobs: Mapped[list["ProcessingJob"]] = relationship(back_populates="clip", cascade="all, delete-orphan")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))          # process | render | transcribe | upload
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|paused|done|failed|cancelled
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(128), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    clip: Mapped[Clip | None] = relationship(back_populates="jobs")


class ExportedVideo(Base):
    __tablename__ = "exported_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    path: Mapped[str] = mapped_column(String(1024))
    srt_path: Mapped[str] = mapped_column(String(1024), default="")
    ass_path: Mapped[str] = mapped_column(String(1024), default="")
    profile: Mapped[str] = mapped_column(String(64), default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    encoder: Mapped[str] = mapped_column(String(32), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    qc_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    qc_report: Mapped[dict] = mapped_column(JSON, default=dict)
    published: Mapped[dict] = mapped_column(JSON, default=dict)   # {"youtube": {"id":..}, ...}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    clip: Mapped[Clip | None] = relationship(back_populates="exports")


class PublishAccount(Base):
    """A connected YouTube channel or TikTok account that exports can be uploaded to.

    Tokens live in the secure store under ``token_key``; only non-secret metadata is stored here.
    """
    __tablename__ = "publish_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))                 # youtube | tiktok
    name: Mapped[str] = mapped_column(String(256), default="")        # channel title / account name
    token_key: Mapped[str] = mapped_column(String(64), unique=True)
    auto_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    # YouTube: private | unlisted | public. TikTok: inbox | SELF_ONLY | ... (direct post privacy)
    privacy: Mapped[str] = mapped_column(String(32), default="private")
    streamer_filter: Mapped[list] = mapped_column(JSON, default=list)  # streamer ids; empty = all streamers
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EditingProfile(Base):
    __tablename__ = "editing_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
