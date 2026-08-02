"""
script_service.py — yt-video  (trend-based general topics)

Mega-prompt strategy: ONE AI call produces everything needed per video:
  • Long-form script  (8–12 min) with [SCENE:label] visual cue markers
  • Shorts script     (50–60 sec) hook + 3-beat structure
  • SEO metadata      YouTube title, description (480 chars), 10 tags

[SCENE:label] markers tell the renderer which Pollinations image to show.
They are stripped before TTS so the voice never reads them.

Supported models (set SCRIPT_MODEL in .env):
  gpt-4o-mini       OpenAI   ~$0.06/month  default
  deepseek-chat     DeepSeek ~$0.01/month  cheapest
  deepseek-reasoner DeepSeek ~$0.04/month  best quality
"""

import re
import os
from openai import OpenAI, RateLimitError, AuthenticationError
from config import config
from logger import logger
from services.cost_service import record_llm


# ---------------------------------------------------------------------------
# Output container
# ---------------------------------------------------------------------------
class ScriptOutput:
    """Parsed output from the mega-prompt."""
    def __init__(
        self,
        long_script: str,
        shorts_script: str,
        seo_title: str,
        seo_description: str,
        seo_tags: list[str],
        scene_prompts: dict[str, str],
        thumbnail_prompt: str,
    ):
        self.long_script     = long_script
        self.shorts_script   = shorts_script
        self.seo_title       = seo_title
        self.seo_description = seo_description
        self.seo_tags        = seo_tags
        self.scene_prompts   = scene_prompts
        self.thumbnail_prompt = thumbnail_prompt

    def to_dict(self) -> dict:
        return {
            "long_script":     self.long_script,
            "shorts_script":   self.shorts_script,
            "seo_title":       self.seo_title,
            "seo_description": self.seo_description,
            "seo_tags":        self.seo_tags,
            "scene_prompts":   self.scene_prompts,
            "thumbnail_prompt": self.thumbnail_prompt,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScriptOutput":
        return cls(
            long_script     = d.get("long_script", ""),
            shorts_script   = d.get("shorts_script", ""),
            seo_title       = d.get("seo_title", ""),
            seo_description = d.get("seo_description", ""),
            seo_tags        = d.get("seo_tags", []),
            scene_prompts   = d.get("scene_prompts", {}),
            thumbnail_prompt = d.get("thumbnail_prompt", ""),
        )


# ---------------------------------------------------------------------------
# AI client
# ---------------------------------------------------------------------------
def _get_client() -> OpenAI:
    model = config.SCRIPT_MODEL
    if model.startswith("deepseek"):
        logger.debug(f"Using DeepSeek API for model: {model}")
        return OpenAI(
            base_url="https://api.deepseek.com",
            api_key=config.DEEPSEEK_API_KEY or config.OPENAI_API_KEY,
        )
    return OpenAI(api_key=config.OPENAI_API_KEY)


# ---------------------------------------------------------------------------
# Mega-prompt format
# ---------------------------------------------------------------------------
_OUTPUT_FORMAT = """\
Respond ONLY with these exact labelled sections, nothing else:

[LONG_SCRIPT]
<the full narration script — 8 to 12 minutes when read aloud>
CRITICAL RULES for [LONG_SCRIPT]:
- Write ONLY the spoken words the narrator will say. Pure narration, nothing else.
- NO stage directions, NO parenthetical cues, NO action lines, NO scene descriptions.
- NO "Narrator:", NO "Host:", NO speaker labels of any kind.
- NO lines starting with "(" — every line must be spoken dialogue.
- Place a [SCENE:label] marker every 2-4 sentences — at EVERY point where the
  visual subject changes (a new topic, example, place, person, number, or event).
- Use labels in this exact scheme: [SCENE:intro], then [SCENE:s1], [SCENE:s2],
  [SCENE:s3] ... numbered sequentially with no gaps, then [SCENE:outro] last.
- Produce 24 to 36 scene markers in total. A long stretch with no marker is wrong.
- Each [SCENE:label] tag MUST be on its own line, alone, exactly as shown.

SOUND HUMAN (not AI):
- Open with a cold open — one concrete moment, number, or surprising detail.
  Talk like a sharp friend explaining something, not a corporate explainer.
- FORBIDDEN openings / phrases (never use these):
  "Real data. Verified sources." / "No speculation." / "No guesswork."
  "In this video we'll…" / "Today we're going to…" / "Without further ado"
  "Let's dive in" / "Buckle up" / "Game changer" / "In today's fast-paced world"
  "It's important to note" / "At the end of the day" / "Here's why it matters"
- Use contractions (you'll, that's, won't, here's). Mix short punchy lines
  with slightly longer ones. Ask a rhetorical question every few minutes.
- Include 2–4 natural asides ("look,", "here's the thing,", "and this is wild —")
  so the pacing feels spoken, not essay-written.
- Prefer specific names, places, numbers, and dates over vague claims.
- End with a soft CTA that feels like a real person asking a question,
  not a template subscribe pitch.
- Structure: Cold open (2–4 spoken lines) → [SCENE:intro] → 5 content chapters,
  EACH broken into 4–6 [SCENE:sN] beats → soft CTA → [SCENE:outro].
- Do NOT use any markdown formatting or headers.

[SHORTS_SCRIPT]
<a punchy 50–60 second version — spoken words only, same human-voice rules as above>

[SEO_TITLE]
<one YouTube title — under 70 characters, high click-through, no clickbait>

[SEO_DESCRIPTION]
<YouTube description — 400–480 characters, natural language, ends with a CTA>

[SEO_TAGS]
<exactly 10 comma-separated tags, no hashtags, mix broad + specific>

[SCENE_PROMPTS]
<one line per scene marker used in LONG_SCRIPT, format exactly: label | visual brief>
CRITICAL RULES for [SCENE_PROMPTS]:
- Include EVERY scene label used in LONG_SCRIPT, including intro and outro.
  There will be 24-36 labels — produce one line for every single one, no gaps.
- Each line must start with the exact scene label, then ` | `, then a concise visual brief.
- The brief must depict the SPECIFIC thing that scene's narration is about — match
  the subject of those 2-4 sentences exactly, not the video's general topic.
- Visual brief must describe what should be visible on screen, not what the narrator says.
- Lead with a real-world subject. Prefer photorealistic / documentary / natural light
  cues over "cinematic AI art", neon, glow, or dark gradient aesthetics.
- Use concrete subjects, actions, setting, mood, camera framing, and domain-specific objects.
- Avoid vague phrases like "relevant to topic" or "about this subject".
- DO NOT include text overlays, captions, logos, watermarks, or UI screenshots unless the topic truly requires them.
- Example: `s2 | Cricket bowler releasing a yorker under stadium lights, batsman defending, documentary sports photo, natural light`

[THUMBNAIL_PROMPT]
<one concise visual brief for the thumbnail>
CRITICAL RULES for [THUMBNAIL_PROMPT]:
- Focus on one striking real-world visual concept for high click-through.
- Prefer photorealistic subject + strong contrast. Avoid plastic AI-illustration looks.
- No text, no watermark, no logo clutter."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_script(
    topic: str,
    trend_signals: list[str] | None = None,
    angle: str | None = None,
) -> "ScriptOutput":
    """
    Generate a full mega-script in one AI call.

    Parameters
    ----------
    topic          : the primary topic to make a video about
    trend_signals  : related trending signals to weave into the script
    angle          : optional framing suggestion from AI topic filter

    Returns: ScriptOutput with long_script, shorts_script, seo_title,
             seo_description, seo_tags all parsed and ready to use.
    """
    model = config.SCRIPT_MODEL
    logger.info(f"Generating mega-script — model={model}, topic='{topic}'")

    trend_block = ""
    if trend_signals:
        top     = trend_signals[:5]
        bullets = "\n".join(f"  • {t}" for t in top)
        trend_block = (
            "\nRELATED TRENDING SIGNALS (weave 1-2 naturally where relevant):\n"
            + bullets + "\n"
        )
        logger.info(f"Injecting {len(top)} trend signals into prompt")

    angle_block = f"\nSUGGESTED ANGLE: {angle}\n" if angle else ""

    # Inject learned TTS style notes from previous runs (empty on first run)
    try:
        from services.tts_memory_service import get_prompt_injection
        memory_block = get_prompt_injection()
    except Exception:
        memory_block = ""
    if memory_block:
        logger.info("Injecting TTS style memory from previous runs into prompt")

    # Inject YouTube Analytics performance notes from previous runs
    analytics_block = ""
    try:
        from services.yt_analytics_memory_service import get_script_prompt_injection
        analytics_block = get_script_prompt_injection()
    except Exception:
        pass
    if analytics_block:
        logger.info("Injecting YouTube Analytics performance notes into prompt")

    prompt = f"""You are a YouTube creator who scripts the way people actually talk on camera —
curious, specific, and a little imperfect. Never sound like a corporate AI explainer.

VIDEO TOPIC: {topic}
{angle_block}{trend_block}
CHANNEL STYLE: A sharp friend walking you through something interesting. Warm, direct, lightly witty.
Avoid polished brochure tone. Prefer concrete details over abstract hype.
TARGET AUDIENCE: India, 18–35, English-speaking, interested in trending real-world topics.
{analytics_block}{memory_block}
{_OUTPUT_FORMAT}
"""

    client = _get_client()
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except RateLimitError:
        logger.error("Quota exceeded. Add credits at https://platform.openai.com/settings/billing")
        raise
    except AuthenticationError:
        logger.error("Invalid API key — check OPENAI_API_KEY / DEEPSEEK_API_KEY in .env")
        raise

    if res.usage:
        record_llm("yt_video", "script", model, res.usage.prompt_tokens, res.usage.completion_tokens)
    raw = res.choices[0].message.content or ""
    logger.info(f"Mega-script received ({len(raw):,} chars) via {model}")

    output = _parse_mega_output(raw, topic)

    os.makedirs("output", exist_ok=True)
    with open("output/script.txt", "w", encoding="utf-8") as f:
        f.write(output.long_script)
    logger.debug("Long script saved → output/script.txt")

    return output


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------
_SECTION_RE = re.compile(
    r"\[(LONG_SCRIPT|SHORTS_SCRIPT|SEO_TITLE|SEO_DESCRIPTION|SEO_TAGS|SCENE_PROMPTS|THUMBNAIL_PROMPT)\]\s*\n(.*?)(?=\n\[(?:LONG_SCRIPT|SHORTS_SCRIPT|SEO_TITLE|SEO_DESCRIPTION|SEO_TAGS|SCENE_PROMPTS|THUMBNAIL_PROMPT)\]|\Z)",
    re.DOTALL,
)


def refine_scene_prompts(
    topic: str,
    angle: str | None,
    labels: list[str],
    scene_texts: dict[str, str],
    initial_prompts: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Rewrite per-scene briefs into tight, diffusion-friendly image prompts.

    The script LLM produces SCENE_PROMPTS as one of seven outputs in the mega-call
    and treats them as an afterthought. A focused second call — given the topic,
    each label's narration, and the initial brief — produces noticeably tighter
    image prompts: concrete subject first, no abstractions, ≤25 words.

    Returns: {label: refined_prompt}. Labels missing from the LLM response keep
    their initial brief (or are omitted if there was none).
    """
    if not labels:
        return {}

    angle_line = f"ANGLE: {angle}\n" if angle else ""

    scene_block_lines: list[str] = []
    for label in labels:
        narration = (scene_texts or {}).get(label, "").strip()
        existing  = (initial_prompts or {}).get(label, "").strip()
        line = f"- {label}:"
        if existing:
            line += f"\n    initial: {existing[:200]}"
        if narration:
            line += f"\n    narration: {narration[:300]}"
        scene_block_lines.append(line)
    scenes_text = "\n".join(scene_block_lines)

    prompt = f"""You are a prompt engineer for a text-to-image diffusion model (Flux).
Rewrite each scene brief into a tight image prompt.

TOPIC: {topic}
{angle_line}SCENES:
{scenes_text}

RULES per prompt:
- Lead with a concrete subject (a person, object, place, or action).
- Use vivid concrete nouns. No abstractions like "insight", "trend", "emotion".
- Maximum 25 words.
- End with 2-3 photo-real style keywords (e.g. "documentary photo, natural light, shallow depth of field").
- Prefer real photography cues. Avoid neon glow, dark gradients, plastic CGI, or "AI art" looks.
- Never include text, captions, watermarks, or UI elements.
- Each scene must be visually distinct from the others.

Respond with one line per scene, exactly:
LABEL | prompt text

Use the exact labels above, in the same order. No preamble, no commentary."""

    model = config.SCRIPT_MODEL
    client = _get_client()
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning(f"refine_scene_prompts: LLM call failed ({exc}); using initial prompts")
        return dict(initial_prompts or {})

    if res.usage:
        record_llm("yt_video", "scene_prompts_refine", model, res.usage.prompt_tokens, res.usage.completion_tokens)
    raw = (res.choices[0].message.content or "").strip()

    refined: dict[str, str] = {}
    label_set = set(labels)
    for line in raw.splitlines():
        if " | " not in line:
            continue
        label, brief = line.split(" | ", 1)
        # Strip common bullet/marker noise the LLM sometimes prefixes.
        label = label.strip().lstrip("-*").strip()
        brief = brief.strip()
        if label in label_set and brief:
            refined[label] = brief

    # Fill in any missing labels with the initial brief.
    for label in labels:
        if label not in refined and (initial_prompts or {}).get(label):
            refined[label] = initial_prompts[label]

    logger.info(f"refine_scene_prompts: refined {len(refined)}/{len(labels)} prompts via {model}")
    return refined


def _parse_scene_prompts(raw_block: str) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for line in raw_block.splitlines():
        stripped = line.strip()
        if not stripped or " | " not in stripped:
            continue
        label, brief = stripped.split(" | ", 1)
        label = label.strip()
        brief = brief.strip()
        if label and brief:
            prompts[label] = brief
    return prompts


def _parse_mega_output(raw: str, fallback_title: str) -> "ScriptOutput":
    sections: dict[str, str] = {}
    for m in _SECTION_RE.finditer(raw):
        sections[m.group(1)] = m.group(2).strip()

    if not sections:
        logger.warning("Could not parse mega-output sections — treating full response as long script")

    long_script     = sections.get("LONG_SCRIPT", raw).strip()
    shorts_script   = sections.get("SHORTS_SCRIPT", "").strip()
    seo_title       = sections.get("SEO_TITLE", fallback_title).strip()
    seo_description = sections.get("SEO_DESCRIPTION", "").strip()
    tags_raw        = sections.get("SEO_TAGS", "").strip()
    seo_tags        = [t.strip() for t in tags_raw.split(",") if t.strip()][:10]
    scene_prompts   = _parse_scene_prompts(sections.get("SCENE_PROMPTS", ""))
    thumbnail_prompt = sections.get("THUMBNAIL_PROMPT", "").strip()

    logger.info(
        f"Parsed: long={len(long_script)} chars  shorts={len(shorts_script)} chars  "
        f"title='{seo_title[:60]}'  tags={len(seo_tags)}  scene_prompts={len(scene_prompts)}"
    )
    return ScriptOutput(
        long_script     = long_script,
        shorts_script   = shorts_script,
        seo_title       = seo_title,
        seo_description = seo_description,
        seo_tags        = seo_tags,
        scene_prompts   = scene_prompts,
        thumbnail_prompt = thumbnail_prompt,
    )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def strip_scene_markers(script: str) -> str:
    """Remove [SCENE:label] tags and any non-spoken lines so TTS gets pure narration."""
    lines = []
    for line in script.splitlines():
        stripped = line.strip()
        # Remove [SCENE:label] marker lines
        if re.match(r"\[SCENE:[^\]]+\]$", stripped):
            continue
        # Remove stage directions: lines starting with '('
        if stripped.startswith("("):
            continue
        # Remove speaker labels: lines like 'Narrator:', 'Host:', 'Speaker:'
        cleaned = re.sub(r'^(?:Narrator|Host|Speaker|Voice|VO)\s*:\s*', '', stripped, flags=re.IGNORECASE)
        # Strip surrounding quotes that sometimes wrap the spoken line
        cleaned = re.sub(r'^"(.*)"$', r'\1', cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


# Back-compat alias
def strip_section_markers(script: str) -> str:
    return strip_scene_markers(script)


# ---------------------------------------------------------------------------
# SYMBOL_NAMES stub — kept so sync_service import doesn't break
# ---------------------------------------------------------------------------
SYMBOL_NAMES: dict[str, str] = {}
