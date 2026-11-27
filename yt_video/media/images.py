"""Topic-aware scene images and thumbnail (ported from services/image_service.py).

Generation goes through :class:`ImageChain`; this module owns prompt
construction, seed derivation, CLIP-verified regeneration and the thumbnail
hook-text overlay.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from yt_video.domain.packaging import derive_thumbnail_hook
from yt_video.media.clip import ClipVerifier
from yt_video.providers.image.chain import ImageChain
from yt_video.logging import get_logger

logger = get_logger(__name__)

_SEED_STEP = 1009
_SCENE_SIZE = (1920, 1080)
_THUMB_SIZE = (1280, 720)

# Position-based visual style descriptors - documentary / photo-real, not AI-art.
_POSITION_STYLES = [
    "wide establishing photo, natural daylight, documentary look",
    "close-up detail, shallow depth of field, real photograph",
    "environmental mid-shot, natural light, candid documentary photo",
    "action moment captured in-camera, high shutter, real-world scene",
    "portrait framing, soft natural light, photojournalism style",
    "elevated overview, real location, documentary photography",
    "quiet closing frame, soft ambient light, authentic photograph",
]


def _stable_seed(text: str, modulus: int) -> int:
    """Deterministic seed from text (Python's hash() is randomized per process)."""
    if modulus <= 0:
        raise ValueError("modulus must be positive")
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulus


def _normalize_visual_text(text: str) -> str:
    """Clean narration text so prompt keywords are readable to the image model."""
    if not text:
        return ""
    cleaned = re.sub(
        r"\b(?:[A-Za-z]-){2,}[A-Za-z]\b", lambda m: m.group(0).replace("-", ""), text
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _scene_focus_text(scene_text: str, max_chars: int = 220) -> str:
    """Return a short, cleaned scene summary suitable for prompt injection."""
    cleaned = _normalize_visual_text(scene_text)
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    focus = " ".join(sentences[:2]).strip()
    if len(focus) <= max_chars:
        return focus
    truncated = focus[:max_chars].rsplit(" ", 1)[0].strip()
    return f"{truncated}..."


def generate_scene_image(
    chain: ImageChain,
    verifier: ClipVerifier,
    prompt: str,
    out_path: Path,
    width: int = _SCENE_SIZE[0],
    height: int = _SCENE_SIZE[1],
    seed: int = 42,
    verify_text: str = "",
    verify_attempts: int = 3,
) -> Path:
    """Generate one background image, regenerating with fresh seeds on a low CLIP score.

    When ``verify_text`` is given and CLIP is enabled, an image scoring below
    ``verifier.min_score`` is rejected and regenerated with seed
    ``seed + attempt * 1009``, up to ``verify_attempts`` tries. If none clears
    the threshold the best-scoring attempt is used. Without verification the
    first image is accepted. Raises RuntimeError if no attempt produced an image.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    verify = bool(verify_text) and verifier.enabled
    attempts = max(1, verify_attempts) if verify else 1

    best_path: Path | None = None
    best_score = -1.0
    tried: list[Path] = []
    try:
        for attempt in range(attempts):
            attempt_seed = seed + attempt * _SEED_STEP
            attempt_path = out_path.with_name(f"{out_path.name}.try{attempt}")
            try:
                chain.generate(prompt, width, height, attempt_seed, attempt_path)
            except RuntimeError as exc:
                logger.warning("%s attempt %d: %s", out_path.name, attempt + 1, exc)
                continue
            tried.append(attempt_path)

            if not verify:
                shutil.copyfile(attempt_path, out_path)
                return out_path

            score = verifier.score(attempt_path, verify_text)
            if score is None:
                # Backend went unavailable mid-run - accept the first valid image.
                shutil.copyfile(attempt_path, out_path)
                return out_path

            logger.info(
                "  CLIP relevance %.3f for %s (attempt %d/%d, threshold %s)",
                score, out_path.name, attempt + 1, attempts, verifier.min_score,
            )
            if score > best_score:
                best_score, best_path = score, attempt_path
            if score >= verifier.min_score:
                verifier.record(score)
                shutil.copyfile(attempt_path, out_path)
                return out_path

        if best_path is not None:
            logger.warning(
                "  %s: no attempt cleared CLIP threshold - using best image (score %.3f)",
                out_path.name, best_score,
            )
            verifier.record(best_score)
            shutil.copyfile(best_path, out_path)
            return out_path
    finally:
        for p in tried:
            p.unlink(missing_ok=True)

    raise RuntimeError(f"no image could be generated for {out_path.name}")


def generate_scene_images(
    chain: ImageChain,
    verifier: ClipVerifier,
    labels: list[str],
    topic: str,
    angle: str,
    scene_texts: dict[str, str],
    prompt_overrides: dict[str, str],
    out_dir: Path,
) -> dict[str, str]:
    """Generate one unique thematic image per scene label. Returns {label: path}."""
    out_dir = Path(out_dir)
    context = f"{topic}" + (f" - {angle}" if angle else "")
    results: dict[str, str] = {}
    verifier.reset()

    for i, label in enumerate(labels):
        style = _POSITION_STYLES[i % len(_POSITION_STYLES)]
        scene_focus = _scene_focus_text(scene_texts.get(label, ""))
        scene_override = _normalize_visual_text(prompt_overrides.get(label, ""))

        # Subject leads, style trails: diffusion models weight early tokens most.
        # Preference: script-LLM scene prompt > scene narration focus > raw topic.
        subject = scene_override or scene_focus or context

        photo_tail = (
            f"{style}, photorealistic, natural lighting, real photograph, "
            f"16:9, no text, no watermark, no CGI, no illustration"
        )
        if label == "intro":
            tail = f"opening frame, {photo_tail}"
        elif label == "outro":
            tail = f"closing frame, reflective mood, {photo_tail}"
        else:
            tail = photo_tail
        prompt = f"{subject}. {tail}"

        # Unique seed per scene so images are visually distinct.
        seed = (i + 1) * 7 + _stable_seed(f"{label}:{topic}", 1000)
        try:
            path = generate_scene_image(
                chain, verifier, prompt, out_dir / f"{label}_scene.jpg",
                width=_SCENE_SIZE[0], height=_SCENE_SIZE[1], seed=seed, verify_text=subject,
            )
            results[label] = str(path)
        except Exception as exc:  # noqa: BLE001 - one bad scene must not sink the batch
            logger.warning("Image generation failed for %s: %s", label, exc)

    logger.info("Scene images generated: %s", list(results))
    logger.info(verifier.summary())
    return results


def generate_thumbnail(
    chain: ImageChain,
    title: str,
    topic: str,
    prompt_override: str,
    hook_text: str,
    out_path: Path,
    verifier: ClipVerifier | None = None,
    text_enabled: bool = True,
) -> Path | None:
    """Generate a 1280x720 YouTube thumbnail: AI subject image + hook-text overlay.

    ``text_enabled`` mirrors v1's THUMBNAIL_TEXT_ENABLED. Returns None if the
    image could not be generated.
    """
    verifier = verifier or ClipVerifier(False, 0.24, "")
    subject = topic or title
    clean_override = _normalize_visual_text(prompt_override)
    # Prefer the script-LLM thumbnail prompt; else the cleaned title/topic.
    head = clean_override or _normalize_visual_text(
        f"{title}. {subject}" if subject and subject != title else (title or subject)
    )
    prompt = (
        f"{head}. "
        f"YouTube thumbnail background, photorealistic, natural lighting, strong contrast, "
        f"real photograph, documentary style, 16:9, leave clear lower third for text overlay, "
        f"no text, no letters, no watermark, no logo, no CGI illustration"
    )
    try:
        path = generate_scene_image(
            chain, verifier, prompt, out_path,
            width=_THUMB_SIZE[0], height=_THUMB_SIZE[1],
            seed=_stable_seed(title, 9999),  # unique + reproducible per title
            verify_text=head,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Thumbnail generation failed: %s", exc)
        return None

    text = (hook_text or derive_thumbnail_hook(title)).strip()
    if text and text_enabled:
        _compose_thumbnail_text(path, text)
    return path


def _load_thumbnail_font(size: int):
    """Prefer bold system fonts that read on mobile thumbnails."""
    from PIL import ImageFont

    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _wrap_hook_lines(text: str, font, draw, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:3]  # hard cap for mobile readability


def _compose_thumbnail_text(image_path: Path, hook_text: str) -> Path:
    """Overlay high-contrast hook text on the lower third. Mutates the image in place."""
    from PIL import Image, ImageDraw, ImageFilter

    hook = re.sub(r"\s+", " ", hook_text).strip().upper()
    if not hook:
        return image_path
    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Thumbnail text compose skipped (open failed): %s", exc)
        return image_path

    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Dark gradient bar across the lower ~45% for text legibility.
    bar_top = int(h * 0.55)
    for y in range(bar_top, h):
        progress = (y - bar_top) / max(h - bar_top, 1)
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, int(40 + 170 * progress)))

    font_size = max(48, int(h * 0.11))
    font = _load_thumbnail_font(font_size)
    max_text_w = int(w * 0.90)
    lines = _wrap_hook_lines(hook, font, draw, max_text_w)
    for _ in range(6):  # shrink if still too wide
        widest = max((draw.textbbox((0, 0), ln, font=font)[2] for ln in lines), default=0)
        if widest <= max_text_w:
            break
        font_size = int(font_size * 0.9)
        font = _load_thumbnail_font(font_size)
        lines = _wrap_hook_lines(hook, font, draw, max_text_w)

    line_gap = int(font_size * 0.18)
    widths, heights = [], []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    block_h = sum(heights) + line_gap * (len(lines) - 1)
    y = h - block_h - int(h * 0.07)

    for line, lw, lh in zip(lines, widths, heights):
        x = (w - lw) // 2
        stroke = max(3, font_size // 18)  # outline for contrast on any background
        for dx, dy in (
            (-stroke, 0), (stroke, 0), (0, -stroke), (0, stroke),
            (-stroke, -stroke), (stroke, stroke), (-stroke, stroke), (stroke, -stroke),
        ):
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += lh + line_gap

    composed = Image.alpha_composite(img, overlay).convert("RGB")
    # Mild sharpen so text stays crisp after YouTube recompression.
    composed = composed.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
    composed.save(image_path, format="JPEG", quality=92)
    logger.info("Thumbnail text overlay applied: '%s' -> %s", hook, image_path)
    return image_path
