"""Stage 12: publish. Ported from v1 ``workflows/long_video.py:457-670``.

Safety rules (this is the highest-risk code in the pipeline):

* Public requires score >= 80 AND safety == "Yes"; any doubt or error -> private.
* With ``packaging_require_approval`` a would-be-public upload waits for an
  explicit approval in ``packaging_pending.json``; a missing, unreadable or
  ambiguous file counts as unapproved. Rejected forces private.
* Every upload id is persisted with ``save_partial`` before any other side
  effect so a crash and re-run never uploads the same video twice.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from yt_video.domain.packaging import scene_titles_from_script
from yt_video.domain.publish import (
    build_publish_description,
    build_up_next_blurb,
    decide_privacy,
    resolve_category_id,
    shorts_title,
)
from yt_video.domain.text import scene_labels, scene_texts
from yt_video.integrations.x import post_video_update
from yt_video.logging import get_logger
from yt_video.media.images import generate_thumbnail
from yt_video.memory._io import read_json
from yt_video.models import Review, ScriptOutput
from yt_video.pipeline.context import RunContext
from yt_video.pipeline.runner import Stage, StageOutcome
from yt_video.pipeline.stages.media import MEDIA_STAGES
from yt_video.pipeline.stages.text import (
    PENDING_FILE,
    TEXT_STAGES,
    PackagingData,
    PackagingStage,
    RankData,
)

log = get_logger(__name__)

X_HASHTAGS = "#Trending #India #Explained #YouTube"


def _read_pending(ctx: RunContext) -> dict[str, Any]:
    """The pending file as a dict; ``{}`` (= unapproved) if missing or unreadable."""
    try:
        data = read_json(ctx.run.path(PENDING_FILE))
    except Exception as exc:  # noqa: BLE001
        log.warning("packaging pending file unreadable (%s); treating as unapproved", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _chosen_variant(pending: dict[str, Any], winner: dict[str, Any]) -> dict[str, Any]:
    """``variants[chosen_index]`` if valid, else the winner."""
    idx = pending.get("chosen_index")
    variants = pending.get("variants")
    if isinstance(idx, int) and not isinstance(idx, bool) and isinstance(variants, list):
        if 0 <= idx < len(variants) and isinstance(variants[idx], dict):
            return variants[idx]
        log.warning("chosen_index %r out of range; using winner", idx)
    pending_winner = pending.get("winner")
    return pending_winner if isinstance(pending_winner, dict) else winner


def _safe_privacy(review: Review) -> str:
    try:
        return decide_privacy(review.score, review.safety)
    except Exception as exc:  # noqa: BLE001 - when in doubt, private
        log.warning("privacy decision failed (%s); forcing private", exc)
        return "private"


class PublishStage:
    name = "publish"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        if ctx.no_upload:
            log.info("skipping upload (--no-upload) - video saved locally only")
            ctx.run.mark_done(self.name, {"skipped": True})
            return None
        if ctx.youtube is None:
            raise RuntimeError("publish requires a YouTube client (none configured)")

        s = ctx.settings
        yt = ctx.youtube
        script = ctx.load("write_script", ScriptOutput)
        review = ctx.load("review", Review)
        sel = ctx.load("rank_topics", RankData).selected
        pkg = ctx.load("packaging", PackagingData)
        topic, angle = sel.title, sel.angle
        fmt = ctx.run.get("pick_format")
        format_id = fmt.get("format_id", "")
        timeline = ctx.run.get("transcribe").get("timeline", {})
        render = ctx.run.get("render")
        video_path = Path(render["video_path"])
        shorts_path = Path(render["shorts_path"]) if render.get("shorts_path") else None
        thumbnail_path: str | None = ctx.run.get("images").get("thumbnail_path")

        partial: dict[str, Any] = dict(ctx.run.get(self.name))
        video_id: str | None = partial.get("video_id")
        required = s.packaging_require_approval
        if required and not ctx.run.path(PENDING_FILE).exists():
            # Missing gate file: write it so --approve/--reject have something to act on.
            PackagingStage._write_pending(
                ctx, topic, angle, [v.model_dump() for v in pkg.variants],
                pkg.winner.model_dump(), pkg.slug,
            )
        pending = _read_pending(ctx) if required else {}
        approved = pending.get("approved") is True
        rejected = pending.get("rejected") is True

        publish_title, thumb_hook = pkg.publish_title, pkg.thumb_hook
        variant_id = pkg.winner.variant_id

        # --- privacy + approval gate -------------------------------------
        use_choice = False
        if video_id:
            privacy = partial.get("privacy") or "private"
            use_choice = required and privacy == "public" and approved and not rejected
        else:
            privacy = _safe_privacy(review)
            if required and privacy == "public":
                if rejected:
                    privacy = "private"
                    log.info("packaging rejected - forcing privacy=private")
                elif not approved:
                    log.warning(
                        "PACKAGING GATE: public upload paused. Review variants then run "
                        "--approve [--variant N] or --reject, then re-run."
                    )
                    return StageOutcome.WAIT
                else:
                    use_choice = True

        if use_choice:
            chosen = _chosen_variant(pending, pkg.winner.model_dump())
            new_title = (chosen.get("title") or publish_title).strip()
            new_hook = (chosen.get("hook") or thumb_hook).strip()
            variant_id = chosen.get("variant_id") or variant_id
            changed = (new_title, new_hook) != (publish_title, thumb_hook)
            publish_title, thumb_hook = new_title, new_hook
            if changed and not video_id:
                try:
                    regen = generate_thumbnail(
                        ctx.images, publish_title, topic, script.thumbnail_prompt,
                        thumb_hook, ctx.run.path("thumbnail.jpg"), verifier=ctx.clip,
                        text_enabled=s.thumbnail_text_enabled,
                    )
                    if regen:
                        thumbnail_path = str(regen)
                        log.info("thumbnail regenerated after packaging approval")
                except Exception as exc:  # noqa: BLE001 - non-fatal
                    log.warning("thumb regen after approval failed (non-fatal): %s", exc)

        log.info(
            "publishing: %s (score=%s, safety=%s, threshold=80 + safe=Yes)",
            privacy, review.score, review.safety,
        )

        # --- long upload (fatal) ------------------------------------------
        up_next = build_up_next_blurb(ctx.analytics.recent_public_video_ids(limit=3))
        category_id: str = partial.get("category_id") or resolve_category_id(
            topic=topic, title=publish_title, default=s.default_category
        )
        if not video_id:
            tags_str = ", ".join(script.seo_tags) if script.seo_tags else ""
            base_description = script.seo_description or (
                f"{topic}\n\n"
                "Educational content. Subscribe for daily trending topic videos.\n\n"
                f"Tags: {tags_str}"
            )
            if up_next:
                base_description = f"{base_description.rstrip()}\n\n{up_next}"
            labels = scene_labels(script.long_script)
            description = build_publish_description(
                base_description,
                timeline=timeline,
                scene_titles=scene_titles_from_script(labels, scene_texts(script.long_script)),
            )
            video_id = yt.upload_video(
                title=publish_title,
                description=description,
                privacy_status=privacy,
                thumbnail_path=Path(thumbnail_path) if thumbnail_path else None,
                tags=script.seo_tags or [],
                video_path=video_path,
                category_id=category_id,
            )
            if not video_id:
                raise RuntimeError("long video upload returned no video id")
            partial.update(video_id=video_id, privacy=privacy, category_id=category_id)
            ctx.run.save_partial(self.name, partial)  # before any other side effect

        # --- post-upload steps (non-fatal, once) ---------------------------
        if not partial.get("post_steps_done"):
            self._post_steps(ctx, video_id, privacy, publish_title, topic, angle, up_next)
            partial["post_steps_done"] = True
            ctx.run.save_partial(self.name, partial)

        # --- Shorts upload (non-fatal) --------------------------------------
        shorts_video_id: str | None = partial.get("shorts_video_id")
        if (
            s.shorts_enabled
            and shorts_path is not None
            and shorts_path.exists()
            and not shorts_video_id
        ):
            try:
                shorts_video_id = yt.upload_video(
                    title=shorts_title(publish_title),
                    description=(
                        f"{topic}\n\n#Shorts #Trending #India\n\n"
                        f"{(script.seo_description or '')[:200]}"
                    ),
                    privacy_status=privacy,
                    thumbnail_path=None,
                    tags=list(script.seo_tags or []) + ["Shorts", "trending"],
                    video_path=shorts_path,
                    category_id=resolve_category_id(
                        topic=topic, title=publish_title, default=s.default_category
                    ),
                )
                if shorts_video_id:
                    partial["shorts_video_id"] = shorts_video_id
                    ctx.run.save_partial(self.name, partial)
            except Exception as exc:  # noqa: BLE001 - non-fatal
                log.warning("Shorts upload failed (non-fatal): %s", exc)
                shorts_video_id = None

        # --- analytics records -------------------------------------------------
        for vid, is_short in ((video_id, False), (shorts_video_id, True)):
            if not vid:
                continue
            title = f"{publish_title} #Shorts" if is_short else publish_title
            try:
                ctx.analytics.record_video(
                    video_id=vid,
                    topic=topic,
                    angle=angle,
                    seo_title=title,
                    privacy_status=privacy,
                    title=title,
                    thumb_hook=thumb_hook,
                    packaging_variant_id=variant_id,
                    is_short=is_short,
                    format_id=format_id,
                )
            except Exception as exc:  # noqa: BLE001 - non-fatal
                log.warning("analytics record failed (non-fatal): %s", exc)
        try:
            ctx.analytics.log_run(video_id, topic, publish_title, privacy)
        except Exception as exc:  # noqa: BLE001 - non-fatal
            log.warning("analytics log_run failed (non-fatal): %s", exc)

        ctx.run.mark_done(
            self.name,
            {
                "video_id": video_id,
                "shorts_video_id": shorts_video_id,
                "privacy": privacy,
                "category_id": category_id,
            },
        )
        return None

    @staticmethod
    def _post_steps(
        ctx: RunContext,
        video_id: str,
        privacy: str,
        title: str,
        topic: str,
        angle: str,
        up_next: str,
    ) -> None:
        """Pinned comment, playlist (public only), X post. Each non-fatal."""
        yt = ctx.youtube
        assert yt is not None
        comment = (
            f"Today's topic: {topic}. "
            f"{angle + ' — ' if angle else ''}"
            f"Let us know your thoughts in the comments! 👇"
        )
        if up_next:
            comment = f"{comment}\n\n{up_next}"
        try:
            yt.post_pinned_comment(video_id, comment)
        except Exception as exc:  # noqa: BLE001
            log.warning("pinned comment failed (non-fatal): %s", exc)

        if privacy == "public":
            fmt = ctx.run.get("pick_format")
            try:
                playlist_id = yt.ensure_playlist(
                    fmt.get("playlist_title", ""),
                    description=f"Series: {fmt.get('format_name', '')}",
                )
                if playlist_id:
                    yt.add_video_to_playlist(playlist_id, video_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("playlist add failed (non-fatal): %s", exc)
        else:
            log.info("skipping playlist add - upload is not public")

        try:
            post_video_update(
                ctx.settings,
                ctx.settings.state_dir / "x_post_log.json",
                title=title,
                video_id=video_id,
                topic=topic,
                angle=angle,
                privacy_status=privacy,
                pipeline="yt_video",
                hashtags=X_HASHTAGS,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("X post failed (non-fatal): %s", exc)


ALL_STAGES: list[type[Stage]] = [*TEXT_STAGES, *MEDIA_STAGES, PublishStage]
