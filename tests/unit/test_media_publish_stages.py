"""Media (7-11) and publish (12) stage tests: fakes only, no network or heavy deps."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_text_stages import _make_env, _responses, _run_all
from yt_video.media.clip import ClipVerifier
from yt_video.media.images import generate_scene_images as real_scene_images
from yt_video.memory._io import read_json, write_json_atomic
from yt_video.pipeline.runner import Pipeline
from yt_video.pipeline.stages import media, publish
from yt_video.pipeline.stages.media import MEDIA_STAGES
from yt_video.pipeline.stages.publish import ALL_STAGES, PublishStage
from yt_video.providers.image.chain import ImageChain
from yt_video.providers.image.fake import FakeImageProvider


class FakeYouTube:
    """Records every call; ``fail_shorts`` raises on the second upload."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.uploads: list[dict[str, Any]] = []
        self.shorts_error: BaseException | None = None

    def _rec(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def upload_video(self, **kw: Any) -> str | None:
        self._rec("upload_video", **kw)
        if kw["video_path"].name.endswith("_shorts.mp4") and self.shorts_error:
            raise self.shorts_error
        self.uploads.append(kw)
        return f"vid{len(self.uploads)}"

    def post_pinned_comment(self, video_id: str, text: str) -> None:
        self._rec("post_pinned_comment", video_id=video_id, text=text)

    def ensure_playlist(self, title: str, description: str = "") -> str | None:
        self._rec("ensure_playlist", title=title, description=description)
        return "PL1"

    def add_video_to_playlist(self, playlist_id: str, video_id: str) -> bool:
        self._rec("add_video_to_playlist", playlist_id=playlist_id, video_id=video_id)
        return True


class Calls:
    def __init__(self) -> None:
        self.voice: list[Path] = []
        self.transcribed: list[Path] = []
        self.video: list[dict[str, Any]] = []
        self.shorts: list[dict[str, Any]] = []
        self.x: list[dict[str, Any]] = []
        self.thumbs: list[dict[str, Any]] = []


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def ctx_after_text(tmp_path, settings, monkeypatch, calls):
    e = _make_env(
        tmp_path, settings, monkeypatch,
        _responses(refine_scene_prompts={"scene_prompts": [{"label": "s0", "prompt": "refined"}]}),
        packaging_require_approval=True,
    )
    ctx = e.ctx
    assert _run_all(ctx) == "done"
    ctx.no_upload = False
    ctx.youtube = FakeYouTube()
    ctx.images = ImageChain([FakeImageProvider()], tmp_path / "cache", sleep=lambda s: None)
    ctx.clip = ClipVerifier(False, 0.24, "unused")

    def fake_voice(text, out_path, settings, corrections, ledger=None, run_id=""):
        Path(out_path).write_bytes(b"mp3")
        calls.voice.append(Path(out_path))

    def fake_transcribe(voice_path, settings, ledger=None, run_id=""):
        calls.transcribed.append(Path(voice_path))
        return [{"word": "hello", "start": 0.0, "end": 0.5}, {"word": "world", "start": 0.5, "end": 1.0}]

    def fake_video(**kw):
        calls.video.append(kw)
        Path(kw["out_path"]).write_bytes(b"mp4")

    def fake_shorts(**kw):
        calls.shorts.append(kw)
        Path(kw["out_path"]).write_bytes(b"mp4")

    def fast_images(chain, verifier, labels, topic, angle, texts, overrides, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for label in labels:
            paths[label] = str(out_dir / f"{label}.png")
            Path(paths[label]).write_bytes(b"png")
        return paths

    monkeypatch.setattr(media, "generate_scene_images", fast_images)
    monkeypatch.setattr(media, "generate_voice", fake_voice)
    monkeypatch.setattr(media, "transcribe_words", fake_transcribe)
    monkeypatch.setattr(media, "create_video", fake_video)
    monkeypatch.setattr(media, "create_shorts_video", fake_shorts)
    monkeypatch.setattr(media, "_audio_duration", lambda p: 100.0)
    monkeypatch.setattr(
        publish, "post_video_update", lambda *a, **kw: calls.x.append(kw)
    )
    return ctx


def _run(ctx) -> str:
    return Pipeline([cls() for cls in ALL_STAGES]).run(ctx)


def _set_review(ctx, score: int, safety: str = "Yes") -> None:
    ctx.run.mark_done("review", {"score": score, "safety": safety, "notes": "n"})


def _pending(ctx, **fields: Any) -> dict:
    path = ctx.run.path("packaging_pending.json")
    data = read_json(path)
    data.update(fields)
    write_json_atomic(path, data, ensure_ascii=False)
    return data


def _approve(ctx, **fields: Any) -> None:
    _pending(ctx, approved=True, **fields)


# --------------------------------------------------------------------- media


def test_media_stages_produce_named_artifacts(ctx_after_text, calls, monkeypatch):
    ctx = ctx_after_text
    monkeypatch.setattr(media, "generate_scene_images", real_scene_images)  # real ImageChain
    assert Pipeline([cls() for cls in ALL_STAGES[:-1]]).run(ctx) == "done"
    slug = ctx.run.slug
    voice = ctx.run.get("voice")
    assert Path(voice["voice_path"]).name == f"{slug}_voice.mp3"
    assert Path(voice["shorts_voice_path"]).name == f"{slug}_shorts_voice.mp3"
    imgs = ctx.run.get("images")
    assert len(imgs["scene_images"]) == 30
    assert all(Path(p).parent.name == "images" for p in imgs["scene_images"].values())
    assert Path(imgs["thumbnail_path"]).name == "thumbnail.jpg"
    tr = ctx.run.get("transcribe")
    assert Path(tr["srt_path"]).name == f"{slug}.srt"
    assert Path(tr["shorts_srt_path"]).name == f"{slug}_shorts.srt"
    assert Path(tr["transcript_path"]).name == f"{slug}_transcript.json"
    assert Path(tr["shorts_transcript_path"]).name == f"{slug}_shorts_transcript.json"
    timeline = json.loads(ctx.run.path(f"{slug}_timeline.json").read_text(encoding="utf-8"))
    assert timeline == tr["timeline"] and len(timeline) == 30
    r = ctx.run.get("render")
    assert Path(r["video_path"]).name == f"{slug}.mp4"
    assert Path(r["shorts_path"]).name == f"{slug}_shorts.mp4"
    assert len(calls.shorts[0]["scene_images"]) == 6  # first 6 only
    assert list(calls.shorts[0]["scene_images"]) == list(imgs["scene_images"])[:6]
    assert calls.video[0]["settings"] is ctx.settings
    assert ctx.run.is_done("learn_tts") and ctx.run.get("learn_tts") == {}


def test_shorts_disabled_skips_shorts(ctx_after_text, calls):
    ctx = ctx_after_text
    ctx.settings.shorts_enabled = False
    _approve(ctx)
    assert _run(ctx) == "done"
    assert len(calls.voice) == 1 and calls.shorts == []
    assert ctx.run.get("voice")["shorts_voice_path"] is None
    assert ctx.run.get("render")["shorts_path"] is None
    assert [n for n in ctx.youtube.names() if n == "upload_video"] == ["upload_video"]
    assert ctx.run.get("publish")["shorts_video_id"] is None


def test_shorts_transcription_failure_is_non_fatal(ctx_after_text, monkeypatch, calls):
    ctx = ctx_after_text

    def flaky(voice_path, settings, ledger=None, run_id=""):
        if "shorts" in Path(voice_path).name:
            raise RuntimeError("whisper down")
        return [{"word": "a", "start": 0.0, "end": 0.4}]

    monkeypatch.setattr(media, "transcribe_words", flaky)
    Pipeline([cls() for cls in MEDIA_STAGES]).run(ctx)
    tr = ctx.run.get("transcribe")
    assert tr["shorts_transcript_path"] is None and tr["shorts_srt_path"] is None
    assert calls.shorts[0]["captions"] == []  # rendered anyway


def test_learn_tts_failure_is_non_fatal(ctx_after_text, monkeypatch):
    ctx = ctx_after_text

    def boom(*a, **k):
        raise RuntimeError("memory broke")

    monkeypatch.setattr(ctx.tts_memory, "analyze_and_update", boom)
    Pipeline([cls() for cls in MEDIA_STAGES]).run(ctx)
    assert ctx.run.is_done("learn_tts") and ctx.run.is_done("render")


def test_thumbnail_failure_yields_none(ctx_after_text, monkeypatch):
    ctx = ctx_after_text

    def boom(*a, **k):
        raise RuntimeError("no thumb")

    monkeypatch.setattr(media, "generate_thumbnail", boom)
    Pipeline([cls() for cls in MEDIA_STAGES]).run(ctx)
    assert ctx.run.get("images")["thumbnail_path"] is None


# ------------------------------------------------------------------- publish


def test_no_upload_skips_publish(ctx_after_text):
    ctx = ctx_after_text
    ctx.no_upload = True
    assert _run(ctx) == "done"
    assert ctx.run.get("publish") == {"skipped": True}
    assert ctx.youtube.calls == []


def test_gate_waits_when_public_and_unapproved(ctx_after_text):
    ctx = ctx_after_text
    assert _run(ctx) == "awaiting_approval"
    assert ctx.run.status == "awaiting_approval"
    assert ctx.youtube.calls == []
    assert not ctx.run.is_done("publish")
    assert ctx.run.get("publish") == {}


def test_gate_waits_when_pending_file_missing_or_corrupt(ctx_after_text):
    ctx = ctx_after_text
    path = ctx.run.path("packaging_pending.json")
    path.unlink()
    assert _run(ctx) == "awaiting_approval"
    path.write_text("{not json", encoding="utf-8")
    assert _run(ctx) == "awaiting_approval"
    assert ctx.youtube.calls == []


def test_rejected_forces_private(ctx_after_text):
    ctx = ctx_after_text
    _pending(ctx, rejected=True)
    assert _run(ctx) == "done"
    assert ctx.youtube.uploads[0]["privacy_status"] == "private"
    assert ctx.youtube.uploads[1]["privacy_status"] == "private"  # Shorts too
    assert "ensure_playlist" not in ctx.youtube.names()
    assert ctx.run.get("publish")["privacy"] == "private"


def test_private_skips_playlist_and_gate(ctx_after_text):
    ctx = ctx_after_text
    _set_review(ctx, 50)
    assert _run(ctx) == "done"  # no approval needed for a private upload
    assert ctx.youtube.uploads[0]["privacy_status"] == "private"
    assert "ensure_playlist" not in ctx.youtube.names()
    assert "post_pinned_comment" in ctx.youtube.names()


def test_unsafe_review_is_private(ctx_after_text):
    ctx = ctx_after_text
    _set_review(ctx, 95, "No")
    _run(ctx)
    assert ctx.run.get("publish")["privacy"] == "private"


def test_privacy_decision_error_forces_private(ctx_after_text, monkeypatch):
    ctx = ctx_after_text

    def boom(score, safety):
        raise RuntimeError("bad")

    monkeypatch.setattr(publish, "decide_privacy", boom)
    assert _run(ctx) == "done"  # private => no gate
    assert ctx.run.get("publish")["privacy"] == "private"


def test_approved_public_full_flow(ctx_after_text, calls):
    ctx = ctx_after_text
    _approve(ctx)
    assert _run(ctx) == "done"
    yt = ctx.youtube
    data = ctx.run.get("publish")
    assert data == {
        "video_id": "vid1", "shorts_video_id": "vid2",
        "privacy": "public", "category_id": "28",  # "AI chip boom" maps to Science & Tech
    }
    long_up, shorts_up = yt.uploads
    assert long_up["privacy_status"] == "public"
    assert long_up["category_id"] == "28" == shorts_up["category_id"]
    assert shorts_up["title"].endswith("#Shorts")
    assert shorts_up["thumbnail_path"] is None
    assert yt.names() == [
        "upload_video", "post_pinned_comment", "ensure_playlist",
        "add_video_to_playlist", "upload_video",
    ]
    assert len(calls.x) == 1 and calls.x[0]["video_id"] == "vid1"
    recorded = {v["video_id"]: v for v in read_json(ctx.analytics.memory_path)["videos"]}
    assert recorded["vid1"]["is_short"] is False and recorded["vid2"]["is_short"] is True
    assert recorded["vid1"]["privacy_status"] == "public"


def test_approved_changed_variant_regenerates_thumbnail(ctx_after_text, monkeypatch, calls):
    ctx = ctx_after_text
    regen_path = ctx.run.path("thumbnail.jpg")

    def fake_thumb(chain, title, topic, prompt_override, hook_text, out_path, **kw):
        calls.thumbs.append({"title": title, "hook": hook_text})
        return regen_path

    monkeypatch.setattr(publish, "generate_thumbnail", fake_thumb)
    _approve(ctx, chosen_index=1)  # variant v2: title "chips", hook ""
    _run(ctx)
    assert len(calls.thumbs) == 1
    assert calls.thumbs[0]["title"] == "chips"
    assert ctx.youtube.uploads[0]["title"] == "chips"


def test_approved_winner_unchanged_does_not_regenerate(ctx_after_text, monkeypatch, calls):
    ctx = ctx_after_text
    monkeypatch.setattr(
        publish, "generate_thumbnail", lambda *a, **k: calls.thumbs.append(1)
    )
    _approve(ctx)
    _run(ctx)
    assert calls.thumbs == []


def test_publish_resume_does_not_double_upload(ctx_after_text):
    ctx = ctx_after_text
    _approve(ctx)
    ctx.youtube.shorts_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        _run(ctx)
    assert not ctx.run.is_done("publish")
    assert ctx.run.get("publish")["video_id"] == "vid1"
    ctx.youtube.shorts_error = None
    assert _run(ctx) == "done"
    long_uploads = [
        kw for n, kw in ctx.youtube.calls
        if n == "upload_video" and not kw["video_path"].name.endswith("_shorts.mp4")
    ]
    assert len(long_uploads) == 1
    data = ctx.run.get("publish")
    assert data["video_id"] == "vid1" and data["shorts_video_id"] == "vid2"


def test_publish_resume_skips_post_steps_when_done(ctx_after_text):
    ctx = ctx_after_text
    _approve(ctx)
    ctx.youtube.shorts_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        _run(ctx)
    assert ctx.run.get("publish")["post_steps_done"] is True
    ctx.youtube.shorts_error = None
    _run(ctx)
    names = ctx.youtube.names()
    assert names.count("post_pinned_comment") == 1
    assert names.count("ensure_playlist") == 1
    assert names.count("add_video_to_playlist") == 1


def test_resume_before_post_steps_keeps_stored_privacy(ctx_after_text):
    """A partial with an id but no post_steps_done re-runs post steps only, never re-uploads."""
    ctx = ctx_after_text
    _approve(ctx)
    Pipeline([cls() for cls in ALL_STAGES[:-1]]).run(ctx)
    ctx.run.save_partial(
        "publish", {"video_id": "old", "privacy": "private", "category_id": "22"}
    )
    ctx.settings.shorts_enabled = False
    _run(ctx)
    assert [n for n in ctx.youtube.names() if n == "upload_video"] == []
    assert "ensure_playlist" not in ctx.youtube.names()  # stored privacy was private
    assert ctx.run.get("publish")["video_id"] == "old"


def test_shorts_upload_failure_is_non_fatal(ctx_after_text):
    ctx = ctx_after_text
    _approve(ctx)
    ctx.youtube.shorts_error = RuntimeError("quota")
    assert _run(ctx) == "done"
    data = ctx.run.get("publish")
    assert data["video_id"] == "vid1" and data["shorts_video_id"] is None


def test_post_steps_failures_are_non_fatal(ctx_after_text, monkeypatch):
    ctx = ctx_after_text
    _approve(ctx)

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(ctx.youtube, "post_pinned_comment", boom)
    monkeypatch.setattr(ctx.youtube, "ensure_playlist", boom)
    monkeypatch.setattr(publish, "post_video_update", boom)
    monkeypatch.setattr(ctx.analytics, "record_video", boom)
    assert _run(ctx) == "done"
    assert ctx.run.get("publish")["video_id"] == "vid1"


def test_long_upload_failure_is_fatal(ctx_after_text, monkeypatch):
    ctx = ctx_after_text
    _approve(ctx)

    def boom(**kw):
        raise RuntimeError("upload failed")

    monkeypatch.setattr(ctx.youtube, "upload_video", boom)
    with pytest.raises(RuntimeError):
        _run(ctx)
    assert not ctx.run.is_done("publish")


def test_publish_stage_name_and_stage_lists():
    assert PublishStage.name == "publish"
    assert [c.name for c in ALL_STAGES][-6:] == [
        "voice", "images", "transcribe", "learn_tts", "render", "publish",
    ]


def test_category_default_comes_from_settings(ctx_after_text, monkeypatch):
    ctx = ctx_after_text
    seen: list[str] = []

    def fake_resolve(topic, title="", default="22"):
        seen.append(default)
        return default

    monkeypatch.setattr(publish, "resolve_category_id", fake_resolve)
    ctx.settings.default_category = "24"
    _approve(ctx)
    _run(ctx)
    assert seen and set(seen) == {"24"}
    assert ctx.youtube.uploads[0]["category_id"] == "24"


def test_images_stage_records_provider_counts_and_warns_on_placeholders(ctx_after_text, caplog):
    ctx = ctx_after_text
    ctx.images.provider_counts = {"pollinations": 2, "placeholder": 3}
    ctx.images.disabled = {"pollinations": 402}
    with caplog.at_level("WARNING"):
        media.ImagesStage().run(ctx)
    data = ctx.run.get("images")
    assert data["provider_counts"]["placeholder"] == 3  # thumbnail adds one fake image
    assert data["disabled_providers"] == {"pollinations": 402}
    assert "3/6 scene images are placeholders (pollinations: HTTP 402)" in caplog.text


def _images_with_gaps(monkeypatch, drop_n: int):
    def gappy(chain, verifier, labels, topic, angle, texts, overrides, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for label in labels[drop_n:]:
            paths[label] = str(out_dir / f"{label}.png")
            Path(paths[label]).write_bytes(b"png")
        return paths

    monkeypatch.setattr(media, "generate_scene_images", gappy)


def test_images_stage_fails_when_over_20_percent_missing(ctx_after_text, monkeypatch):
    ctx = ctx_after_text
    ctx.images.disabled = {"pollinations": 402}
    _images_with_gaps(monkeypatch, 7)  # 7/30 = 23%
    with pytest.raises(RuntimeError, match=r"7/30 scene images missing: pollinations disabled: HTTP 402"):
        media.ImagesStage().run(ctx)
    assert not ctx.run.is_done("images")


def test_images_stage_continues_at_20_percent_missing(ctx_after_text, monkeypatch):
    ctx = ctx_after_text
    _images_with_gaps(monkeypatch, 6)  # 6/30 = 20%, not over the threshold
    media.ImagesStage().run(ctx)
    assert ctx.run.is_done("images")


# ------------------------------------------------------------ gate (I4)


@pytest.mark.parametrize("value", ["true", "false", 1, "yes"])
def test_non_boolean_approved_values_still_wait(ctx_after_text, value):
    ctx = ctx_after_text
    _pending(ctx, approved=value)
    assert _run(ctx) == "awaiting_approval"
    assert ctx.youtube.calls == []


def test_rejected_wins_over_approved(ctx_after_text):
    ctx = ctx_after_text
    _pending(ctx, approved=True, rejected=True)
    assert _run(ctx) == "done"
    assert [u["privacy_status"] for u in ctx.youtube.uploads] == ["private", "private"]
    assert ctx.run.get("publish")["privacy"] == "private"


def test_wait_then_cli_approve_then_rerun_uploads(ctx_after_text, settings, monkeypatch):
    from yt_video import cli

    ctx = ctx_after_text
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert _run(ctx) == "awaiting_approval"
    assert ctx.youtube.calls == []
    assert cli.main(["--approve-packaging"]) == cli.EXIT_OK
    assert read_json(ctx.run.path("packaging_pending.json"))["approved"] is True
    assert _run(ctx) == "done"
    assert ctx.youtube.uploads[0]["privacy_status"] == "public"
    assert ctx.run.get("publish")["video_id"] == "vid1"
