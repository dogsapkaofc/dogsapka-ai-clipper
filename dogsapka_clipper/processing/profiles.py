"""Editing profiles. Built-ins are seeded into the DB; users can add custom ones."""
from __future__ import annotations

import copy

DEFAULT_PROFILE = {
    "aspect": "9:16",            # "9:16" | "source"
    "layout": "auto",            # auto | facecam_top | crop | blur | camera | source
    "fallback_layout": "crop",   # used by "auto" when no facecam / camera is detected
    "facecam_ratio": 0.30,       # preserve more gameplay and avoid oversized faces
    "subtitle_style": "viral",   # viral | clean | dynamic | minimal | none
    "subtitle_position": "lower",  # lower | middle | upper
    "accent_color": "#FFE600",
    "font": "Arial Black",
    "font_size": 0,              # 0 = automatic (relative to height)
    "zoom": False,               # subtle zoom on the detected peak
    "zoom_strength": 0.08,
    "dynamic_focus": False,      # stable framing by default
    "normalize_audio": True,
    "trim_silence": False,
    "transition": "none",        # none | fade
    "quality_crf_offset": 0,
    "burn_subtitles": True,
    "export_srt": True,
    "export_ass": False,
}

BUILTIN_PROFILES: dict[str, dict] = {
    "tiktok_gaming": {"name": "TikTok Gaming", "subtitle_style": "viral", "zoom": False, "transition": "none",
                      "layout": "auto", "trim_silence": True, "quality_crf_offset": -2},
    "gaming_boxed": {"name": "Gaming · Boxed captions", "subtitle_style": "boxed", "quality_crf_offset": -2},
    "gaming_neon": {"name": "Gaming · Neon captions", "subtitle_style": "neon", "accent_color": "#00E5FF",
                    "quality_crf_offset": -2},
    "youtube_shorts": {"name": "YouTube Shorts", "subtitle_style": "clean", "zoom": False, "transition": "fade",
                       "subtitle_position": "lower"},
    "streamer_reactions": {"name": "Streamer Reactions", "layout": "auto", "facecam_ratio": 0.5,
                           "subtitle_style": "dynamic", "zoom": False, "accent_color": "#00E5FF"},
    "cinematic": {"name": "Cinematic Highlights", "subtitle_style": "minimal", "zoom": False, "transition": "fade",
                  "layout": "auto", "fallback_layout": "blur", "quality_crf_offset": -2},
    "original": {"name": "Original Clip", "aspect": "source", "layout": "source", "subtitle_style": "minimal",
                 "zoom": False, "transition": "none"},
}


def resolve_profile(cfg: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_PROFILE)
    for k, v in (cfg or {}).items():
        if k in DEFAULT_PROFILE or k == "name":
            out[k] = v
    return out


def builtin(key: str) -> dict:
    return resolve_profile(BUILTIN_PROFILES.get(key, BUILTIN_PROFILES["tiktok_gaming"]))
