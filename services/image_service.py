"""
image_service.py — yt-video

AI image generation over a pluggable provider chain. config.IMAGE_PROVIDERS is
an ordered list tried per image; the first valid result wins. The PIL gradient
is always the final offline fallback, so generation never hard-fails.

  Providers (config.IMAGE_PROVIDERS):
    pollinations  — FREE, no API key  (default)
    openai        — PAID, gpt-image-1 / DALL-E  (needs OPENAI_API_KEY)
  Final fallback:
    PIL gradient  — locally generated, offline, always works

Default chain is free (Pollinations → PIL gradient), so the pipeline costs $0
unless you opt a paid provider into IMAGE_PROVIDERS, e.g.:
    IMAGE_PROVIDERS=openai,pollinations   # paid OpenAI first, free fallback

Add another service by registering a `_fetch_<name>` provider in
_IMAGE_PROVIDERS — the retry/validate/cache orchestration is provider-agnostic.
(DeepSeek is not listed: it has no image-generation API.)

Usage:
  from services.image_service import generate_scene_image, generate_scene_images_for_movers

  path = generate_scene_image("Indian stock market bull run", "scene_nifty.jpg")
  scenes = generate_scene_images_for_movers(["RELIANCE", "HDFCBANK", "TCS"])
"""

import hashlib
import os
import re
import shutil
import time
from io import BytesIO
from urllib.parse import quote
import requests
from logger import logger
from config import config
from services import clip_service

# Distinct seed offset between regeneration attempts — large prime so a retry
# lands on a genuinely different image, not a near-duplicate.
_SEED_STEP = 1009

_IMAGE_DIR = "output/images"
_CACHE_DIR = os.path.join(_IMAGE_DIR, ".cache")

# Validation thresholds
_MIN_VALID_BYTES = 2048           # anything smaller is almost certainly an error page
_MIN_PIXEL_VARIANCE = 50          # near-uniform images (blank, all-black) score < 50
_MIN_DIM_RATIO = 0.5              # accept anything ≥ 50% of requested dims


def _save_bytes(data: bytes, out_path: str) -> None:
    with open(out_path, "wb") as f:
        f.write(data)


def _cache_path_for(prompt: str, seed: int, model: str, width: int, height: int) -> str:
    """Content-addressed cache path. Same (prompt, seed, model, dims) → same file."""
    key = f"{prompt}|{seed}|{model}|{width}x{height}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{digest}.jpg")


def _stable_seed(text: str, modulus: int) -> int:
    """
    Deterministic seed from text. Prefer this over Python's hash(), which is
    randomized per process and breaks image-cache reproducibility.
    """
    if modulus <= 0:
        raise ValueError("modulus must be positive")
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulus


def _validate_image(data: bytes, expected_w: int, expected_h: int) -> tuple[bool, str]:
    """Reject corrupt, undersized, or near-uniform (blank/black) images."""
    if len(data) < _MIN_VALID_BYTES:
        return (False, f"too few bytes ({len(data)})")

    try:
        from PIL import Image
        img = Image.open(BytesIO(data))
        img.load()
    except Exception as exc:
        return (False, f"corrupt: {exc}")

    w, h = img.size
    if w < expected_w * _MIN_DIM_RATIO or h < expected_h * _MIN_DIM_RATIO:
        return (False, f"too small {w}x{h} (wanted {expected_w}x{expected_h})")

    try:
        small = img.convert("L").resize((64, 64))
        pixels = list(small.getdata())
        n = len(pixels)
        mean = sum(pixels) / n
        variance = sum((p - mean) ** 2 for p in pixels) / n
        if variance < _MIN_PIXEL_VARIANCE:
            return (False, f"near-uniform pixels (variance {variance:.1f})")
    except Exception:
        # If the variance check itself fails, accept the image — don't reject on tooling bugs.
        pass

    return (True, "")


# ===========================================================================
# Image providers — pluggable AI image services.
#
# Separation of concerns:
#   * A provider is a pure fetcher: (prompt, width, height, seed) -> bytes|None.
#     It only talks to its service and returns raw image bytes (or None on
#     failure). It does NOT retry, validate, save, or cache.
#   * The orchestrator (_obtain_image / _fetch_with_retries) owns retry,
#     validation, saving and caching — identical for every provider.
#   * config.IMAGE_PROVIDERS is the ordered chain to try; PIL gradient is the
#     always-on offline fallback (see generate_scene_image).
#
# To add a service (Stability, Gemini, …): write a `_fetch_<name>` returning
# bytes|None and register it in _IMAGE_PROVIDERS. Nothing else changes.
# ===========================================================================
def _fetch_pollinations(prompt: str, width: int, height: int, seed: int) -> bytes | None:
    """FREE — Pollinations.ai. Honours exact width/height and a seed for variety."""
    model = config.POLLINATIONS_MODEL
    encoded_prompt = quote(prompt, safe="")
    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={width}&height={height}&model={model}&seed={seed}&nologo=true"
    )
    headers = {}
    if config.POLLINATIONS_KEY:
        headers["Authorization"] = f"Bearer {config.POLLINATIONS_KEY}"
    try:
        response = requests.get(url, headers=headers, timeout=90)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type:
            raise ValueError(f"unexpected content-type: {content_type}")
        return response.content
    except Exception as exc:
        logger.warning(f"  pollinations fetch failed: {exc}")
        return None


def _openai_image_size(width: int, height: int) -> str:
    """Map requested dims to the nearest size the OpenAI Images API supports.

    gpt-image-1 only accepts 1024x1024 / 1536x1024 / 1024x1536, so pick by
    aspect ratio (landscape 16:9 → 1536x1024, portrait 9:16 → 1024x1536).
    """
    ratio = width / height if height else 1.0
    if ratio > 1.2:
        return "1536x1024"
    if ratio < 0.83:
        return "1024x1536"
    return "1024x1024"


def _fetch_openai(prompt: str, width: int, height: int, seed: int) -> bytes | None:
    """PAID — OpenAI Images API (gpt-image-1 / DALL-E). `seed` is unsupported, ignored."""
    if not config.OPENAI_API_KEY:
        logger.warning("  openai image provider enabled but OPENAI_API_KEY not set — skipping")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("  openai package not installed — skipping openai image provider")
        return None

    import base64

    size = _openai_image_size(width, height)
    model = config.OPENAI_IMAGE_MODEL
    logger.info(f"  openai [{model} {size} q={config.OPENAI_IMAGE_QUALITY}] generating…")
    try:
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        result = client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            quality=config.OPENAI_IMAGE_QUALITY,
            n=1,
        )
        item = result.data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:                                   # gpt-image-1 always returns base64
            return base64.b64decode(b64)
        url = getattr(item, "url", None)
        if url:                                   # dall-e-3 returns a URL
            r = requests.get(url, timeout=90)
            r.raise_for_status()
            return r.content
        logger.warning("  openai image response had no image data")
        return None
    except Exception as exc:
        logger.warning(f"  openai image generation failed: {exc}")
        return None


# Registry: provider name (as used in config.IMAGE_PROVIDERS) → fetcher.
_IMAGE_PROVIDERS: dict[str, "callable[[str, int, int, int], bytes | None]"] = {
    "pollinations": _fetch_pollinations,
    "openai":       _fetch_openai,
}


def _provider_cache_tag() -> str:
    """Stable cache tag for the active provider chain so changing providers/
    models/quality regenerates rather than serving a stale cached image."""
    parts: list[str] = []
    for name in config.IMAGE_PROVIDERS:
        if name == "openai":
            parts.append(f"openai-{config.OPENAI_IMAGE_MODEL}-{config.OPENAI_IMAGE_QUALITY}")
        elif name == "pollinations":
            parts.append(f"pollinations-{config.POLLINATIONS_MODEL}")
        else:
            parts.append(name)
    return ",".join(parts) or "none"


def _fetch_with_retries(
    fetch: "callable[[str, int, int, int], bytes | None]",
    name: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    retries: int,
    out_path: str,
) -> bool:
    """Run one provider with retry + validation; save to out_path on first valid image."""
    logger.info(f"Image provider '{name}': generating — '{prompt[:70]}…'")
    for attempt in range(1, retries + 1):
        data = fetch(prompt, width, height, seed)
        if data:
            valid, reason = _validate_image(data, width, height)
            if valid:
                _save_bytes(data, out_path)
                logger.info(f"  ✓ {name} saved ({len(data) // 1024} KB) → {out_path}")
                return True
            logger.warning(f"  {name} attempt {attempt}/{retries}: validation failed — {reason}")
        else:
            logger.warning(f"  {name} attempt {attempt}/{retries}: no image returned")
        if attempt < retries:
            time.sleep(5 * attempt)
    return False


# ---------------------------------------------------------------------------
# Fallback 2 — PIL gradient (always works, offline)
# ---------------------------------------------------------------------------
def _generate_gradient_fallback(
    symbol: str,
    out_path: str,
    width: int,
    height: int,
) -> str:
    """Generate a dark financial-style gradient background with PIL."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
    except ImportError:
        from PIL import Image, ImageDraw
        np = None  # type: ignore

    logger.info(f"PIL gradient fallback: generating for {symbol}…")

    # Dark blue-to-dark-navy gradient
    if np is not None:
        top_color    = (13, 17, 45)    # deep navy
        bottom_color = (5, 5, 20)      # near-black
        arr = np.zeros((height, width, 3), dtype=np.uint8)
        for i in range(height):
            t = i / height
            arr[i, :] = [
                int(top_color[c] + t * (bottom_color[c] - top_color[c]))
                for c in range(3)
            ]
        img = Image.fromarray(arr, "RGB")
    else:
        img = Image.new("RGB", (width, height), color=(13, 17, 45))

    # Subtle grid lines
    draw = ImageDraw.Draw(img, "RGBA")
    grid_color = (255, 255, 255, 15)
    for x in range(0, width, 80):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, 80):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    # Symbol label
    try:
        font = ImageFont.truetype("arial.ttf", size=72)
    except Exception:
        font = ImageFont.load_default()
    text = symbol
    bbox = draw.textbbox((0, 0), text, font=font)
    tx = (width - (bbox[2] - bbox[0])) // 2
    ty = (height - (bbox[3] - bbox[1])) // 2
    draw.text((tx, ty), text, fill=(255, 255, 255, 120), font=font)

    img.save(out_path, "JPEG", quality=90)
    logger.info(f"  ✓ PIL gradient saved → {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _obtain_image(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    retries: int,
) -> str | None:
    """Return a cache path holding a valid image for (prompt, seed), or None.

    Reuses the content-addressed cache; on a miss, tries each provider in
    config.IMAGE_PROVIDERS in order and returns the first that yields a valid
    image. The PIL gradient fallback is applied one level up.
    """
    cache_path = _cache_path_for(prompt, seed, _provider_cache_tag(), width, height)
    if os.path.exists(cache_path) and os.path.getsize(cache_path) >= _MIN_VALID_BYTES:
        logger.info(f"image_service: cache hit → {os.path.basename(cache_path)}")
        return cache_path

    for provider_name in config.IMAGE_PROVIDERS:
        fetch = _IMAGE_PROVIDERS.get(provider_name)
        if fetch is None:
            logger.warning(f"image_service: unknown provider '{provider_name}' — skipping")
            continue
        if _fetch_with_retries(
            fetch, provider_name, prompt, width, height, seed, retries, cache_path
        ):
            return cache_path
    return None


def generate_scene_image(
    prompt: str,
    filename: str,
    width: int  = 1920,
    height: int = 1080,
    seed: int   = 42,
    retries: int = 3,
    symbol: str  = "",
    verify_text: str = "",                 # subject to score the image against (CLIP)
    verify_attempts: int = 3,              # max regeneration tries to clear threshold
    skip_cache: bool = False,              # deprecated, ignored
    allow_picsum_fallback: bool = False,   # deprecated, ignored
) -> str:
    """
    Generate a background image:
      1. Pollinations.ai (AI-generated, content-addressed cache)
      2. PIL gradient    (locally generated, always works)

    Caching is keyed on (prompt, seed, model, dims) via a sha1 hash — same
    request returns instantly, different prompt/seed regenerates.

    Content-to-scene accuracy
    -------------------------
    When `verify_text` is given and the CLIP backend is installed, each
    generated image is scored for semantic relevance to `verify_text`. An
    image below `clip_service.CLIP_MIN_SCORE` is rejected and regenerated
    with a fresh seed, up to `verify_attempts` times. If none clears the
    threshold, the best-scoring attempt is used. With no `verify_text` or no
    CLIP backend, the first valid image is accepted (legacy behaviour).

    Removed (both accepted as kwargs for call-site compatibility):
      - skip_cache: redundant once cache key encodes the request
      - allow_picsum_fallback: Picsum served random photos — exactly the
        "totally different kind of images" we were trying to fix.
    """
    del skip_cache, allow_picsum_fallback

    os.makedirs(_IMAGE_DIR, exist_ok=True)
    out_path = os.path.join(_IMAGE_DIR, filename)

    verify   = bool(verify_text) and clip_service.is_enabled()
    attempts = max(1, verify_attempts) if verify else 1

    best_path: str | None = None
    best_score = -1.0

    for attempt in range(attempts):
        attempt_seed = seed + attempt * _SEED_STEP
        cache_path = _obtain_image(prompt, width, height, attempt_seed, retries)
        if cache_path is None:
            continue

        if not verify:
            shutil.copyfile(cache_path, out_path)
            return out_path

        score = clip_service.score_image_prompt(cache_path, verify_text)
        if score is None:
            # Backend went unavailable mid-run — accept the first valid image.
            shutil.copyfile(cache_path, out_path)
            return out_path

        logger.info(
            f"  CLIP relevance {score:.3f} for {filename} "
            f"(attempt {attempt + 1}/{attempts}, threshold {clip_service.CLIP_MIN_SCORE})"
        )
        if score > best_score:
            best_score, best_path = score, cache_path
        if score >= clip_service.CLIP_MIN_SCORE:
            clip_service.record_score(score)
            shutil.copyfile(cache_path, out_path)
            return out_path

    if best_path is not None:
        logger.warning(
            f"  {filename}: no attempt cleared CLIP threshold — "
            f"using best image (score {best_score:.3f})"
        )
        clip_service.record_score(best_score)
        shutil.copyfile(best_path, out_path)
        return out_path

    sym = symbol or os.path.splitext(filename)[0].split("_")[0].upper()
    logger.warning("Pollinations.ai unavailable — using PIL gradient fallback…")
    return _generate_gradient_fallback(sym, out_path, width, height)


def generate_scene_images_for_movers(
    movers: list[str],
    symbol_names: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Generate one thematic background illustration for each stock/index.

    Parameters
    ----------
    movers       : list of NSE symbols, e.g. ['RELIANCE', 'HDFCBANK', 'NIFTY']
    symbol_names : optional {symbol: full_name} mapping for richer prompts

    Returns: {symbol: image_path} — symbols without a generated image are omitted.
    """
    results: dict[str, str] = {}

    SYMBOL_STYLE_HINTS: dict[str, str] = {
        "NIFTY":      "Indian stock exchange trading floor, Nifty 50 index, digital screens",
        "RELIANCE":   "Reliance Industries oil refinery and telecom towers",
        "HDFCBANK":   "modern bank building, digital banking, Indian financial district",
        "ICICIBANK":  "ICICI bank branch, digital payments India",
        "INFY":       "Infosys tech campus, Indian IT industry",
        "TCS":        "Tata Consultancy Services office, global IT services",
        "WIPRO":      "Wipro technology campus, software development",
        "TMPV": "Tata Motors passenger cars electric vehicles, Indian automotive factory",
        "TMCV": "Tata Motors trucks buses commercial vehicles, Indian transport industry",
        "MARUTI":     "Maruti Suzuki car showroom, Indian automobile industry",
        "BHARTIARTL": "Airtel 5G towers, Indian telecom network",
        "BAJFINANCE": "Bajaj Finance digital lending, fintech India",
        "ADANIENT":   "Adani ports and logistics, infrastructure India",
        "SUNPHARMA":  "pharmaceutical research lab, Indian pharma industry",
        "DRREDDY":    "Dr Reddys laboratory, medicine research India",
        "HINDALCO":   "aluminium smelter, metals manufacturing India",
        "TATASTEEL":  "Tata steel plant, Indian steel industry",
        "NTPC":       "NTPC power plant, Indian electricity generation",
        "ONGC":       "ONGC offshore oil platform, Indian energy sector",
        "COALINDIA":  "coal mine, Indian energy extraction",
    }

    default_hint = "Indian stock market data visualization, financial charts, dark background"

    for symbol in movers:
        hint = SYMBOL_STYLE_HINTS.get(symbol.upper(), default_hint)
        full_name = (symbol_names or {}).get(symbol, "")
        subject = f"{full_name} ({symbol}): {hint}" if full_name else f"{symbol}: {hint}"

        # Subject first — diffusion models weight early tokens most.
        prompt = (
            f"{subject}. "
            f"photorealistic financial news photo, natural lighting, real world scene, "
            f"documentary photography, 16:9, no text, no watermark"
        )

        try:
            path = generate_scene_image(
                prompt      = prompt,
                filename    = f"{symbol.lower()}_scene.jpg",
                symbol      = symbol,
                verify_text = subject,
            )
            results[symbol] = path
        except Exception as exc:
            logger.warning(f"Image generation failed for {symbol}: {exc}")

    logger.info(f"Scene images generated: {list(results.keys())}")
    return results


def _normalize_visual_text(text: str) -> str:
    """Clean narration text so prompt keywords are readable to the image model."""
    if not text:
        return ""

    cleaned = re.sub(r"\b(?:[A-Za-z]-){2,}[A-Za-z]\b", lambda m: m.group(0).replace("-", ""), text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


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


def generate_scene_images_for_scenes(
    labels: list[str],
    topic: str,
    angle: str = "",
    scene_texts: dict[str, str] | None = None,
    scene_prompt_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Generate one unique thematic image per scene label.

    Unlike generate_scene_images_for_movers (which is for NSE stock symbols),
    this function builds topic-aware prompts for generic scene labels like
    intro, section1, section2 … outro, using different seeds so every
    image is visually distinct.

    Parameters
    ----------
    labels : scene label strings from [SCENE:label] markers, e.g.
             ['intro', 'section1', 'section2', 'section3', 'section4', 'outro']
    topic  : the video topic, used as the visual subject in every prompt
    angle  : optional creative angle / sub-focus for richer prompts

    Returns: {label: image_path}
    """
    # Position-based visual style descriptors — documentary / photo-real, not AI-art
    _POSITION_STYLES = [
        "wide establishing photo, natural daylight, documentary look",
        "close-up detail, shallow depth of field, real photograph",
        "environmental mid-shot, natural light, candid documentary photo",
        "action moment captured in-camera, high shutter, real-world scene",
        "portrait framing, soft natural light, photojournalism style",
        "elevated overview, real location, documentary photography",
        "quiet closing frame, soft ambient light, authentic photograph",
    ]

    context = f"{topic}" + (f" — {angle}" if angle else "")
    results: dict[str, str] = {}

    # Fresh accuracy accumulator for this video's scene set.
    clip_service.reset_session()

    for i, label in enumerate(labels):
        style = _POSITION_STYLES[i % len(_POSITION_STYLES)]
        scene_text = (scene_texts or {}).get(label, "")
        scene_focus = _scene_focus_text(scene_text)
        scene_override = _normalize_visual_text((scene_prompt_overrides or {}).get(label, ""))

        # Subject leads, style trails. Diffusion models weight early tokens most,
        # so leading with style descriptors ("dramatic opening shot…") used to
        # bury the actual topic and produce off-topic images.
        # Preference: script-LLM scene_prompt > scene narration focus > raw topic.
        if scene_override:
            subject = scene_override
        elif scene_focus:
            subject = scene_focus
        else:
            subject = context

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

        # Unique seed per scene so images are visually distinct
        seed = (i + 1) * 7 + _stable_seed(f"{label}:{topic}", 1000)

        try:
            path = generate_scene_image(
                prompt      = prompt,
                filename    = f"{label}_scene.jpg",
                width       = 1920,
                height      = 1080,
                seed        = seed,
                retries     = 3,
                verify_text = subject,
            )
            results[label] = path
        except Exception as exc:
            logger.warning(f"Image generation failed for {label}: {exc}")

    logger.info(f"Scene images generated: {list(results.keys())}")
    logger.info(clip_service.session_summary())
    return results


def generate_thumbnail(
    title: str,
    topic: str = "",
    out_filename: str = "thumbnail.jpg",
    movers: list[str] | None = None,   # kept for backward compat, ignored
    prompt_override: str = "",
    hook_text: str = "",
) -> str:
    """
    Generate a YouTube thumbnail (1280x720).

    AI paints a text-free subject image; Pillow then overlays a 3–5 word
    mobile CTR hook (unless THUMBNAIL_TEXT_ENABLED=0).

    Returns: path to saved thumbnail.
    """
    from services.packaging_service import derive_thumbnail_hook

    subject = topic or title
    clean_override = _normalize_visual_text(prompt_override)
    # Subject leads, style trails. Prefer the script-LLM thumbnail prompt; else
    # use the (cleaned) title/topic directly. No keyword expansion — the script
    # LLM already understands the topic; substring matching produced wrong
    # categories (e.g. "stock" → trading charts on unrelated videos).
    head = clean_override or _normalize_visual_text(
        f"{title}. {subject}" if subject and subject != title else (title or subject)
    )

    prompt = (
        f"{head}. "
        f"YouTube thumbnail background, photorealistic, natural lighting, strong contrast, "
        f"real photograph, documentary style, 16:9, leave clear lower third for text overlay, "
        f"no text, no letters, no watermark, no logo, no CGI illustration"
    )

    path = generate_scene_image(
        prompt      = prompt,
        filename    = out_filename,
        width       = 1280,
        height      = 720,
        seed        = _stable_seed(title, 9999),   # unique + reproducible per title
        symbol      = "thumbnail",
        verify_text = head,
    )

    text = (hook_text or derive_thumbnail_hook(title)).strip()
    if text and config.THUMBNAIL_TEXT_ENABLED:
        path = _compose_thumbnail_text(path, text)
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
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
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


def _compose_thumbnail_text(image_path: str, hook_text: str) -> str:
    """
    Overlay high-contrast hook text on the lower third of a thumbnail.
    Mutates the image in place and returns the same path.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    hook = re.sub(r"\s+", " ", hook_text).strip().upper()
    if not hook:
        return image_path

    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as exc:
        logger.warning(f"Thumbnail text compose skipped (open failed): {exc}")
        return image_path

    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Dark gradient bar across the lower ~42% for text legibility
    bar_top = int(h * 0.55)
    for y in range(bar_top, h):
        progress = (y - bar_top) / max(h - bar_top, 1)
        alpha = int(40 + 170 * progress)
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    # Fit font to width
    font_size = max(48, int(h * 0.11))
    font = _load_thumbnail_font(font_size)
    max_text_w = int(w * 0.90)
    lines = _wrap_hook_lines(hook, font, draw, max_text_w)
    # Shrink if still too wide
    for _ in range(6):
        widest = max(
            (draw.textbbox((0, 0), line, font=font)[2] for line in lines),
            default=0,
        )
        if widest <= max_text_w:
            break
        font_size = int(font_size * 0.9)
        font = _load_thumbnail_font(font_size)
        lines = _wrap_hook_lines(hook, font, draw, max_text_w)

    line_gap = int(font_size * 0.18)
    heights = []
    widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    block_h = sum(heights) + line_gap * (len(lines) - 1)
    y = h - block_h - int(h * 0.07)

    # Soft shadow plate behind text
    for line, lw, lh in zip(lines, widths, heights):
        x = (w - lw) // 2
        # Stroke / outline for contrast on any background
        stroke = max(3, font_size // 18)
        for dx, dy in (
            (-stroke, 0), (stroke, 0), (0, -stroke), (0, stroke),
            (-stroke, -stroke), (stroke, stroke), (-stroke, stroke), (stroke, -stroke),
        ):
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += lh + line_gap

    composed = Image.alpha_composite(img, overlay).convert("RGB")
    # Mild sharpen so text stays crisp after YouTube recompression
    composed = composed.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
    composed.save(image_path, quality=92)
    logger.info(f"Thumbnail text overlay applied: '{hook}' → {image_path}")
    return image_path
