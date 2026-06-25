"""
long_video.py — yt-video  (100% free, trend-driven pipeline)

Exact 5-phase flow from spec (yt-cost-effective-pipeline.html):

  Phase 1 — Trend Discovery
    pytrends + RSS feeds + yfinance news  →  raw signals
    AI filter + score (one batch call)    →  Top 3 ranked topics

  Phase 2 — Script Generation  (one mega-prompt AI call)
    Top topic  →  Long script + [SCENE:] visual cues
               +  Shorts script (60 sec)
               +  SEO title, description, 10 tags

  Phase 3 — Media Assets  (all free, run in sequence)
    Edge TTS        →  voice MP3
    Pollinations.ai →  scene images + thumbnail
    Whisper         →  captions / scene timestamps

  Phase 4 — Video Rendering  (local FFmpeg via MoviePy)
    scene images + voice + bg music  →  final_video.mp4  (16:9)

  Phase 5 — Review & Publish
    AI score gate (≥80 → public, else private)
    YouTube Data API v3  →  upload with full SEO metadata
    Analytics logger     →  output/analytics_log.json (feedback loop)

Checkpoint: output/pipeline_state.json — re-run resumes from failed step.
Use --restart to force full re-run from scratch.

NOTE: yt_ai_factory/ handles the NIFTY 50 candlestick-chart pipeline.
      This pipeline is for general trending topics only — no stock data.
"""

import json
import os
import datetime
from services.topic_service import fetch_trending_topics, filter_and_score_topics, get_content_angle
from services.script_service import generate_script, strip_scene_markers, ScriptOutput, refine_scene_prompts
from services.tts_service import generate_voice
from services.video_service import create_video
from services.youtube_service import upload_video, post_pinned_comment
from services.review_service import review_script, extract_score, log_review
from services.sync_service import transcribe_and_sync
from services.checkpoint_service import is_done, mark_done, get_step_data
from services.image_service import generate_scene_images_for_scenes, generate_thumbnail
from services.tts_memory_service import analyze_and_update
from services.yt_analytics_memory_service import record_video
from services.x_service import post_video_update
from services.cost_service import print_run_summary
from logger import logger


# ---------------------------------------------------------------------------
# Scene image labels helper
# ---------------------------------------------------------------------------
def _make_slug(title: str) -> str:
    """Convert a title to an SEO-friendly filename slug with YYYYMMDD date suffix."""
    import re
    today = datetime.date.today().strftime("%Y%m%d")
    slug  = title.lower()
    slug  = re.sub(r"[^\w\s]", "", slug)   # strip punctuation
    slug  = re.sub(r"[\s_]+", "_", slug)   # whitespace → underscore
    slug  = slug.strip("_")[:60]           # cap length before date
    return f"{slug}_{today}"


def _scene_labels_from_script(script: str) -> list[str]:
    """Extract all [SCENE:label] labels from the long script."""
    import re
    return re.findall(r"\[SCENE:([^\]]+)\]", script)


def _scene_texts_from_script(script: str) -> dict[str, str]:
    """Map each [SCENE:label] to the narration text that follows it."""
    import re

    matches = list(re.finditer(r"\[SCENE:([^\]]+)\]", script))
    scene_texts: dict[str, str] = {}

    for index, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(script)
        scene_texts[label] = script[start:end].strip()

    return scene_texts


# ---------------------------------------------------------------------------
# Analytics logger (Phase 5 feedback loop)
# ---------------------------------------------------------------------------
def _log_analytics(video_id: str | None, topic: str, seo_title: str, privacy: str) -> None:
    """Save minimal publish record. Extend to fetch views/CTR after 48h."""
    record = {
        "date":       datetime.date.today().isoformat(),
        "video_id":   video_id,
        "topic":      topic,
        "seo_title":  seo_title,
        "privacy":    privacy,
        "views_48h":  None,   # TODO: fetch via YouTube Analytics API after 48h
        "ctr_48h":    None,
    }
    log_path = "output/analytics_log.json"
    history: list[dict] = []
    if os.path.exists(log_path):
        try:
            with open(log_path) as f:
                history = json.load(f)
        except Exception:
            pass
    history.append(record)
    os.makedirs("output", exist_ok=True)
    with open(log_path, "w") as f:
        json.dump(history, f, indent=2)
    logger.info(f"Analytics record saved → {log_path}  (video_id={video_id})")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_long_video(no_upload: bool = False) -> None:
    logger.info("=== yt-video FREE pipeline started ===")
    logger.info("  Phase 1 — Trend Discovery  : pytrends + RSS + yfinance → AI filter")
    logger.info("  Phase 2 — Script           : mega-prompt (long + shorts + SEO)")
    logger.info("  Phase 3 — Assets           : Edge TTS + Pollinations + Whisper")
    logger.info("  Phase 4 — Render           : local MoviePy + FFmpeg")
    logger.info("  Phase 5 — Publish          : YouTube API + analytics logger")

    # ── Phase 1 Step 1: Raw trend signals ──────────────────────────────────
    if is_done("fetch_signals"):
        raw_signals = get_step_data("fetch_signals").get("raw_signals", [])
        logger.info(f"Raw signals (from checkpoint): {len(raw_signals)} topics")
    else:
        logger.info("Fetching trend signals (pytrends + RSS + yfinance)...")
        raw_signals = fetch_trending_topics()
        mark_done("fetch_signals", raw_signals=raw_signals)
        logger.info(f"Raw signals fetched: {len(raw_signals)}")

    # ── Phase 1 Step 2: AI filter + score → Top 3 topics ──────────────────
    if is_done("filter_topics"):
        ranked_topics = get_step_data("filter_topics").get("ranked_topics", [])
        logger.info(f"Ranked topics (from checkpoint): {[t['title'] for t in ranked_topics]}")
    else:
        logger.info("AI filtering and scoring topics...")
        ranked_topics = filter_and_score_topics(raw_signals, top_n=3)
        mark_done("filter_topics", ranked_topics=ranked_topics)
        logger.info("Top 3 topics after AI filter:")
        for i, t in enumerate(ranked_topics, 1):
            logger.info(f"  {i}. [{t.get('score', '?')}] {t['title']}  — {t.get('angle', '')}")

    # Pick the #1 topic
    top = ranked_topics[0] if ranked_topics else {"title": "Today's Trending Topic", "angle": ""}
    topic = top["title"]
    angle = top.get("angle", "") or get_content_angle(top["title"])
    logger.info(f"Selected topic: '{topic}'  angle: '{angle}'")

    # ── Phase 2: Mega-script (long + shorts + SEO) — ONE AI call ──────────
    if is_done("generate_script"):
        logger.info("Loading script output from checkpoint...")
        script_data  = get_step_data("generate_script")
        script_out   = ScriptOutput.from_dict(script_data)
    else:
        # Pass remaining ranked topics as supporting trend signals
        supporting = [t["title"] for t in ranked_topics[1:]] + raw_signals[:5]
        script_out = generate_script(
            topic         = topic,
            trend_signals = supporting,
            angle         = angle,
        )
        mark_done("generate_script", **script_out.to_dict())

    logger.info(f"Long script: {len(script_out.long_script):,} chars")
    logger.info(f"Shorts:      {len(script_out.shorts_script):,} chars")
    logger.info(f"SEO title:   {script_out.seo_title}")
    logger.info(f"Tags:        {script_out.seo_tags}")
    # Derive slug-based output paths from the SEO title
    _slug           = _make_slug(script_out.seo_title or topic)
    _voice_path_def = f"output/{_slug}_voice.mp3"
    _video_path     = f"output/{_slug}.mp4"
    _transcript_path = f"output/{_slug}_transcript.json"
    _timeline_path  = f"output/{_slug}_timeline.json"
    logger.info(f"Output slug: {_slug}")
    # ── Phase 2 (self-review): AI safety + quality gate ───────────────────
    if is_done("review_script"):
        score = get_step_data("review_script")["score"]
        logger.info(f"Review score (from checkpoint): {score}")
    else:
        review = review_script(script_out.long_script)
        score  = extract_score(review)
        log_review(topic, score, review)
        mark_done("review_script", score=score)
        logger.info(f"Review score: {score}/100")

    # ── Phase 3a: Edge TTS voiceover ───────────────────────────────────────
    if is_done("generate_voice"):
        voice_path = get_step_data("generate_voice").get("voice_path", _voice_path_def)
        logger.info(f"Voice (from checkpoint): {voice_path}")
    else:
        narration  = strip_scene_markers(script_out.long_script)
        voice_path = generate_voice(narration, out_path=_voice_path_def)
        mark_done("generate_voice", voice_path=voice_path)

    # ── Phase 3b: Pollinations scene images + thumbnail ───────────────────
    # Always check if topic has changed; if so, regenerate fresh images
    prev_topic = get_step_data("generate_images").get("topic", "")
    images_outdated = prev_topic and prev_topic != topic

    if is_done("generate_images") and not images_outdated:
        scene_images   = get_step_data("generate_images").get("scene_images", {})
        thumbnail_path = get_step_data("generate_images").get("thumbnail_path")
        logger.info(f"Scene images (from checkpoint): {list(scene_images.keys())}")
    else:
        if images_outdated:
            logger.info(f"Topic changed ('{prev_topic}' → '{topic}') — regenerating images")

        labels = _scene_labels_from_script(script_out.long_script)
        scene_texts = _scene_texts_from_script(script_out.long_script)
        logger.info(f"Generating {len(labels)} scene images via Pollinations.ai...")

        # Note: image_service uses a content-addressed cache keyed on
        # (prompt, seed, model, dims). Different prompts → cache miss → fresh
        # image, so we no longer need to delete old files preemptively.

        # Second-pass LLM call to tighten scene prompts before image generation.
        # The mega-script LLM treats SCENE_PROMPTS as an afterthought; a focused
        # rewrite materially improves diffusion-model output quality.
        try:
            refined_prompts = refine_scene_prompts(
                topic           = topic,
                angle           = angle,
                labels          = labels,
                scene_texts     = scene_texts,
                initial_prompts = script_out.scene_prompts,
            )
        except Exception as exc:
            logger.warning(f"Scene prompt refine failed (non-fatal): {exc}")
            refined_prompts = script_out.scene_prompts

        scene_images = generate_scene_images_for_scenes(
            labels                 = labels,
            topic                  = topic,
            angle                  = angle,
            scene_texts            = scene_texts,
            scene_prompt_overrides = refined_prompts,
        )
        try:
            thumbnail_path = generate_thumbnail(
                title           = script_out.seo_title or topic,
                topic           = topic,
                prompt_override = script_out.thumbnail_prompt,
            )
        except Exception as exc:
            logger.warning(f"Thumbnail generation failed (non-fatal): {exc}")
            thumbnail_path = None
        mark_done("generate_images", scene_images=scene_images, thumbnail_path=thumbnail_path, topic=topic)

    # ── Phase 3c: Whisper captions + scene timestamps ─────────────────────
    if is_done("sync_scenes"):
        with open(_timeline_path) as f:
            timeline = json.load(f)
        logger.info(f"Scene timeline (from checkpoint): {timeline}")
    else:
        timeline = transcribe_and_sync(
            voice_path, script_out.long_script,
            transcript_path=_transcript_path,
            timeline_path=_timeline_path,
        )
        mark_done("sync_scenes")

    # ── TTS learning: analyze this run and update memory for next run ──────
    try:
        analyze_and_update(
            transcript_path = _transcript_path,
            script          = script_out.long_script,
        )
    except Exception as exc:
        logger.warning(f"TTS memory update failed (non-fatal): {exc}")

    # ── Phase 4: Render video (local MoviePy + FFmpeg) ────────────────────
    if not is_done("create_video"):
        logger.info("Assembling video locally (MoviePy + FFmpeg)...")
        scene_label_list = _scene_labels_from_script(script_out.long_script)
        create_video(
            timeline     = timeline,
            ticker_items = [],
            stat_cards   = [],
            scene_images = scene_images,
            scene_labels = scene_label_list,
            topic        = topic,
            angle        = angle,
            voice_path   = voice_path,
            out_path     = _video_path,
        )
        mark_done("create_video", video_path=_video_path)

    # ── Phase 5: Upload to YouTube ────────────────────────────────────────
    video_id = None
    if no_upload:
        logger.info("Skipping upload (--no-upload flag set) — video saved locally only")
    elif is_done("upload_video"):
        logger.info("Video already uploaded — skipping")
        video_id = get_step_data("upload_video").get("video_id")
    else:
        privacy = "public" if score >= 80 else "private"
        logger.info(f"Publishing: {privacy} (score={score}, threshold=80)")

        tags_str = ", ".join(script_out.seo_tags) if script_out.seo_tags else ""
        description = (
            script_out.seo_description
            or (
                f"{topic}\n\n"
                "Educational content. Subscribe for daily trending topic videos.\n\n"
                f"Tags: {tags_str}"
            )
        )

        video_id = upload_video(
            title          = script_out.seo_title or topic,
            description    = description,
            privacy_status = privacy,
            thumbnail_path = thumbnail_path,
            tags           = script_out.seo_tags or [],
            video_path     = _video_path,
        )
        mark_done("upload_video", privacy=privacy, video_id=video_id)

        # Post a pinned comment with the topic + angle
        if video_id:
            comment = (
                f"Today's topic: {topic}. "
                f"{angle + ' — ' if angle else ''}"
                f"Let us know your thoughts in the comments! 👇"
            )
            post_pinned_comment(video_id, comment)
            post_video_update(
                title=script_out.seo_title or topic,
                video_id=video_id,
                topic=topic,
                angle=angle,
                privacy_status=privacy,
                pipeline="yt_video",
                hashtags="#StockMarket #Trading #Investing #Finance #NSE #NIFTY50",
            )

    # ── Record upload for 48h analytics fetch ─────────────────────────────
    if video_id:
        try:
            record_video(
                video_id       = video_id,
                topic          = topic,
                angle          = angle,
                seo_title      = script_out.seo_title or topic,
                privacy_status = "public" if score >= 80 else "private",
            )
        except Exception as exc:
            logger.warning(f"Analytics record failed (non-fatal): {exc}")

    # ── Phase 5: Analytics logger (feedback loop) ─────────────────────────
    _log_analytics(
        video_id  = video_id,
        topic     = topic,
        seo_title = script_out.seo_title or topic,
        privacy   = "public" if score >= 80 else "private",
    )

    logger.info("=== yt-video pipeline complete ✓ ===")
    print_run_summary("yt_video")
