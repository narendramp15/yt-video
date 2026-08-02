"""
video_service.py — yt-video

Assembles the final YouTube video using MoviePy + FFmpeg (local, free).

Replaces: AWS Lambda / cloud rendering ($5–$20/month)
  → Runs locally (free) or via GitHub Actions (free CI minutes)
  → Fine for ~20 videos/month. Scale with self-hosted runners for 100+.

New in yt-video:
  • Scene images from Pollinations.ai used as section backgrounds
    (replaces plain stock chart Ken Burns when scene_images provided)
  • Ticker bar + stat cards unchanged from v1

Components:
  1. Background — Pollinations scene images OR candlestick chart (Ken Burns effect)
  2. Ticker bar  — scrolling bottom bar with live prices
  3. Stat cards  — animated pop-in cards per stock
  4. Audio mix   — voice + optional background music
"""

import os
import re
import glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    VideoFileClip, VideoClip, AudioFileClip,
    CompositeAudioClip, CompositeVideoClip, concatenate_videoclips,
    afx,
)
from config import config
from logger import logger

_CHART_DIR = "output/charts"
_W, _H     = 1920, 1080
_SUPPORTED_MUSIC_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


def _resolve_background_music_path() -> str | None:
    if not config.BACKGROUND_MUSIC_ENABLED:
        return None

    explicit_track = config.BACKGROUND_MUSIC_TRACK
    if explicit_track:
        if os.path.isfile(explicit_track):
            return explicit_track
        logger.warning(f"Configured background music track not found: {explicit_track}")

    music_dir = config.BACKGROUND_MUSIC_DIR
    if not music_dir or not os.path.isdir(music_dir):
        return None

    tracks = [
        os.path.join(music_dir, name)
        for name in sorted(os.listdir(music_dir))
        if os.path.isfile(os.path.join(music_dir, name))
        and os.path.splitext(name)[1].lower() in _SUPPORTED_MUSIC_EXTENSIONS
    ]
    return tracks[0] if tracks else None


# ---------------------------------------------------------------------------
# Font helper
# ---------------------------------------------------------------------------
def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"C:/Windows/Fonts/{'arialbd' if bold else 'arial'}.ttf",
        "arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Scrolling ticker bar
# ---------------------------------------------------------------------------
def _make_ticker_clip(
    ticker_items: list[str],
    video_width: int,
    duration: float,
    fps: int = 24,
    bar_height: int = 64,
) -> VideoClip:
    BG_COLOR      = (10, 10, 18)
    ACCENT_COLOR  = (255, 165, 0)
    NEUTRAL_COLOR = (255, 215, 0)
    UP_COLOR      = (0, 255, 120)
    DOWN_COLOR    = (255, 80, 80)
    SEPARATOR     = "   ▪   "

    font       = _load_font(30, bold=True)
    full_text  = SEPARATOR.join(ticker_items) + SEPARATOR

    dummy        = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_width   = int(dummy.textlength(full_text, font=font))
    scroll_width = text_width + video_width
    scroll_speed = scroll_width / duration

    def _item_color(item: str) -> tuple:
        if "▲" in item:
            return UP_COLOR
        if "▼" in item:
            return DOWN_COLOR
        return NEUTRAL_COLOR

    def make_frame(t: float) -> np.ndarray:
        img  = Image.new("RGB", (video_width, bar_height), BG_COLOR)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, video_width, 3], fill=ACCENT_COLOR)
        draw.rectangle([0, 0, 70, bar_height], fill=(180, 0, 0))
        draw.text((8, bar_height // 2 - 10), "LIVE",
                  font=_load_font(20, bold=True), fill=(255, 255, 255))

        x_start = int(video_width - (scroll_speed * t)) % scroll_width
        for offset in [x_start, x_start - scroll_width]:
            x = offset + 80
            for item in ticker_items:
                color = _item_color(item)
                draw.text((x, bar_height // 2 - 15), item, font=font, fill=color)
                x += int(dummy.textlength(item + SEPARATOR, font=font))

        return np.array(img)

    return VideoClip(make_frame, duration=duration).with_fps(fps)


# ---------------------------------------------------------------------------
# Animated stat card
# ---------------------------------------------------------------------------
def _make_stat_card_clip(
    label: str,
    value: str,
    change: str,
    start: float,
    duration: float = 4.0,
    fps: int = 24,
    card_w: int = 420,
    card_h: int = 130,
) -> VideoClip:
    is_positive  = "▲" in change
    ACCENT       = (0, 255, 120) if is_positive else (255, 80, 80)
    BG_FILL      = (18, 18, 30, 210)
    SLIDE_IN     = 0.35
    FADE_OUT     = 0.4

    font_label  = _load_font(22)
    font_value  = _load_font(42, bold=True)
    font_change = _load_font(28, bold=True)

    def _ease_out_cubic(p: float) -> float:
        return 1 - (1 - min(p, 1.0)) ** 3

    def make_frame(t: float) -> np.ndarray:
        slide     = _ease_out_cubic(t / SLIDE_IN)
        x_off     = int((1 - slide) * -(card_w + 20))
        time_left = duration - t
        alpha_mul = min(time_left / FADE_OUT, 1.0)

        canvas = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        card   = Image.new("RGBA", (card_w, card_h),
                           (*BG_FILL[:3], int(BG_FILL[3] * alpha_mul)))
        draw   = ImageDraw.Draw(card)
        draw.rectangle([0, 0, 6, card_h], fill=(*ACCENT, 255))
        draw.text((20, 10),  label,  font=font_label,  fill=(180, 180, 200, 230))
        draw.text((20, 38),  value,  font=font_value,  fill=(255, 255, 255, 255))
        draw.text((20, 92),  change, font=font_change, fill=(*ACCENT, 255))
        draw.rectangle([6, card_h - 2, card_w, card_h], fill=(*ACCENT, 120))
        canvas.paste(card, (max(x_off, 0), 0))
        return np.array(canvas.convert("RGB"))

    clip = VideoClip(make_frame, duration=duration).with_fps(fps)
    return clip.with_start(start)


# ---------------------------------------------------------------------------
# Background builders
# ---------------------------------------------------------------------------
def _chart_path(symbol: str) -> str | None:
    path = os.path.join(_CHART_DIR, f"{symbol}.png")
    return path if os.path.exists(path) else None


def _make_label_overlay(symbol: str, w: int = _W, h: int = _H) -> np.ndarray:
    try:
        from services.script_service import SYMBOL_NAMES
        label = SYMBOL_NAMES.get(symbol, symbol)
    except ImportError:
        label = symbol

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    bar_h   = 70
    draw.rectangle([(0, h - bar_h), (w, h)], fill=(0, 0, 0, 160))
    font = _load_font(40, bold=True)
    draw.text((40, h - bar_h + 14), label, font=font, fill=(255, 255, 255, 230))
    return np.array(overlay)


def _ken_burns_clip(
    image_path: str,
    duration: float,
    symbol: str = "",
) -> VideoClip:
    """Slow pan + zoom (Ken Burns) over a static image."""
    # Milder zoom reads less like stock AI slideshow motion.
    zoom = 1.03
    large = Image.open(image_path).convert("RGB").resize(
        (int(_W * zoom), int(_H * zoom)), Image.LANCZOS
    )
    large_arr  = np.array(large, dtype=np.uint8)
    lw, lh     = large_arr.shape[1], large_arr.shape[0]
    # Only stamp a bottom label for real finance tickers — never for intro/s1/etc.
    label_rgba = _make_label_overlay(symbol) if _is_finance_symbol(symbol) else None

    def make_frame(t: float) -> np.ndarray:
        progress = t / max(duration, 0.001)
        x     = int((lw - _W) * progress)
        y     = int((lh - _H) * 0.5)
        frame = large_arr[y:y + _H, x:x + _W].copy()
        if label_rgba is not None:
            alpha = label_rgba[:, :, 3:4] / 255.0
            rgb   = label_rgba[:, :, :3]
            frame = (frame * (1 - alpha) + rgb * alpha).astype(np.uint8)
        return frame

    return VideoClip(make_frame, duration=duration).with_fps(24)


def _is_finance_symbol(symbol: str) -> bool:
    """True only for stock/index tickers — not scene labels like intro/s1."""
    if not symbol:
        return False
    s = symbol.strip()
    if not s or s.lower() in {"intro", "outro"}:
        return False
    if re.fullmatch(r"s\d+", s, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"section\d+", s, flags=re.IGNORECASE):
        return False
    # NSE-style tickers are usually uppercase alphanumerics
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.&-]{1,14}", s))


def _build_background_timed(
    duration: float,
    timeline: dict,
    scene_images: dict[str, str] | None = None,
) -> VideoClip:
    """
    Build the full-length background by concatenating per-section clips.

    Priority per section:
      1. Pollinations.ai scene image (if scene_images provided)
      2. Candlestick chart PNG (output/charts/<symbol>.png)
      3. Fallback: NIFTY chart
    """
    sorted_sections = sorted(timeline.items(), key=lambda x: x[1])
    logger.info(f"Building timed slideshow: {[(s, round(t, 1)) for s, t in sorted_sections]}")
    scene_images = scene_images or {}
    clips = []

    for i, (symbol, start_t) in enumerate(sorted_sections):
        end_t        = sorted_sections[i + 1][1] if i + 1 < len(sorted_sections) else duration
        seg_duration = max(end_t - start_t, 1.0)

        # Priority 1: Pollinations.ai scene image
        scene = scene_images.get(symbol)
        if scene and os.path.exists(scene):
            logger.info(f"  {symbol}: {start_t:.1f}s → {end_t:.1f}s  [Pollinations scene]")
            clips.append(_ken_burns_clip(scene, seg_duration, symbol=symbol))
            continue

        # Priority 2: chart PNG (Ken Burns)
        chart = _chart_path(symbol)
        if chart is None:
            if symbol == "OUTRO":
                chart = next(
                    (_chart_path(s) for s, _ in reversed(sorted_sections[:i]) if _chart_path(s)),
                    _chart_path("NIFTY"),
                )
            else:
                chart = _chart_path("NIFTY")
        if chart is None:
            logger.warning(f"  {symbol}: no image found — skipping segment")
            continue

        logger.info(f"  {symbol}: {start_t:.1f}s → {end_t:.1f}s  [chart Ken Burns]")
        clips.append(_ken_burns_clip(chart, seg_duration, symbol=symbol))

    if not clips:
        raise FileNotFoundError("No background images found in output/charts/ or output/images/")
    return concatenate_videoclips(clips, method="compose")


def _build_background_equal(
    duration: float,
    scene_images: dict[str, str] | None = None,
) -> VideoClip:
    """Fallback: equal-duration slideshow when no timeline provided."""
    # Prefer scene images if available
    image_files: list[str] = []
    if scene_images:
        image_files = [p for p in scene_images.values() if os.path.exists(p)]
    if not image_files:
        image_files = sorted(glob.glob(f"{_CHART_DIR}/*.png"))

    if image_files:
        per_clip = duration / len(image_files)
        clips = [
            _ken_burns_clip(p, per_clip, symbol=os.path.splitext(os.path.basename(p))[0].replace("_scene", ""))
            for p in image_files
        ]
        return concatenate_videoclips(clips, method="compose")

    if not os.path.exists("assets/background.mp4"):
        raise FileNotFoundError("No images and no assets/background.mp4 found.")
    bg    = VideoFileClip("assets/background.mp4")
    loops = int(duration / bg.duration) + 1
    return concatenate_videoclips([bg] * loops).subclipped(0, duration)


# ---------------------------------------------------------------------------
# Overlay builder (ticker + stat cards)
# ---------------------------------------------------------------------------
def _build_overlays(
    duration: float,
    ticker_items: list[str] | None,
    stat_cards: list[dict] | None,
    fps: int = 24,
) -> list[VideoClip]:
    overlays: list[VideoClip] = []

    if ticker_items:
        ticker = _make_ticker_clip(ticker_items, _W, duration, fps=fps)
        ticker = ticker.with_position(("left", _H - 64))
        overlays.append(ticker)

    if stat_cards:
        for idx, card_cfg in enumerate(stat_cards):
            card = _make_stat_card_clip(
                label    = card_cfg["label"],
                value    = card_cfg["value"],
                change   = card_cfg["change"],
                start    = card_cfg["start"],
                duration = card_cfg.get("duration", 4.0),
                fps      = fps,
            )
            y_pos = 80 + idx * 150
            card  = card.with_position((40, y_pos))
            overlays.append(card)

    return overlays


# ---------------------------------------------------------------------------
# Lower-third overlay (scene label slide-up)
# ---------------------------------------------------------------------------
def _make_lower_third_clip(
    label: str,
    start: float,
    duration: float = 5.0,
    fps: int = 24,
) -> VideoClip:
    """Slide-up lower-third bar showing the current scene/chapter label."""
    SLIDE_IN  = 0.3
    FADE_OUT  = 0.4
    BAR_H     = 90
    BAR_TOP   = _H - 64 - BAR_H

    font_label = _load_font(34, bold=True)
    bg_color   = (18, 18, 28)
    accent     = (255, 140, 0)

    def _ease_out(p: float) -> float:
        return 1 - (1 - min(p, 1.0)) ** 3

    def make_frame(t: float) -> np.ndarray:
        slide     = _ease_out(t / SLIDE_IN) if t < SLIDE_IN else 1.0
        time_left = duration - t
        alpha_mul = min(time_left / FADE_OUT, 1.0)

        y_off = int((1 - slide) * (BAR_H + 10))
        canvas = Image.new("RGBA", (_W, BAR_H), (0, 0, 0, 0))
        bar    = Image.new("RGBA", (_W, BAR_H),
                           (*bg_color, int(200 * alpha_mul)))
        draw   = ImageDraw.Draw(bar)
        draw.rectangle([0, 0, _W, 4], fill=(*accent, 255))
        # Clean label: strip [SCENE:] syntax if present
        clean = label.replace("_", " ").title()
        draw.text((40, BAR_H // 2 - 17), clean, font=font_label,
                  fill=(255, 255, 255, 230))
        canvas.paste(bar, (0, y_off))
        return np.array(canvas.convert("RGB"))

    clip = VideoClip(make_frame, duration=duration).with_fps(fps)
    return clip.with_start(start).with_position(("left", BAR_TOP))


# ---------------------------------------------------------------------------
# Chapter card (full-screen transition between scenes)
# ---------------------------------------------------------------------------
def _make_chapter_card_clip(
    chapter_num: int,
    total_chapters: int,
    label: str,
    start: float,
    duration: float = 2.5,
    fps: int = 24,
) -> VideoClip:
    """Full-frame dark chapter transition card with progress bar."""
    FADE_IN  = 0.25
    FADE_OUT = 0.25
    BG       = (8, 8, 20)
    GOLD     = (255, 215, 0)
    ORANGE   = (255, 140, 0)

    font_num   = _load_font(32)
    font_title = _load_font(72, bold=True)
    font_sub   = _load_font(26)

    clean = label.replace("_", " ").title()

    def _alpha(t: float) -> float:
        if t < FADE_IN:
            return t / FADE_IN
        if t > duration - FADE_OUT:
            return (duration - t) / FADE_OUT
        return 1.0

    def make_frame(t: float) -> np.ndarray:
        a      = _alpha(t)
        canvas = Image.new("RGBA", (_W, _H), (*BG, int(255 * a)))
        draw   = ImageDraw.Draw(canvas)

        # Chapter label top
        chap_text = f"CHAPTER {chapter_num} / {total_chapters}"
        draw.text((_W // 2 - 120, 80), chap_text,
                  font=font_num, fill=(*GOLD, int(200 * a)))

        # Title centred
        title_bbox = draw.textbbox((0, 0), clean, font=font_title)
        tw = title_bbox[2] - title_bbox[0]
        draw.text((_W // 2 - tw // 2, _H // 2 - 60), clean,
                  font=font_title, fill=(255, 255, 255, int(255 * a)))

        # Sub-label
        sub_bbox = draw.textbbox((0, 0), "Explore More", font=font_sub)
        sw = sub_bbox[2] - sub_bbox[0]
        draw.text((_W // 2 - sw // 2, _H // 2 + 60), "Explore More",
                  font=font_sub, fill=(*GOLD, int(180 * a)))

        # Progress bar at bottom
        bar_w = int(_W * chapter_num / total_chapters)
        draw.rectangle([0, _H - 8, bar_w, _H], fill=(*ORANGE, int(255 * a)))

        return np.array(canvas.convert("RGB"))

    clip = VideoClip(make_frame, duration=duration).with_fps(fps)
    return clip.with_start(start)


# ---------------------------------------------------------------------------
# Intro card (2s opener listing today's topic)
# ---------------------------------------------------------------------------
def _make_intro_clip(
    topic: str,
    angle: str = "",
    duration: float = 2.0,
    fps: int = 24,
) -> VideoClip:
    """2-second intro card with today's topic title and angle."""
    FADE    = 0.4
    BG      = (8, 8, 20)
    ORANGE  = (255, 140, 0)
    GOLD    = (255, 215, 0)

    font_title  = _load_font(52, bold=True)
    font_angle  = _load_font(28)

    def _alpha(t: float) -> float:
        if t < FADE:
            return t / FADE
        if t > duration - FADE:
            return (duration - t) / FADE
        return 1.0

    def make_frame(t: float) -> np.ndarray:
        a      = _alpha(t)
        canvas = Image.new("RGBA", (_W, _H), (*BG, int(255 * a)))
        draw   = ImageDraw.Draw(canvas)

        # Orange top bar
        draw.rectangle([0, 0, _W, 8], fill=(*ORANGE, int(255 * a)))

        # Topic title
        topic_short = topic[:60] + ("…" if len(topic) > 60 else "")
        t_bbox = draw.textbbox((0, 0), topic_short, font=font_title)
        tw = t_bbox[2] - t_bbox[0]
        draw.text((_W // 2 - tw // 2, _H // 2 - 60), topic_short,
                  font=font_title, fill=(255, 255, 255, int(255 * a)))

        # Angle subtitle
        if angle:
            angle_short = angle[:80]
            a_bbox = draw.textbbox((0, 0), angle_short, font=font_angle)
            aw = a_bbox[2] - a_bbox[0]
            draw.text((_W // 2 - aw // 2, _H // 2 + 30), angle_short,
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
    cues: list[dict],
    size: tuple[int, int] = (_W, _H),
    fps: int = 24,
) -> list[VideoClip]:
    """Build per-cue burned caption overlays for mute / mobile viewers."""
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

        # Pre-render once — cue text is static; redrawing every frame is wasteful.
        canvas = Image.new("RGBA", (width, bar_h), (0, 0, 0, 0))
        bar = Image.new("RGBA", (width, bar_h), (0, 0, 0, 150))
        draw = ImageDraw.Draw(bar)
        y = 18
        for line in text.split("\n"):
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            draw.text(
                ((width - tw) // 2, y),
                line,
                font=font,
                fill=(255, 255, 255, 245),
                stroke_width=2,
                stroke_fill=(0, 0, 0, 220),
            )
            y += font_size + 6
        canvas.paste(bar, (0, 0))
        frame = np.array(canvas.convert("RGB"))

        def make_frame(t: float, cached=frame) -> np.ndarray:
            return cached

        clip = VideoClip(make_frame, duration=dur).with_fps(fps)
        clips.append(clip.with_start(start).with_position(("center", y_pos)))

    return clips


def _vertical_ken_burns(
    image_path: str,
    duration: float,
    size: tuple[int, int] = (1080, 1920),
) -> VideoClip:
    """Center-crop a 16:9 still into 9:16 with mild zoom."""
    tw, th = size
    src = Image.open(image_path).convert("RGB")
    # Scale so height fills, then crop width (or pad if portrait already)
    scale = max(tw / src.width, th / src.height) * 1.04
    nw, nh = int(src.width * scale), int(src.height * scale)
    large = src.resize((nw, nh), Image.LANCZOS)
    arr = np.array(large, dtype=np.uint8)

    def make_frame(t: float) -> np.ndarray:
        progress = t / max(duration, 0.001)
        max_x = max(nw - tw, 0)
        max_y = max(nh - th, 0)
        x = int(max_x * 0.15 + max_x * 0.7 * progress)
        y = int(max_y * 0.5)
        return arr[y:y + th, x:x + tw].copy()

    return VideoClip(make_frame, duration=duration).with_fps(24)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def create_video(
    timeline: dict | None = None,
    ticker_items: list[str] | None = None,
    stat_cards: list[dict] | None = None,
    scene_images: dict[str, str] | None = None,
    scene_labels: list[str] | None = None,
    scene_titles: dict[str, str] | None = None,
    topic: str = "",
    angle: str = "",
    voice_path: str | None = None,
    out_path: str = "output/final_video.mp4",
    enable_chapter_cards: bool | None = None,
    enable_intro_card: bool | None = None,
    enable_lower_thirds: bool | None = None,
    captions: list[dict] | None = None,
    burn_captions: bool | None = None,
) -> str:
    """
    Assemble the final YouTube video locally using FFmpeg + MoviePy.

    Parameters
    ----------
    timeline     : { symbol: start_seconds } — chart sync timeline
    ticker_items : list of ticker strings for the scrolling bottom bar
                   (finance only — leave empty for general topics)
    stat_cards   : list of stat card configs (finance only — leave empty otherwise)
    scene_images : { symbol: image_path } from image_service (Pollinations.ai)
                   Used as section backgrounds (prioritised over chart PNGs)
    scene_labels : ordered list of scene labels for chapter cards + lower-thirds
    scene_titles : optional {label: human title} for viewer-facing overlays
    topic        : video topic (used in intro card)
    angle        : content angle/framing (used in intro card subtitle)
    voice_path   : path to the TTS audio file; auto-detected when omitted
    out_path     : destination path for the rendered MP4
    enable_chapter_cards : full-screen chapter transitions (default: config, off)
    enable_intro_card    : 2s topic card at t=0 (default: config, off — fights hook)
    enable_lower_thirds  : per-scene label bars (default: config, off — less AI look)
    captions     : optional [{text,start,end}] cues to burn in
    burn_captions: override for BURN_CAPTIONS_ENABLED

    Returns: path to the rendered video file.
    """
    logger.info("Loading voice audio...")
    if voice_path is None:
        _voice_files = ["output/voice.mp3", "output/voice.wav"]
        voice_path = next((p for p in _voice_files if os.path.exists(p)), None)
    if voice_path is None:
        raise FileNotFoundError("No voice file found — run TTS step first")

    use_chapter_cards = (
        config.CHAPTER_CARDS_ENABLED if enable_chapter_cards is None else enable_chapter_cards
    )
    use_intro_card = (
        config.INTRO_CARD_ENABLED if enable_intro_card is None else enable_intro_card
    )
    use_lower_thirds = (
        config.LOWER_THIRDS_ENABLED if enable_lower_thirds is None else enable_lower_thirds
    )
    use_captions = (
        config.BURN_CAPTIONS_ENABLED if burn_captions is None else burn_captions
    )

    # Never paint finance chrome unless the caller explicitly provided items.
    ticker_items = ticker_items or None
    stat_cards = stat_cards or None
    if ticker_items == []:
        ticker_items = None
    if stat_cards == []:
        stat_cards = None

    voice    = AudioFileClip(voice_path)
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut
    voice    = voice.with_effects([AudioFadeIn(0.5), AudioFadeOut(0.5)])
    duration = voice.duration
    logger.info(f"Voice duration: {duration:.1f}s  (source: {voice_path})")

    # 1. Background
    bg = (
        _build_background_timed(duration, timeline, scene_images=scene_images)
        if timeline
        else _build_background_equal(duration, scene_images=scene_images)
    )

    # 2. Overlays (ticker + stat cards) — finance-only when provided
    overlays = _build_overlays(
        duration     = duration,
        ticker_items = ticker_items,
        stat_cards   = stat_cards,
    )

    # 3. Optional lower-thirds + optional chapter cards + optional intro
    sorted_scenes = sorted(timeline.items(), key=lambda x: x[1]) if timeline else []
    chapter_labels = scene_labels or [s for s, _ in sorted_scenes]
    total_ch = max(len(chapter_labels), 1)
    titles = scene_titles or {}

    if use_intro_card and topic and duration > 0:
        intro = _make_intro_clip(topic=topic, angle=angle, duration=2.0)
        overlays.append(intro)
    elif topic:
        logger.info("Intro card disabled — spoken hook owns the first seconds")

    for ch_idx, (label, start_t) in enumerate(sorted_scenes):
        if label in ("intro", "outro", "NIFTY"):
            continue
        end_t    = sorted_scenes[ch_idx + 1][1] if ch_idx + 1 < len(sorted_scenes) else duration
        seg_dur  = max(end_t - start_t, 1.0)
        display  = titles.get(label) or label

        if use_lower_thirds:
            lt_dur = min(6.0, seg_dur * 0.35)
            lt = _make_lower_third_clip(label=display, start=start_t, duration=lt_dur)
            overlays.append(lt)

        if use_chapter_cards and ch_idx > 0:
            card_start = max(start_t - 0.3, 0.0)
            card = _make_chapter_card_clip(
                chapter_num    = ch_idx,
                total_chapters = total_ch,
                label          = display,
                start          = card_start,
                duration       = 2.5,
            )
            overlays.append(card)

    if not use_lower_thirds:
        logger.info("Lower-thirds disabled — cleaner, less template look")
    if not use_chapter_cards:
        logger.info("Chapter cards disabled — retention-safe")

    # 3b. Burned captions (mute retention)
    if use_captions and captions:
        caption_clips = _make_caption_clips(captions, size=(_W, _H))
        overlays.extend(caption_clips)
        logger.info(f"Burning {len(caption_clips)} caption cue(s) into long-form")
    elif use_captions:
        logger.info("Caption burn enabled but no cues provided — skipping")

    final_video = (
        CompositeVideoClip([bg] + overlays, size=(_W, _H))
        if overlays
        else bg
    )
    logger.info(f"Compositing: background + {len(overlays)} overlay layer(s)")

    # 4. Audio mix
    music_path = _resolve_background_music_path()
    if music_path:
        logger.info(f"Mixing background music: {music_path}")
        from moviepy import concatenate_audioclips
        music = AudioFileClip(music_path)
        if music.duration < duration:
            loops = int(duration / music.duration) + 1
            music = concatenate_audioclips([music] * loops).subclipped(0, duration)
        else:
            music = music.subclipped(0, duration)
        # moviepy 2.x: multiply_volume() was removed — use the MultiplyVolume effect.
        music       = music.with_effects([afx.MultiplyVolume(0.08)])
        final_audio = CompositeAudioClip([voice, music])
    else:
        logger.info("No background music track found — using voice only")
        final_audio = voice

    final_video = final_video.with_audio(final_audio)

    os.makedirs("output", exist_ok=True)
    logger.info(f"Rendering {out_path} (this may take a few minutes)...")
    final_video.write_videofile(
        out_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
    )
    logger.info(f"Video rendering complete ✓  → {out_path}")
    return out_path


def create_shorts_video(
    voice_path: str,
    scene_images: dict[str, str] | None = None,
    captions: list[dict] | None = None,
    out_path: str = "output/shorts.mp4",
) -> str:
    """
    Assemble a vertical Shorts video (1080x1920) from shorts voice + scene stills.
    Captions are always burned in for mute feed viewing.
    """
    _SW, _SH = 1080, 1920
    if not voice_path or not os.path.exists(voice_path):
        raise FileNotFoundError(f"Shorts voice not found: {voice_path}")

    voice = AudioFileClip(voice_path)
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut
    voice = voice.with_effects([AudioFadeIn(0.2), AudioFadeOut(0.3)])
    duration = min(float(voice.duration), 55.0)
    voice = voice.subclipped(0, duration)
    logger.info(f"Shorts voice duration: {duration:.1f}s")

    paths = [p for p in (scene_images or {}).values() if p and os.path.exists(p)]
    if not paths:
        # Solid dark fallback so render never hard-fails
        def _solid(t: float) -> np.ndarray:
            return np.full((_SH, _SW, 3), 18, dtype=np.uint8)
        bg = VideoClip(_solid, duration=duration).with_fps(24)
    else:
        per = duration / len(paths)
        clips = [_vertical_ken_burns(p, per, size=(_SW, _SH)) for p in paths]
        bg = concatenate_videoclips(clips, method="compose").subclipped(0, duration)

    overlays: list[VideoClip] = []
    if captions:
        overlays.extend(_make_caption_clips(captions, size=(_SW, _SH)))
        logger.info(f"Shorts: burning {len(overlays)} caption cue(s)")

    final_video = (
        CompositeVideoClip([bg] + overlays, size=(_SW, _SH))
        if overlays
        else bg
    )
    final_video = final_video.with_audio(voice)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    logger.info(f"Rendering Shorts → {out_path} ...")
    final_video.write_videofile(
        out_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
    )
    logger.info(f"Shorts render complete ✓  → {out_path}")
    return out_path
