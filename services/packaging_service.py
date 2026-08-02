"""
packaging_service.py — click-auction helpers for Phase A.

Owns title/thumb text, description chapters, category mapping, and
human-readable scene titles used in overlays and YouTube chapters.
"""

from __future__ import annotations

import re
from logger import logger

# YouTube category IDs:
#   17 Sports · 22 People & Blogs · 24 Entertainment · 25 News & Politics
#   26 Howto & Style · 27 Education · 28 Science & Technology
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("28", ("ai", "chatgpt", "openai", "tech", "software", "startup", "gadget",
            "iphone", "android", "robot", "semiconductor", "chip", "cloud")),
    ("17", ("cricket", "ipl", "football", "soccer", "olympics", "match", "sports",
            "tennis", "nba", "world cup")),
    ("27", ("explained", "how to", "howto", "learn", "guide", "tutorial",
            "history of", "science of")),
    ("26", ("stock", "nifty", "sensex", "ipo", "invest", "mutual fund", "rupee",
            "budget", "rbi", "finance", "market", "trading", "crypto", "bitcoin")),
    ("25", ("news", "election", "government", "policy", "parliament", "modi",
            "breaking")),
    ("24", ("celebrity", "bollywood", "movie", "film", "netflix", "series",
            "viral")),
]


def derive_thumbnail_hook(title: str, max_words: int = 5) -> str:
    """
    Build a 3–5 word mobile-readable hook from the SEO title.
    Used as on-image thumbnail text (Pillow overlay).
    """
    if not title:
        return "WATCH THIS"
    # Prefer the punchiest clause before a separator
    head = re.split(r"[|–—:\-]", title, maxsplit=1)[0].strip()
    words = re.findall(r"[A-Za-z0-9']+", head)
    if not words:
        return "WATCH THIS"
    hook = " ".join(words[:max_words])
    return hook.upper()


def humanize_scene_label(label: str, scene_text: str = "") -> str:
    """Turn s1/intro markers into viewer-facing chapter/overlay titles."""
    label = (label or "").strip()
    if scene_text:
        first = scene_text.strip().split(".")[0].strip()
        words = re.findall(r"[A-Za-z0-9']+", first)
        if len(words) >= 3:
            phrase = " ".join(words[:6])
            return phrase[:48].rstrip()

    special = {"intro": "Introduction", "outro": "Key Takeaway"}
    if label.lower() in special:
        return special[label.lower()]

    if re.fullmatch(r"s\d+", label, flags=re.IGNORECASE):
        return f"Part {label[1:]}"

    return label.replace("_", " ").replace("-", " ").title()


def scene_titles_from_script(
    labels: list[str],
    scene_texts: dict[str, str] | None = None,
) -> dict[str, str]:
    texts = scene_texts or {}
    return {label: humanize_scene_label(label, texts.get(label, "")) for label in labels}


def format_chapter_timestamps(
    timeline: dict[str, float] | None,
    scene_titles: dict[str, str] | None = None,
) -> str:
    """
    YouTube chapters block for the description.
    Requires ≥3 chapters; first timestamp forced to 0:00.
    """
    if not timeline:
        return ""

    items = sorted(timeline.items(), key=lambda x: float(x[1]))
    if len(items) < 3:
        logger.debug("Skipping chapters — need at least 3 timeline entries")
        return ""

    lines = ["Chapters:"]
    for index, (label, start) in enumerate(items):
        seconds = 0.0 if index == 0 else max(float(start), 0.0)
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        title = (scene_titles or {}).get(label) or humanize_scene_label(label)
        lines.append(f"{mins}:{secs:02d} {title}")

    return "\n".join(lines)


def build_publish_description(
    base_description: str,
    timeline: dict[str, float] | None = None,
    scene_titles: dict[str, str] | None = None,
) -> str:
    """Merge SEO blurb with auto-generated chapter timestamps."""
    base = (base_description or "").strip()
    chapters = format_chapter_timestamps(timeline, scene_titles)
    if not chapters:
        return base
    if not base:
        return chapters
    return f"{base}\n\n{chapters}"


def resolve_category_id(
    topic: str,
    title: str = "",
    default: str = "22",
) -> str:
    """
    Map topic/title keywords → YouTube categoryId.
    Defaults to People & Blogs (22) when no rule matches — safer than
    always forcing News & Politics.
    """
    haystack = f"{topic} {title}".lower()
    for category_id, keywords in _CATEGORY_RULES:
        if any(_keyword_matches(haystack, kw) for kw in keywords):
            logger.info(f"Category mapped -> {category_id} (topic match)")
            return category_id
    logger.info(f"Category fallback -> {default}")
    return default or "22"


def _keyword_matches(haystack: str, keyword: str) -> bool:
    """
    Match keywords without false positives like "ai" inside "daily".
    Multi-word phrases stay substring matches; single tokens use word boundaries.
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return False
    if " " in kw:
        return kw in haystack
    return re.search(rf"\b{re.escape(kw)}\b", haystack) is not None


def decide_privacy(score: int, safety: str, threshold: int = 80) -> str:
    """Public only when quality score clears threshold AND safety is Yes."""
    safe = (safety or "").strip().lower() == "yes"
    if score >= threshold and safe:
        return "public"
    return "private"


# ---------------------------------------------------------------------------
# Phase B — title / thumb packaging A/B
# ---------------------------------------------------------------------------

_CLICKBAIT_SPAM = (
    "you won't believe", "gone wrong", "shocking", "must watch",
    "click here", "number 7 will", "doctors hate",
)


def score_packaging_variant(variant: dict) -> float:
    """
    Heuristic CTR score for a {title, hook} package.
    Higher is better. Used to auto-pick among A/B variants.
    """
    title = (variant.get("title") or "").strip()
    hook = (variant.get("hook") or "").strip()
    if not title:
        return -100.0

    score = 0.0
    tlen = len(title)
    # Sweet spot ~40–65 chars for mobile title truncation
    if 40 <= tlen <= 65:
        score += 3.0
    elif 30 <= tlen < 40 or 65 < tlen <= 75:
        score += 1.5
    else:
        score -= 1.0

    if re.search(r"\d", title):
        score += 1.5
    if re.search(r"[A-Z][a-z]+", title):  # proper-noun-ish casing present
        score += 0.5
    if "?" in title:
        score += 0.75

    low = title.lower()
    if any(spam in low for spam in _CLICKBAIT_SPAM):
        score -= 4.0

    hook_words = re.findall(r"[A-Za-z0-9']+", hook)
    if 3 <= len(hook_words) <= 5:
        score += 2.0
    elif hook_words:
        score += 0.5
    else:
        score -= 1.0

    return round(score, 2)


def pick_best_packaging(variants: list[dict]) -> dict:
    """Return the highest-scoring packaging variant (keeps input order on ties)."""
    if not variants:
        return {"title": "", "hook": "WATCH THIS", "variant_id": "fallback", "score": 0.0}
    best = max(
        enumerate(variants),
        key=lambda pair: (score_packaging_variant(pair[1]), -pair[0]),
    )[1]
    best = dict(best)
    best["score"] = score_packaging_variant(best)
    return best


def generate_packaging_variants(
    topic: str,
    base_title: str = "",
    angle: str = "",
    n: int | None = None,
) -> list[dict]:
    """
    Generate n title/hook packages via one cheap LLM call.
    Falls back to deterministic variants from base_title when LLM fails.
    """
    from config import config

    count = n or config.PACKAGING_VARIANTS
    count = max(1, min(int(count), 5))
    base = (base_title or topic or "Today's Trend").strip()

    variants: list[dict] = []
    try:
        from openai import OpenAI
        from services.cost_service import record_llm

        model = config.SCRIPT_MODEL
        if model.startswith("deepseek"):
            client = OpenAI(
                base_url="https://api.deepseek.com",
                api_key=config.DEEPSEEK_API_KEY or config.OPENAI_API_KEY,
            )
        else:
            client = OpenAI(api_key=config.OPENAI_API_KEY)

        angle_line = f"ANGLE: {angle}\n" if angle else ""
        prompt = f"""You write YouTube packaging for an India 18–35 English channel.
TOPIC: {topic}
BASE TITLE: {base}
{angle_line}
Return exactly {count} alternative packages as JSON array only:
[
  {{"title": "under 70 chars, specific, curiosity without spam", "hook": "3 to 5 UPPERCASE words for thumbnail"}},
  ...
]
No markdown, no commentary."""

        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        if res.usage:
            record_llm(
                "yt_video", "packaging_variants", model,
                res.usage.prompt_tokens, res.usage.completion_tokens,
            )
        raw = (res.choices[0].message.content or "").strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            parsed = __import__("json").loads(match.group())
            for i, item in enumerate(parsed[:count]):
                title = str(item.get("title") or base).strip()[:70]
                hook = str(item.get("hook") or derive_thumbnail_hook(title)).strip().upper()
                hook_words = re.findall(r"[A-Za-z0-9']+", hook)
                hook = " ".join(hook_words[:5]).upper() or derive_thumbnail_hook(title)
                variants.append({
                    "title": title,
                    "hook": hook,
                    "variant_id": f"v{i + 1}",
                })
    except Exception as exc:
        logger.warning(f"packaging variants LLM failed ({exc}) — using fallbacks")

    if not variants:
        # Deterministic fallbacks so the pipeline never stalls
        hook = derive_thumbnail_hook(base)
        variants = [
            {"title": base[:70], "hook": hook, "variant_id": "v1"},
            {
                "title": (f"{base.rstrip('.')} — Explained")[:70],
                "hook": hook,
                "variant_id": "v2",
            },
            {
                "title": (f"What Changed: {topic}")[:70],
                "hook": derive_thumbnail_hook(topic or base),
                "variant_id": "v3",
            },
        ][:count]

    for v in variants:
        v["score"] = score_packaging_variant(v)
    logger.info(
        "Packaging variants: "
        + ", ".join(f"{v['variant_id']}={v['score']}" for v in variants)
    )
    return variants
