"""Video rendering with MoviePy + FFmpeg (ported from services/video_service.py).

Long-form 16:9 with Ken Burns scene backgrounds, optional chapter/intro/lower-
third overlays, burned captions and ducked background music; plus 9:16 Shorts.
moviepy is imported lazily inside the functions that need it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from yt_video.settings import PROJECT_ROOT, Settings
from yt_video.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from moviepy import VideoClip

logger = get_logger(__name__)

_W, _H = 1920, 1080
_SUPPORTED_MUSIC_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
_FALLBACK_BACKGROUND = PROJECT_ROOT / "assets" / "background.mp4"


def _resolve_background_music_path(settings: Settings) -> Path | None:
    if not settings.background_music_enabled:
        return None

    track = settings.background_music_track
    if track:
        if Path(track).is_file():
            return Path(track)
        logger.warning("Configured background music track not found: %s", track)

    music_dir = settings.background_music_dir
    if not music_dir or not Path(music_dir).is_dir():
        return None

    tracks = [
        p for p in sorted(Path(music_dir).iterdir())
        if p.is_file() and p.suffix.lower() in _SUPPORTED_MUSIC_EXTENSIONS
    ]
    return tracks[0] if tracks else None


# ---------------------------------------------------------------------------
# Font helper
# ---------------------------------------------------------------------------
def _load_font(size: int, bold: bool = False) -> Any:
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"C:/Windows/Fonts/{'arialbd' if bold else 'arial'}.ttf",
        "arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Ken Burns backgrounds
# ---------------------------------------------------------------------------
def _make_label_overlay(label: str, w: int = _W, h: int = _H) -> np.ndarray:
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bar_h = 70
    draw.rectangle([(0, h - bar_h), (w, h)], fill=(0, 0, 0, 160))
    draw.text((40, h - bar_h + 14), label, font=_load_font(40, bold=True), fill=(255, 255, 255, 230))
    return np.array(overlay)


def _is_finance_symbol(symbol: str) -> bool:
    """True only for stock/index tickers - not scene labels like intro/s1."""
    if not symbol:
        return False
    s = symbol.strip()
    if not s or s.lower() in {"intro", "outro"}:
        return False
    if re.fullmatch(r"s\d+", s, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"section\d+", s, flags=re.IGNORECASE):
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.&-]{1,14}", s))


def _ken_burns_clip(image_path: str | Path, duration: float, symbol: str = "") -> VideoClip:
    """Slow pan + zoom (Ken Burns) over a static image."""
    from moviepy import VideoClip

    zoom = 1.03  # milder zoom reads less like stock AI slideshow motion
    large = Image.open(image_path).convert("RGB").resize(
        (int(_W * zoom), int(_H * zoom)), Image.LANCZOS
    )
    large_arr = np.array(large, dtype=np.uint8)
    lw, lh = large_arr.shape[1], large_arr.shape[0]
    # Only stamp a bottom label for real tickers - never for intro/s1/etc.
    label_rgba = _make_label_overlay(symbol) if _is_finance_symbol(symbol) else None

    def make_frame(t: float) -> np.ndarray:
        progress = t / max(duration, 0.001)
        x = int((lw - _W) * progress)
        y = int((lh - _H) * 0.5)
        frame = large_arr[y:y + _H, x:x + _W].copy()
        if label_rgba is not None:
            alpha = label_rgba[:, :, 3:4] / 255.0
            rgb = label_rgba[:, :, :3]
            frame = (frame * (1 - alpha) + rgb * alpha).astype(np.uint8)
        return frame

    return VideoClip(make_frame, duration=duration).with_fps(24)


def fill_missing_scene_images(labels: list[str], scene_images: dict[str, str]) -> dict[str, str]:
    """Give every label an image: a gap reuses the previous scene's image.

    A gap at the very start uses the nearest following image. Keeps segment
    count (and so total duration) equal to the timeline. Raises ValueError if
    no label has an image.
    """
    usable = {k: v for k, v in scene_images.items() if v}
    if not any(label in usable for label in labels):
        raise ValueError("No scene images available for any timeline label")
    filled: dict[str, str] = {}
    previous: str | None = None
    for label in labels:
        if label in usable:
            previous = usable[label]
            filled[label] = previous
        elif previous is not None:
            logger.warning("Scene '%s' has no image - reusing previous scene image", label)
            filled[label] = previous
    for label in labels:
        if label not in filled:
            nxt = next(usable[x] for x in labels if x in usable)
            logger.warning("Scene '%s' has no image - using nearest following image", label)
            filled[label] = nxt
    return {label: filled[label] for label in labels}


def _build_background_timed(
    duration: float, timeline: dict[str, float], scene_images: dict[str, str]
) -> VideoClip:
    """Concatenate per-section Ken Burns clips following the timeline."""
    from moviepy import concatenate_videoclips

    sorted_sections = sorted(timeline.items(), key=lambda x: x[1])
    logger.info("Building timed slideshow: %s", [(s, round(t, 1)) for s, t in sorted_sections])
    existing = {k: v for k, v in scene_images.items() if v and Path(v).exists()}
    images = fill_missing_scene_images([label for label, _ in sorted_sections], existing)
    clips = []
    for i, (label, start_t) in enumerate(sorted_sections):
        end_t = sorted_sections[i + 1][1] if i + 1 < len(sorted_sections) else duration
        seg_duration = max(end_t - start_t, 1.0)
        logger.info("  %s: %.1fs -> %.1fs", label, start_t, end_t)
        clips.append(_ken_burns_clip(images[label], seg_duration, symbol=label))
    return concatenate_videoclips(clips, method="compose")


def _build_background_equal(duration: float, scene_images: dict[str, str]) -> VideoClip:
    """Fallback: equal-duration slideshow when no timeline is provided."""
    from moviepy import VideoFileClip, concatenate_videoclips

    image_files = [p for p in scene_images.values() if Path(p).exists()]
    if image_files:
        per_clip = duration / len(image_files)
        clips = [
            _ken_burns_clip(p, per_clip, symbol=Path(p).stem.replace("_scene", ""))
            for p in image_files
        ]
        return concatenate_videoclips(clips, method="compose")

    if not _FALLBACK_BACKGROUND.exists():
        raise FileNotFoundError(f"No images and no {_FALLBACK_BACKGROUND} found.")
    bg = VideoFileClip(str(_FALLBACK_BACKGROUND))
    loops = int(duration / bg.duration) + 1
    return concatenate_videoclips([bg] * loops).subclipped(0, duration)


# ---------------------------------------------------------------------------
# Optional overlays: lower third, chapter card, intro card
# ---------------------------------------------------------------------------
def _make_lower_third_clip(label: str, start: float, duration: float = 5.0, fps: int = 24) -> VideoClip:
    """Slide-up lower-third bar showing the current scene/chapter label."""
    from moviepy import VideoClip

    SLIDE_IN, FADE_OUT, BAR_H = 0.3, 0.4, 90
    BAR_TOP = _H - 64 - BAR_H
    font_label = _load_font(34, bold=True)
    bg_color = (18, 18, 28)
    accent = (255, 140, 0)

    def _ease_out(p: float) -> float:
        return 1 - (1 - min(p, 1.0)) ** 3

    def make_frame(t: float) -> np.ndarray:
        slide = _ease_out(t / SLIDE_IN) if t < SLIDE_IN else 1.0
        alpha_mul = min((duration - t) / FADE_OUT, 1.0)
        y_off = int((1 - slide) * (BAR_H + 10))
        canvas = Image.new("RGBA", (_W, BAR_H), (0, 0, 0, 0))
        bar = Image.new("RGBA", (_W, BAR_H), (*bg_color, int(200 * alpha_mul)))
        draw = ImageDraw.Draw(bar)
        draw.rectangle([0, 0, _W, 4], fill=(*accent, 255))
        clean = label.replace("_", " ").title()
        draw.text((40, BAR_H // 2 - 17), clean, font=font_label, fill=(255, 255, 255, 230))
        canvas.paste(bar, (0, y_off))
        return np.array(canvas.convert("RGB"))

    clip = VideoClip(make_frame, duration=duration).with_fps(fps)
    return clip.with_start(start).with_position(("left", BAR_TOP))


def _make_chapter_card_clip(
    chapter_num: int, total_chapters: int, label: str, start: float,
    duration: float = 2.5, fps: int = 24,
) -> VideoClip:
    """Full-frame dark chapter transition card with progress bar."""
    from moviepy import VideoClip

    FADE_IN = FADE_OUT = 0.25
    BG, GOLD, ORANGE = (8, 8, 20), (255, 215, 0), (255, 140, 0)
    font_num = _load_font(32)
    font_title = _load_font(72, bold=True)
    font_sub = _load_font(26)
    clean = label.replace("_", " ").title()

    def _alpha(t: float) -> float:
        if t < FADE_IN:
            return t / FADE_IN
        if t > duration - FADE_OUT:
            return (duration - t) / FADE_OUT
        return 1.0

    def make_frame(t: float) -> np.ndarray:
        a = _alpha(t)
        canvas = Image.new("RGBA", (_W, _H), (*BG, int(255 * a)))
        draw = ImageDraw.Draw(canvas)
        draw.text((_W // 2 - 120, 80), f"CHAPTER {chapter_num} / {total_chapters}",
                  font=font_num, fill=(*GOLD, int(200 * a)))
        tb = draw.textbbox((0, 0), clean, font=font_title)
        draw.text((_W // 2 - (tb[2] - tb[0]) // 2, _H // 2 - 60), clean,
                  font=font_title, fill=(255, 255, 255, int(255 * a)))
        sb = draw.textbbox((0, 0), "Explore More", font=font_sub)
        draw.text((_W // 2 - (sb[2] - sb[0]) // 2, _H // 2 + 60), "Explore More",
                  font=font_sub, fill=(*GOLD, int(180 * a)))
        bar_w = int(_W * chapter_num / total_chapters)
        draw.rectangle([0, _H - 8, bar_w, _H], fill=(*ORANGE, int(255 * a)))
        return np.array(canvas.convert("RGB"))

    clip = VideoClip(make_frame, duration=duration).with_fps(fps)
    return clip.with_start(start)


def _make_intro_clip(topic: str, angle: str = "", duration: float = 2.0, fps: int = 24) -> VideoClip:
    """2-second intro card with the topic title and angle."""
    from moviepy import VideoClip

    FADE = 0.4
    BG, ORANGE, GOLD = (8, 8, 20), (255, 140, 0), (255, 215, 0)
    font_title = _load_font(52, bold=True)
    font_angle = _load_font(28)

    def _alpha(t: float) -> float:
        if t < FADE:
            return t / FADE
        if t > duration - FADE:
            return (duration - t) / FADE
        return 1.0

    def make_frame(t: float) -> np.ndarray:
        a = _alpha(t)
        canvas = Image.new("RGBA", (_W, _H), (*BG, int(255 * a)))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, _W, 8], fill=(*ORANGE, int(255 * a)))
        topic_short = topic[:60] + ("…" if len(topic) > 60 else "")
        tb = draw.textbbox((0, 0), topic_short, font=font_title)
        draw.text((_W // 2 - (tb[2] - tb[0]) // 2, _H // 2 - 60), topic_short,
                  font=font_title, fill=(255, 255, 255, int(255 * a)))
        if angle:
            angle_short = angle[:80]
            ab = draw.textbbox((0, 0), angle_short, font=font_angle)
            draw.text((_W // 2 - (ab[2] - ab[0]) // 2, _H // 2 + 30), angle_short,
                      font=font_angle, fill=(*GOLD, int(200 * a)))
        return np.array(canvas.convert("RGB"))

    return VideoClip(make_frame, duration=duration).with_fps(fps)


# ---------------------------------------------------------------------------
# Burned-in captions
# ---------------------------------------------------------------------------
def _wrap_caption_text(text: str, max_chars: int = 28) -> str:
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= max_chars:
            cur = f"{cur} {w}"
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= 2:
                break
    if len(lines) < 2:
        lines.append(cur)
    return "\n".join(lines[:2])


def _make_caption_clips(
    cues: list[dict], size: tuple[int, int] = (_W, _H), fps: int = 24
) -> list[VideoClip]:
    """Build per-cue burned caption overlays for mute / mobile viewers."""
    from moviepy import VideoClip

    if not cues:
        return []

    width, height = size
    is_vertical = height > width
    font_size = 54 if is_vertical else 42
    bar_h = 160 if is_vertical else 110
    y_pos = int(height * (0.72 if is_vertical else 0.82))
    font = _load_font(font_size, bold=True)
    clips: list[VideoClip] = []

    for cue in cues:
        text = _wrap_caption_text(str(cue.get("text", "")), max_chars=26 if is_vertical else 36)
        if not text:
            continue
        start = float(cue.get("start", 0.0))
        end = float(cue.get("end", start + 1.0))
        dur = max(end - start, 0.2)

        # Pre-render once - cue text is static; redrawing every frame is wasteful.
        canvas = Image.new("RGBA", (width, bar_h), (0, 0, 0, 0))
        bar = Image.new("RGBA", (width, bar_h), (0, 0, 0, 150))
        draw = ImageDraw.Draw(bar)
        y = 18
        for line in text.split("\n"):
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            draw.text(
                ((width - tw) // 2, y), line, font=font,
                fill=(255, 255, 255, 245), stroke_width=2, stroke_fill=(0, 0, 0, 220),
            )
            y += font_size + 6
        canvas.paste(bar, (0, 0))
        frame = np.array(canvas.convert("RGB"))

        def make_frame(t: float, cached: np.ndarray = frame) -> np.ndarray:
            return cached

        clip = VideoClip(make_frame, duration=dur).with_fps(fps)
        clips.append(clip.with_start(start).with_position(("center", y_pos)))

    return clips


def _vertical_ken_burns(
    image_path: str | Path, duration: float, size: tuple[int, int] = (1080, 1920)
) -> VideoClip:
    """Center-crop a 16:9 still into 9:16 with mild zoom."""
    from moviepy import VideoClip

    tw, th = size
    src = Image.open(image_path).convert("RGB")
    scale = max(tw / src.width, th / src.height) * 1.04
    nw, nh = int(src.width * scale), int(src.height * scale)
    arr = np.array(src.resize((nw, nh), Image.LANCZOS), dtype=np.uint8)

    def make_frame(t: float) -> np.ndarray:
        progress = t / max(duration, 0.001)
        max_x = max(nw - tw, 0)
        max_y = max(nh - th, 0)
        x = int(max_x * 0.15 + max_x * 0.7 * progress)
        y = int(max_y * 0.5)
        return arr[y:y + th, x:x + tw].copy()

    return VideoClip(make_frame, duration=duration).with_fps(24)


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------
def create_video(
    timeline: dict[str, float],
    scene_images: dict[str, str],
    scene_labels: list[str],
    scene_titles: dict[str, str],
    topic: str,
    angle: str,
    voice_path: Path,
    out_path: Path,
    captions: list[dict],
    settings: Settings,
) -> Path:
    """Assemble the final 1920x1080 YouTube video locally using FFmpeg + MoviePy.

    ``timeline`` maps scene label -> start second. Chapter cards, intro card,
    lower thirds and caption burn-in follow the ``settings`` toggles.
    """
    from moviepy import (
        AudioFileClip, CompositeAudioClip, CompositeVideoClip,
        afx, concatenate_audioclips,
    )
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut

    voice_path = Path(voice_path)
    out_path = Path(out_path)
    if not voice_path.exists():
        raise FileNotFoundError(f"Voice file not found: {voice_path}")

    logger.info("Loading voice audio...")
    voice = AudioFileClip(str(voice_path))
    voice = voice.with_effects([AudioFadeIn(0.5), AudioFadeOut(0.5)])
    duration = voice.duration
    logger.info("Voice duration: %.1fs  (source: %s)", duration, voice_path)

    # 1. Background
    bg = (
        _build_background_timed(duration, timeline, scene_images)
        if timeline
        else _build_background_equal(duration, scene_images)
    )

    # 2. Optional intro / lower-thirds / chapter cards
    overlays: list[VideoClip] = []
    sorted_scenes = sorted(timeline.items(), key=lambda x: x[1]) if timeline else []
    chapter_labels = scene_labels or [s for s, _ in sorted_scenes]
    total_ch = max(len(chapter_labels), 1)

    if settings.intro_card_enabled and topic and duration > 0:
        overlays.append(_make_intro_clip(topic=topic, angle=angle, duration=2.0))
    elif topic:
        logger.info("Intro card disabled - spoken hook owns the first seconds")

    for ch_idx, (label, start_t) in enumerate(sorted_scenes):
        if label in ("intro", "outro", "NIFTY"):
            continue
        end_t = sorted_scenes[ch_idx + 1][1] if ch_idx + 1 < len(sorted_scenes) else duration
        seg_dur = max(end_t - start_t, 1.0)
        display = scene_titles.get(label) or label

        if settings.lower_thirds_enabled:
            lt_dur = min(6.0, seg_dur * 0.35)
            overlays.append(_make_lower_third_clip(label=display, start=start_t, duration=lt_dur))

        if settings.chapter_cards_enabled and ch_idx > 0:
            overlays.append(_make_chapter_card_clip(
                chapter_num=ch_idx, total_chapters=total_ch, label=display,
                start=max(start_t - 0.3, 0.0), duration=2.5,
            ))

    if not settings.lower_thirds_enabled:
        logger.info("Lower-thirds disabled - cleaner, less template look")
    if not settings.chapter_cards_enabled:
        logger.info("Chapter cards disabled - retention-safe")

    # 3. Burned captions (mute retention)
    if settings.burn_captions_enabled and captions:
        caption_clips = _make_caption_clips(captions, size=(_W, _H))
        overlays.extend(caption_clips)
        logger.info("Burning %d caption cue(s) into long-form", len(caption_clips))
    elif settings.burn_captions_enabled:
        logger.info("Caption burn enabled but no cues provided - skipping")

    final_video = CompositeVideoClip([bg] + overlays, size=(_W, _H)) if overlays else bg
    logger.info("Compositing: background + %d overlay layer(s)", len(overlays))

    # 4. Audio mix: voice + ducked background music
    music_path = _resolve_background_music_path(settings)
    if music_path:
        logger.info("Mixing background music: %s", music_path)
        music = AudioFileClip(str(music_path))
        if music.duration < duration:
            loops = int(duration / music.duration) + 1
            music = concatenate_audioclips([music] * loops).subclipped(0, duration)
        else:
            music = music.subclipped(0, duration)
        music = music.with_effects([afx.MultiplyVolume(0.08)])
        final_audio = CompositeAudioClip([voice, music])
    else:
        logger.info("No background music track found - using voice only")
        final_audio = voice

    final_video = final_video.with_audio(final_audio)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Rendering %s (this may take a few minutes)...", out_path)
    final_video.write_videofile(str(out_path), fps=24, codec="libx264", audio_codec="aac")
    logger.info("Video rendering complete -> %s", out_path)
    return out_path


SHORTS_MAX_DURATION_S = 175.0  # YouTube Shorts allow up to 3 minutes


def _clamp_cues(cues: list[dict], duration: float) -> list[dict]:
    """Drop cues starting at/after ``duration`` and trim the rest to end by it."""
    out: list[dict] = []
    for cue in cues:
        start = float(cue.get("start", 0.0))
        if start >= duration:
            continue
        end = min(float(cue.get("end", start + 1.0)), duration)
        out.append({**cue, "end": end})
    return out


def _build_shorts_composite(
    voice_path: Path,
    scene_images: dict[str, str],
    captions: list[dict],
    max_duration: float = SHORTS_MAX_DURATION_S,
) -> VideoClip:
    """Vertical Shorts clip whose audio, background and captions share one duration.

    The duration is the voice length, capped at ``max_duration``; no layer runs past it.
    """
    from moviepy import AudioFileClip, CompositeVideoClip, VideoClip, concatenate_videoclips
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut

    _SW, _SH = 1080, 1920
    voice = AudioFileClip(str(voice_path))
    voice_duration = float(voice.duration)
    duration = min(voice_duration, max_duration)
    if voice_duration > max_duration:
        logger.warning(
            "Shorts voice is %.1fs, longer than the %.1fs cap; truncating audio, video and captions",
            voice_duration, max_duration,
        )
    voice = voice.subclipped(0, duration).with_effects([AudioFadeIn(0.2), AudioFadeOut(0.3)])
    logger.info("Shorts duration: %.1fs", duration)

    paths = [p for p in scene_images.values() if p and Path(p).exists()]
    if not paths:
        # Solid dark fallback so render never hard-fails.
        def _solid(t: float) -> np.ndarray:
            return np.full((_SH, _SW, 3), 18, dtype=np.uint8)

        bg = VideoClip(_solid, duration=duration).with_fps(24)
    else:
        per = duration / len(paths)
        clips = [_vertical_ken_burns(p, per, size=(_SW, _SH)) for p in paths]
        bg = concatenate_videoclips(clips, method="compose").subclipped(0, duration)

    overlays: list[VideoClip] = []
    cues = _clamp_cues(captions, duration)
    if cues:
        overlays.extend(_make_caption_clips(cues, size=(_SW, _SH)))
        logger.info("Shorts: burning %d caption cue(s)", len(overlays))

    final_video = CompositeVideoClip([bg] + overlays, size=(_SW, _SH)) if overlays else bg
    return final_video.with_duration(duration).with_audio(voice)


def create_shorts_video(
    voice_path: Path,
    scene_images: dict[str, str],
    captions: list[dict],
    out_path: Path,
    settings: Settings,
) -> Path:
    """Assemble a vertical Shorts video (1080x1920) from shorts voice + scene stills.

    Captions are always burned in for mute feed viewing (``settings`` is accepted
    for interface symmetry with :func:`create_video`).
    """
    del settings
    voice_path = Path(voice_path)
    out_path = Path(out_path)
    if not voice_path.exists():
        raise FileNotFoundError(f"Shorts voice not found: {voice_path}")

    final_video = _build_shorts_composite(voice_path, scene_images, captions)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Rendering Shorts -> %s ...", out_path)
    final_video.write_videofile(str(out_path), fps=24, codec="libx264", audio_codec="aac")
    logger.info("Shorts render complete -> %s", out_path)
    return out_path
