"""Media stages 7-11: voice, images, transcribe, learn-tts, render.

Ported from v1 ``workflows/long_video.py:291-455``. Artifacts live in the run
folder and are named ``<slug>_voice.mp3``, ``<slug>.mp4`` and so on.
"""
from __future__ import annotations

import json
from pathlib import Path

from yt_video import llm_tasks
from yt_video.domain.captions import load_caption_cues, words_to_srt
from yt_video.domain.packaging import scene_titles_from_script
from yt_video.domain.sync import align_scenes
from yt_video.domain.text import scene_labels, scene_texts, strip_scene_markers
from yt_video.logging import get_logger
from yt_video.media.images import generate_scene_images, generate_thumbnail
from yt_video.media.render import create_shorts_video, create_video
from yt_video.models import ScriptOutput
from yt_video.pipeline.context import RunContext
from yt_video.pipeline.runner import Stage, StageOutcome
from yt_video.pipeline.stages.text import PackagingData, RankData
from yt_video.providers.transcribe import transcribe_words
from yt_video.providers.tts import generate_voice

log = get_logger(__name__)

SHORTS_SCENE_LIMIT = 6


def _slug(ctx: RunContext) -> str:
    return ctx.run.slug or ctx.load("packaging", PackagingData).slug


def _shorts_wanted(ctx: RunContext, script: ScriptOutput) -> bool:
    return bool(ctx.settings.shorts_enabled and script.shorts_script.strip())


def _audio_duration(voice_path: Path) -> float:
    from moviepy import AudioFileClip

    clip = AudioFileClip(str(voice_path))
    try:
        return float(clip.duration)
    finally:
        clip.close()


def _write_transcript(words: list[dict], transcript_path: Path, srt_path: Path) -> None:
    transcript_path.write_text(json.dumps(words, indent=2), encoding="utf-8")
    words_to_srt(words, srt_path)


class VoiceStage:
    name = "voice"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        script = ctx.load("write_script", ScriptOutput)
        slug = _slug(ctx)
        corrections = ctx.tts_memory.pronunciation_corrections()
        voice_path = ctx.run.path(f"{slug}_voice.mp3")
        generate_voice(
            strip_scene_markers(script.long_script),
            voice_path, ctx.settings, corrections, ctx.ledger, ctx.run.id,
        )
        shorts_voice: Path | None = None
        if _shorts_wanted(ctx, script):
            shorts_voice = ctx.run.path(f"{slug}_shorts_voice.mp3")
            generate_voice(
                strip_scene_markers(script.shorts_script),
                shorts_voice, ctx.settings, corrections, ctx.ledger, ctx.run.id,
            )
        ctx.run.mark_done(
            self.name,
            {
                "voice_path": str(voice_path),
                "shorts_voice_path": str(shorts_voice) if shorts_voice else None,
            },
        )
        return None


class ImagesStage:
    name = "images"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        script = ctx.load("write_script", ScriptOutput)
        sel = ctx.load("rank_topics", RankData).selected
        pkg = ctx.load("packaging", PackagingData)
        labels = scene_labels(script.long_script)
        texts = scene_texts(script.long_script)
        refined = llm_tasks.refine_scene_prompts(
            ctx.router, sel.title, sel.angle, labels, texts, script.scene_prompt_map
        )
        scene_images = generate_scene_images(
            ctx.images, ctx.clip, labels, sel.title, sel.angle, texts, refined,
            ctx.run.path("images"),
        )
        thumbnail_path: Path | None
        try:
            thumbnail_path = generate_thumbnail(
                ctx.images, pkg.publish_title, sel.title, script.thumbnail_prompt,
                pkg.thumb_hook, ctx.run.path("thumbnail.jpg"),
                verifier=ctx.clip, text_enabled=ctx.settings.thumbnail_text_enabled,
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal
            log.warning("thumbnail generation failed (non-fatal): %s", exc)
            thumbnail_path = None
        counts = dict(ctx.images.provider_counts)
        disabled = dict(ctx.images.disabled)
        placeholders = counts.get("placeholder", 0)
        if placeholders:
            total = sum(counts.values())
            reasons = ", ".join(f"{p}: HTTP {s}" for p, s in disabled.items()) or "providers failed"
            log.warning("%d/%d scene images are placeholders (%s)", placeholders, total, reasons)
        ctx.run.mark_done(
            self.name,
            {
                "scene_images": {k: str(v) for k, v in scene_images.items()},
                "thumbnail_path": str(thumbnail_path) if thumbnail_path else None,
                "provider_counts": counts,
                "disabled_providers": disabled,
            },
        )
        return None


class TranscribeStage:
    name = "transcribe"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        script = ctx.load("write_script", ScriptOutput)
        slug = _slug(ctx)
        voice = ctx.run.get("voice")
        voice_path = Path(voice["voice_path"])
        transcript_path = ctx.run.path(f"{slug}_transcript.json")
        srt_path = ctx.run.path(f"{slug}.srt")
        timeline_path = ctx.run.path(f"{slug}_timeline.json")

        duration = _audio_duration(voice_path)
        log.info("audio duration: %.1fs", duration)
        words: list[dict] = []
        try:
            words = transcribe_words(voice_path, ctx.settings, ctx.ledger, ctx.run.id)
            _write_transcript(words, transcript_path, srt_path)
        except Exception as exc:  # noqa: BLE001 - v1 falls back to proportional timing
            log.warning("Whisper refinement skipped (%s); using proportional timing", exc)
            words = []
        timeline = align_scenes(words, script.long_script, duration)
        timeline_path.write_text(json.dumps(timeline, indent=2), encoding="utf-8")

        shorts_transcript: Path | None = None
        shorts_srt: Path | None = None
        shorts_voice = voice.get("shorts_voice_path")
        if shorts_voice and ctx.settings.shorts_enabled:
            t_path = ctx.run.path(f"{slug}_shorts_transcript.json")
            s_path = ctx.run.path(f"{slug}_shorts.srt")
            try:
                shorts_words = transcribe_words(
                    Path(shorts_voice), ctx.settings, ctx.ledger, ctx.run.id
                )
                _write_transcript(shorts_words, t_path, s_path)
                shorts_transcript, shorts_srt = t_path, s_path
            except Exception as exc:  # noqa: BLE001 - non-fatal
                log.warning("Shorts Whisper failed (non-fatal): %s", exc)
        ctx.run.mark_done(
            self.name,
            {
                "timeline": timeline,
                "transcript_path": str(transcript_path),
                "srt_path": str(srt_path),
                "shorts_transcript_path": str(shorts_transcript) if shorts_transcript else None,
                "shorts_srt_path": str(shorts_srt) if shorts_srt else None,
            },
        )
        return None


class LearnTTSStage:
    name = "learn_tts"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        try:
            script = ctx.load("write_script", ScriptOutput)
            ctx.tts_memory.analyze_and_update(
                Path(ctx.run.get("transcribe")["transcript_path"]), script.long_script
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal
            log.warning("TTS memory update failed (non-fatal): %s", exc)
        ctx.run.mark_done(self.name, {})
        return None


class RenderStage:
    name = "render"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        script = ctx.load("write_script", ScriptOutput)
        sel = ctx.load("rank_topics", RankData).selected
        slug = _slug(ctx)
        voice = ctx.run.get("voice")
        tr = ctx.run.get("transcribe")
        scene_images: dict[str, str] = ctx.run.get("images").get("scene_images", {})
        labels = scene_labels(script.long_script)
        titles = scene_titles_from_script(labels, scene_texts(script.long_script))

        video_path = ctx.run.path(f"{slug}.mp4")
        log.info("assembling long video")
        create_video(
            timeline=tr["timeline"],
            scene_images=scene_images,
            scene_labels=labels,
            scene_titles=titles,
            topic=sel.title,
            angle=sel.angle,
            voice_path=Path(voice["voice_path"]),
            out_path=video_path,
            captions=load_caption_cues(Path(tr["transcript_path"])),
            settings=ctx.settings,
        )

        shorts_path: Path | None = None
        if voice.get("shorts_voice_path") and ctx.settings.shorts_enabled:
            log.info("assembling Shorts video (9:16)")
            shorts_path = ctx.run.path(f"{slug}_shorts.mp4")
            shorts_captions = (
                load_caption_cues(Path(tr["shorts_transcript_path"]))
                if tr.get("shorts_transcript_path")
                else []
            )
            create_shorts_video(
                voice_path=Path(voice["shorts_voice_path"]),
                scene_images=dict(list(scene_images.items())[:SHORTS_SCENE_LIMIT]),
                captions=shorts_captions,
                out_path=shorts_path,
                settings=ctx.settings,
            )
        ctx.run.mark_done(
            self.name,
            {
                "video_path": str(video_path),
                "shorts_path": str(shorts_path) if shorts_path else None,
            },
        )
        return None


MEDIA_STAGES: list[type[Stage]] = [
    VoiceStage,
    ImagesStage,
    TranscribeStage,
    LearnTTSStage,
    RenderStage,
]
