"""Command line entry point for the yt-video pipeline.

Exit codes: 0 success/done, 1 pipeline error, 2 usage error, 3 awaiting approval.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Any

from yt_video.costs import CostLedger, format_run_summary
from yt_video.logging import add_run_file_handler, get_logger
from yt_video.media.clip import ClipVerifier
from yt_video.memory._io import write_json_atomic
from yt_video.memory.tts_memory import TTSMemory
from yt_video.migrate import migrate_state, needs_migration
from yt_video.pipeline.context import RunContext
from yt_video.pipeline.run_store import Run, RunStateCorruptError, RunStore
from yt_video.pipeline.runner import Pipeline
from yt_video.settings import PROJECT_ROOT, Settings, load_settings

if TYPE_CHECKING:
    from yt_video.integrations.youtube import YouTubeClient
    from yt_video.memory.analytics_memory import AnalyticsMemory

log = get_logger()

PENDING_FILE = "packaging_pending.json"
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_AWAITING = 0, 1, 2, 3
AWAITING_TEXT = (
    "\nAwaiting packaging approval. Review with --list-packaging, then:\n"
    "  uv run python main.py --approve-packaging [--variant N]\n"
    "  uv run python main.py --reject-packaging\n"
    "and re-run: uv run python main.py"
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="yt-video: automated YouTube pipeline",
        epilog="Exit codes: 0 done, 1 pipeline error, 2 usage error, 3 awaiting approval.",
    )
    p.add_argument("--restart", action="store_true",
                   help="Discard the unfinished run and start a fresh one")
    p.add_argument("--new", action="store_true",
                   help="Abandon the unfinished run (kept on disk) and start a fresh one")
    p.add_argument("--list-voices", action="store_true",
                   help="Print all available Microsoft Edge TTS voices and exit")
    p.add_argument("--fetch-analytics", action="store_true",
                   help="Fetch YouTube Analytics for videos uploaded 48+ hours ago")
    p.add_argument("--no-upload", action="store_true",
                   help="Run the full pipeline but skip the YouTube upload")
    p.add_argument("--approve-packaging", action="store_true",
                   help="Approve pending packaging for public upload")
    p.add_argument("--reject-packaging", action="store_true",
                   help="Reject packaging and force this run to upload as private")
    p.add_argument("--variant", type=int, default=None,
                   help="0-based packaging variant index to approve (with --approve-packaging)")
    p.add_argument("--list-packaging", action="store_true",
                   help="Print pending packaging variants and exit")
    p.add_argument("--claude-only", action="store_true",
                   help="Use only the Claude CLI provider (no OpenAI fallback)")
    p.add_argument("--migrate-state", action="store_true",
                   help="Copy legacy state files into the state directory and exit")
    return p


def _log_claude_version(settings: Settings) -> None:
    """Log `claude --version` once per run. Never fatal."""
    try:
        proc = subprocess.run(
            [settings.claude_cli_path, "--version"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        log.info("claude --version: %s", (proc.stdout or proc.stderr).strip())
    except Exception as exc:  # noqa: BLE001
        log.info("claude --version unavailable (%s)", exc)


def _find_unfinished(store: RunStore, restart: bool = False) -> tuple[Run | None, int | None]:
    """latest_unfinished with corrupt-state handling; second value is an exit code on failure."""
    try:
        return store.latest_unfinished(), None
    except RunStateCorruptError as err:
        if restart:
            log.warning("discarding corrupt run folder %s", err.run_dir)
            store.discard_dir(err.run_dir)
            return None, None
        print(str(err))
        return None, EXIT_USAGE


def _read_pending(run: Run) -> dict[str, Any] | None:
    """Read the pending file without side effects; None if missing or invalid."""
    try:
        data = json.loads(run.path(PENDING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _print_pending(pending: dict[str, Any]) -> None:
    winner = pending.get("winner") or {}
    print(f"Topic: {pending.get('topic')}")
    print(f"Angle: {pending.get('angle')}")
    print(f"Format: {pending.get('format_id')}")
    print(f"Approved: {pending.get('approved')}  rejected={pending.get('rejected')}")
    print(f"Current winner: {winner.get('variant_id')} '{winner.get('title')}' "
          f"hook='{winner.get('hook')}'")
    for i, v in enumerate(pending.get("variants") or []):
        marker = " <- winner" if v.get("variant_id") == winner.get("variant_id") else ""
        print(f"  [{i}] {v.get('variant_id')} score={v.get('score')} "
              f"title='{v.get('title')}' hook='{v.get('hook')}'{marker}")


def _packaging_command(settings: Settings, args: argparse.Namespace) -> int:
    store = RunStore(settings.runs_dir)
    run, code = _find_unfinished(store)
    if code is not None:
        return code
    miss = EXIT_OK if args.list_packaging else EXIT_USAGE
    if run is None:
        print("No unfinished run. Run the pipeline first: uv run python main.py")
        return miss
    pending = _read_pending(run)
    if pending is None:
        print(f"No packaging pending for run {run.id}. Run the pipeline with "
              "PACKAGING_REQUIRE_APPROVAL=true first.")
        return miss

    if args.list_packaging:
        _print_pending(pending)
        return EXIT_OK

    if args.approve_packaging:
        variants = pending.get("variants") or []
        if args.variant is not None and not 0 <= args.variant < len(variants):
            print(f"--variant {args.variant} out of range (0..{max(len(variants) - 1, 0)})")
            return EXIT_USAGE
        _print_pending(pending)
        pending["approved"] = True
        pending["rejected"] = False
        if args.variant is not None:
            pending["chosen_index"] = args.variant
        write_json_atomic(run.path(PENDING_FILE), pending, ensure_ascii=False)
        idx = pending.get("chosen_index")
        if isinstance(idx, int) and 0 <= idx < len(variants):
            chosen = variants[idx]
        else:
            chosen = pending.get("winner") or {}
        print(f"\nApproved: {chosen.get('variant_id')} '{chosen.get('title')}'"
              " - re-run: uv run python main.py")
        return EXIT_OK

    pending["rejected"] = True
    pending["approved"] = False
    write_json_atomic(run.path(PENDING_FILE), pending, ensure_ascii=False)
    print("Packaging rejected - upload will be private. Re-run: uv run python main.py")
    return EXIT_OK


def _youtube(settings: Settings) -> YouTubeClient:
    from yt_video.integrations.youtube import YouTubeClient

    return YouTubeClient(
        settings.youtube_client_secret,
        settings.state_dir / "youtube_token.json",
        settings.state_dir / "playlists.json",
        channel_name=settings.channel_name,
    )


def _analytics(settings: Settings, youtube: YouTubeClient) -> AnalyticsMemory:
    from yt_video.memory.analytics_memory import AnalyticsMemory

    return AnalyticsMemory(
        settings.state_dir / "yt_analytics_memory.json",
        settings.state_dir / "analytics_log.json",
        credentials_provider=youtube.credentials,
    )


def _ensure_migrated(settings: Settings) -> None:
    """Copy legacy state once, before any memory file is opened."""
    if needs_migration(settings.state_dir):
        log.info("state migrated: %s", migrate_state(settings.state_dir, PROJECT_ROOT))


def _pipeline_run(settings: Settings, args: argparse.Namespace) -> int:
    _ensure_migrated(settings)
    store = RunStore(settings.runs_dir)
    run, code = _find_unfinished(store, restart=args.restart)
    if code is not None:
        return code
    now = datetime.now()
    if run is not None and args.restart:
        log.info("--restart: discarding run %s", run.id)
        store.discard(run)
        run = None
    elif run is not None and args.new:
        log.info("--new: abandoning run %s", run.id)
        store.abandon(run)
        run = None
    if run is None:
        run = store.create(now)

    handler = None
    try:
        handler = add_run_file_handler(run.path("pipeline.log"))
        from yt_video.pipeline.stages.publish import ALL_STAGES
        from yt_video.providers.image.chain import build_image_chain
        from yt_video.providers.llm.router import build_router

        _log_claude_version(settings)
        ledger = CostLedger(settings.state_dir / "costs.db")
        youtube = _youtube(settings)
        clip = ClipVerifier(settings.clip_verify, settings.clip_min_score, settings.clip_model)
        ctx = RunContext(
            settings=settings,
            run=run,
            router=build_router(settings, ledger, run.id, args.claude_only),
            images=build_image_chain(settings, ledger, run.id),
            clip=clip,
            tts_memory=TTSMemory(settings.state_dir / "tts_style_memory.json"),
            analytics=_analytics(settings, youtube),
            youtube=youtube,
            ledger=ledger,
            no_upload=args.no_upload,
            today=now.date(),
        )
        outcome = Pipeline([stage() for stage in ALL_STAGES]).run(ctx)
    except Exception:  # noqa: BLE001
        log.exception("pipeline failed in run %s (re-run to resume)", run.id)
        return EXIT_ERROR
    else:
        try:
            print(format_run_summary(ledger.run_rows(run.id)))
            print(clip.summary())
        except Exception as exc:  # noqa: BLE001 - summary never changes the outcome
            log.warning("run summary unavailable: %s", exc)
        if outcome == "awaiting_approval":
            print(AWAITING_TEXT)
            return EXIT_AWAITING
        return EXIT_OK
    finally:
        if handler is not None:
            get_logger().removeHandler(handler)
            handler.close()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    packaging = (args.approve_packaging, args.reject_packaging, args.list_packaging)
    if sum(bool(x) for x in packaging) > 1:
        print("--approve-packaging, --reject-packaging, and --list-packaging "
              "are mutually exclusive")
        return EXIT_USAGE
    if args.variant is not None and not args.approve_packaging:
        print("--variant requires --approve-packaging")
        return EXIT_USAGE

    if args.list_voices:
        from yt_video.providers.tts import list_edge_voices

        print("\nAvailable Microsoft Edge TTS voices (English):\n")
        list_edge_voices()
        return EXIT_OK

    settings = load_settings()

    if args.migrate_state:
        print(migrate_state(settings.state_dir, PROJECT_ROOT))
        return EXIT_OK
    if any(packaging):
        return _packaging_command(settings, args)
    if args.fetch_analytics:
        _ensure_migrated(settings)
        fetched = _analytics(settings, _youtube(settings)).fetch_pending()
        if fetched:
            log.info("Done. %s video(s) analyzed.", fetched)
        else:
            log.info("No videos ready yet (need 48h+ since upload).")
        return EXIT_OK
    return _pipeline_run(settings, args)
