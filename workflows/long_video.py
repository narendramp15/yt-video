"""
long_video.py — yt-video  (100% free, trend-driven pipeline)

Exact 5-phase flow from spec (yt-cost-effective-pipeline.html):

  Phase 1 — Trend Discovery
    pytrends + RSS feeds + yfinance news  →  raw signals
    AI filter + score (one batch call)    →  Top 3 ranked topics
    Deterministic novelty/velocity/niche re-rank (Phase B)

  Phase 2 — Script Generation  (one mega-prompt AI call)
    Top topic  →  Long script + [SCENE:] visual cues
               +  Shorts script (60 sec)
               +  SEO title, description, 10 tags
    Packaging A/B: 3 title/hook variants → heuristic pick (Phase B)

  Phase 3 — Media Assets  (all free, run in sequence)
    Edge TTS        →  long + shorts voice MP3
    Pollinations.ai →  scene images + thumbnail
    Whisper         →  captions / scene timestamps + SRT

  Phase 4 — Video Rendering  (local FFmpeg via MoviePy)
    long 16:9 (+ burned captions) + Shorts 9:16 (+ burned captions)

  Phase 5 — Review & Publish
    AI score gate (≥80 + safety Yes → public)
    YouTube Data API v3  →  upload long + Shorts
    Analytics logger     →  output/analytics_log.json (feedback loop)

Checkpoint: output/pipeline_state.json — re-run resumes from failed step.
Use --restart to force full re-run from scratch.

NOTE: yt_ai_factory/ handles the NIFTY 50 candlestick-chart pipeline.
      This pipeline is for general trending topics only — no stock data.
"""

import json
import os
import datetime
from config import config
from services.topic_service import fetch_trending_topics, filter_and_score_topics, get_content_angle
from services.script_service import generate_script, strip_scene_markers, ScriptOutput, refine_scene_prompts
from services.tts_service import generate_voice
from services.video_service import create_video, create_shorts_video
from services.youtube_service import upload_video, post_pinned_comment
from services.review_service import review_script, extract_score, extract_safety, log_review
from services.sync_service import transcribe_and_sync, load_caption_cues, words_to_srt, transcribe_words
from services.checkpoint_service import is_done, mark_done, get_step_data
from services.image_service import generate_scene_images_for_scenes, generate_thumbnail
from services.tts_memory_service import analyze_and_update
from services.yt_analytics_memory_service import record_video
from services.x_service import post_video_update
from services.cost_service import print_run_summary
from services.packaging_service import (
    build_publish_description,
    decide_privacy,
    derive_thumbnail_hook,
    generate_packaging_variants,
    pick_best_packaging,
    resolve_category_id,
    scene_titles_from_script,
)
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
    logger.info("  Phase 1 — Trend Discovery  : pytrends + RSS + yfinance → AI filter + re-rank")
    logger.info("  Phase 2 — Script           : mega-prompt (long + shorts + SEO) + packaging A/B")
    logger.info("  Phase 3 — Assets           : Edge TTS + Pollinations + Whisper/SRT")
    logger.info("  Phase 4 — Render           : long 16:9 + Shorts 9:16 (captions burned)")
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

    # ── Phase 2b: Packaging A/B (title + thumb hook) ──────────────────────
    if is_done("packaging_ab"):
        pkg_data = get_step_data("packaging_ab")
        packaging_variants = pkg_data.get("variants", [])
        packaging_winner = pkg_data.get("winner") or {}
        logger.info(
            f"Packaging (from checkpoint): {packaging_winner.get('variant_id')} "
            f"score={packaging_winner.get('score')}"
        )
    else:
        packaging_variants = generate_packaging_variants(
            topic=topic,
            base_title=script_out.seo_title or topic,
            angle=angle,
        )
        packaging_winner = pick_best_packaging(packaging_variants)
        mark_done(
            "packaging_ab",
            variants=packaging_variants,
            winner=packaging_winner,
        )
        logger.info(
            f"Packaging winner: {packaging_winner.get('variant_id')} "
            f"'{packaging_winner.get('title')}' hook='{packaging_winner.get('hook')}'"
        )

    publish_title = (packaging_winner.get("title") or script_out.seo_title or topic).strip()
    thumb_hook = (packaging_winner.get("hook") or derive_thumbnail_hook(publish_title)).strip()
    packaging_variant_id = packaging_winner.get("variant_id") or ""

    # Derive slug-based output paths from the chosen publish title
    _slug            = _make_slug(publish_title or topic)
    _voice_path_def  = f"output/{_slug}_voice.mp3"
    _shorts_voice    = f"output/{_slug}_shorts_voice.mp3"
    _video_path      = f"output/{_slug}.mp4"
    _shorts_path     = f"output/{_slug}_shorts.mp4"
    _transcript_path = f"output/{_slug}_transcript.json"
    _shorts_transcript = f"output/{_slug}_shorts_transcript.json"
    _timeline_path   = f"output/{_slug}_timeline.json"
    _srt_path        = f"output/{_slug}.srt"
    _shorts_srt      = f"output/{_slug}_shorts.srt"
    logger.info(f"Output slug: {_slug}")

    # ── Phase 2 (self-review): AI safety + quality gate ───────────────────
    if is_done("review_script"):
        review_data = get_step_data("review_script")
        score = review_data["score"]
        safety = review_data.get("safety") or "No"
        logger.info(f"Review score (from checkpoint): {score}  safety={safety}")
    else:
        review = review_script(script_out.long_script)
        score  = extract_score(review)
        safety = extract_safety(review)
        log_review(topic, score, review)
        mark_done("review_script", score=score, safety=safety)
        logger.info(f"Review score: {score}/100  safety={safety}")

    # ── Phase 3a: Edge TTS voiceover (long) ────────────────────────────────
    if is_done("generate_voice"):
        voice_path = get_step_data("generate_voice").get("voice_path", _voice_path_def)
        logger.info(f"Voice (from checkpoint): {voice_path}")
    else:
        narration  = strip_scene_markers(script_out.long_script)
        voice_path = generate_voice(narration, out_path=_voice_path_def)
        mark_done("generate_voice", voice_path=voice_path)

    # ── Phase 3a2: Shorts TTS ──────────────────────────────────────────────
    shorts_voice_path = None
    if config.SHORTS_ENABLED and (script_out.shorts_script or "").strip():
        if is_done("generate_shorts_voice"):
            shorts_voice_path = get_step_data("generate_shorts_voice").get(
                "voice_path", _shorts_voice
            )
            logger.info(f"Shorts voice (from checkpoint): {shorts_voice_path}")
        else:
            shorts_narration = strip_scene_markers(script_out.shorts_script)
            shorts_voice_path = generate_voice(shorts_narration, out_path=_shorts_voice)
            mark_done("generate_shorts_voice", voice_path=shorts_voice_path)

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
                title           = publish_title,
                topic           = topic,
                prompt_override = script_out.thumbnail_prompt,
                hook_text       = thumb_hook,
            )
        except Exception as exc:
            logger.warning(f"Thumbnail generation failed (non-fatal): {exc}")
            thumbnail_path = None
        mark_done(
            "generate_images",
            scene_images=scene_images,
            thumbnail_path=thumbnail_path,
            topic=topic,
            thumb_hook=thumb_hook,
        )

    # ── Phase 3c: Whisper captions + scene timestamps (long) ───────────────
    if is_done("sync_scenes"):
        with open(_timeline_path) as f:
            timeline = json.load(f)
        logger.info(f"Scene timeline (from checkpoint): {timeline}")
    else:
        timeline = transcribe_and_sync(
            voice_path, script_out.long_script,
            transcript_path=_transcript_path,
            timeline_path=_timeline_path,
            srt_path=_srt_path,
        )
        mark_done("sync_scenes", srt_path=_srt_path)

    long_captions = load_caption_cues(_transcript_path)

    # ── Phase 3c2: Shorts Whisper / captions ───────────────────────────────
    shorts_captions: list[dict] = []
    if shorts_voice_path and config.SHORTS_ENABLED:
        if is_done("sync_shorts"):
            shorts_captions = load_caption_cues(
                get_step_data("sync_shorts").get("transcript_path", _shorts_transcript)
            )
            logger.info(f"Shorts captions (from checkpoint): {len(shorts_captions)} cues")
        else:
            try:
                words = transcribe_words(shorts_voice_path)
                os.makedirs("output", exist_ok=True)
                with open(_shorts_transcript, "w", encoding="utf-8") as f:
                    json.dump(words, f, indent=2)
                words_to_srt(words, _shorts_srt)
                shorts_captions = load_caption_cues(_shorts_transcript)
                mark_done(
                    "sync_shorts",
                    transcript_path=_shorts_transcript,
                    srt_path=_shorts_srt,
                )
            except Exception as exc:
                logger.warning(f"Shorts Whisper failed (non-fatal): {exc}")
                mark_done("sync_shorts", transcript_path=_shorts_transcript, error=str(exc))

    # ── TTS learning: analyze this run and update memory for next run ──────
    try:
        analyze_and_update(
            transcript_path = _transcript_path,
            script          = script_out.long_script,
        )
    except Exception as exc:
        logger.warning(f"TTS memory update failed (non-fatal): {exc}")

    # ── Phase 4: Render long video ─────────────────────────────────────────
    scene_label_list = _scene_labels_from_script(script_out.long_script)
    scene_text_map = _scene_texts_from_script(script_out.long_script)
    scene_title_map = scene_titles_from_script(scene_label_list, scene_text_map)

    if not is_done("create_video"):
        logger.info("Assembling long video locally (MoviePy + FFmpeg)...")
        create_video(
            timeline     = timeline,
            ticker_items = None,
            stat_cards   = None,
            scene_images = scene_images,
            scene_labels = scene_label_list,
            scene_titles = scene_title_map,
            topic        = topic,
            angle        = angle,
            voice_path   = voice_path,
            out_path     = _video_path,
            captions     = long_captions,
        )
        mark_done("create_video", video_path=_video_path)

    # ── Phase 4b: Render Shorts ────────────────────────────────────────────
    if shorts_voice_path and config.SHORTS_ENABLED and not is_done("create_shorts"):
        logger.info("Assembling Shorts video (9:16)...")
        # Prefer a short subset of scenes for vertical pacing
        short_images = dict(list((scene_images or {}).items())[:6])
        create_shorts_video(
            voice_path   = shorts_voice_path,
            scene_images = short_images,
            captions     = shorts_captions,
            out_path     = _shorts_path,
        )
        mark_done("create_shorts", video_path=_shorts_path)
    elif is_done("create_shorts"):
        logger.info(
            f"Shorts (from checkpoint): "
            f"{get_step_data('create_shorts').get('video_path', _shorts_path)}"
        )

    # ── Phase 5: Upload long video ─────────────────────────────────────────
    video_id = None
    privacy = decide_privacy(score, safety)
    if no_upload:
        logger.info("Skipping upload (--no-upload flag set) — video saved locally only")
    elif is_done("upload_video"):
        logger.info("Video already uploaded — skipping")
        video_id = get_step_data("upload_video").get("video_id")
        privacy = get_step_data("upload_video").get("privacy", privacy)
    else:
        logger.info(
            f"Publishing: {privacy} (score={score}, safety={safety}, "
            f"threshold=80 + Safe to publish? Yes)"
        )

        tags_str = ", ".join(script_out.seo_tags) if script_out.seo_tags else ""
        base_description = (
            script_out.seo_description
            or (
                f"{topic}\n\n"
                "Educational content. Subscribe for daily trending topic videos.\n\n"
                f"Tags: {tags_str}"
            )
        )
        description = build_publish_description(
            base_description,
            timeline=timeline,
            scene_titles=scene_title_map,
        )
        category_id = resolve_category_id(
            topic=topic,
            title=publish_title,
        )

        video_id = upload_video(
            title          = publish_title,
            description    = description,
            privacy_status = privacy,
            thumbnail_path = thumbnail_path,
            tags           = script_out.seo_tags or [],
            video_path     = _video_path,
            category_id    = category_id,
        )
        mark_done("upload_video", privacy=privacy, video_id=video_id, category_id=category_id)

        if video_id:
            comment = (
                f"Today's topic: {topic}. "
                f"{angle + ' — ' if angle else ''}"
                f"Let us know your thoughts in the comments! 👇"
            )
            post_pinned_comment(video_id, comment)
            post_video_update(
                title=publish_title,
                video_id=video_id,
                topic=topic,
                angle=angle,
                privacy_status=privacy,
                pipeline="yt_video",
                hashtags="#Trending #India #Explained #YouTube",
            )

    # ── Phase 5b: Upload Shorts ────────────────────────────────────────────
    shorts_video_id = None
    if (
        config.SHORTS_ENABLED
        and not no_upload
        and os.path.exists(_shorts_path)
    ):
        if is_done("upload_shorts"):
            shorts_video_id = get_step_data("upload_shorts").get("video_id")
            logger.info(f"Shorts already uploaded: {shorts_video_id}")
        else:
            shorts_title = publish_title
            if "#shorts" not in shorts_title.lower():
                shorts_title = f"{shorts_title[:60].rstrip()} #Shorts"
            shorts_desc = (
                f"{topic}\n\n#Shorts #Trending #India\n\n"
                f"{(script_out.seo_description or '')[:200]}"
            )
            shorts_tags = list(script_out.seo_tags or []) + ["Shorts", "trending"]
            try:
                shorts_video_id = upload_video(
                    title          = shorts_title,
                    description    = shorts_desc,
                    privacy_status = privacy,
                    thumbnail_path = None,
                    tags           = shorts_tags,
                    video_path     = _shorts_path,
                    category_id    = resolve_category_id(topic=topic, title=publish_title),
                )
                mark_done(
                    "upload_shorts",
                    video_id=shorts_video_id,
                    privacy=privacy,
                )
            except Exception as exc:
                logger.warning(f"Shorts upload failed (non-fatal): {exc}")

    # ── Record uploads for 48h analytics fetch ─────────────────────────────
    if video_id:
        try:
            record_video(
                video_id       = video_id,
                topic          = topic,
                angle          = angle,
                seo_title      = publish_title,
                privacy_status = privacy,
                title          = publish_title,
                thumb_hook     = thumb_hook,
                packaging_variant_id = packaging_variant_id,
                is_short       = False,
            )
        except Exception as exc:
            logger.warning(f"Analytics record failed (non-fatal): {exc}")

    if shorts_video_id:
        try:
            record_video(
                video_id       = shorts_video_id,
                topic          = topic,
                angle          = angle,
                seo_title      = f"{publish_title} #Shorts",
                privacy_status = privacy,
                title          = f"{publish_title} #Shorts",
                thumb_hook     = thumb_hook,
                packaging_variant_id = packaging_variant_id,
                is_short       = True,
            )
        except Exception as exc:
            logger.warning(f"Shorts analytics record failed (non-fatal): {exc}")

    # ── Phase 5: Analytics logger (feedback loop) ─────────────────────────
    _log_analytics(
        video_id  = video_id,
        topic     = topic,
        seo_title = publish_title,
        privacy   = privacy,
    )

    logger.info("=== yt-video pipeline complete ✓ ===")
    print_run_summary("yt_video")
