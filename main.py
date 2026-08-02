"""
main.py — yt-video entry point

Usage:
  python main.py              # run pipeline (resumes from checkpoint)
  python main.py --restart    # force full re-run from scratch
  python main.py --list-voices  # print all available Edge TTS voices
  python main.py --approve-packaging [--variant N]
  python main.py --reject-packaging

Environment setup:
  cp env.example .env         # fill in your API keys
  pip install -r requirements.txt
"""

import sys
import os
import argparse

# Make all modules importable from the yt-video directory
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Change CWD to the yt-video directory so all relative paths
# (output/, output/script.txt, etc.) resolve inside yt-video/
# and don't collide with the root yt_ai_factory output/.
os.chdir(_HERE)

# Ensure output directory exists before logger tries to create pipeline.log
os.makedirs(os.path.join(_HERE, "output"), exist_ok=True)

from logger import logger  # noqa: E402
from services.checkpoint_service import reset_today  # noqa: E402
from workflows.long_video import run_long_video  # noqa: E402


def _print_packaging_pending() -> None:
    from services.packaging_service import load_packaging_pending

    pending = load_packaging_pending()
    if not pending:
        print("No packaging pending. Run the pipeline with PACKAGING_REQUIRE_APPROVAL=true first.")
        return
    print(f"Topic: {pending.get('topic')}")
    print(f"Angle: {pending.get('angle')}")
    print(f"Format: {pending.get('format_id')}")
    print(f"Approved: {pending.get('approved')}  force_private={pending.get('force_private')}")
    winner = pending.get("winner") or {}
    print(
        f"Current winner: {winner.get('variant_id')} "
        f"'{winner.get('title')}' hook='{winner.get('hook')}'"
    )
    for i, v in enumerate(pending.get("variants") or []):
        marker = " ← winner" if v.get("variant_id") == winner.get("variant_id") else ""
        print(
            f"  [{i}] {v.get('variant_id')} score={v.get('score')} "
            f"title='{v.get('title')}' hook='{v.get('hook')}'{marker}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="yt-video — 100% free YouTube pipeline (edge-tts + Pollinations.ai + pytrends)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Cost breakdown (monthly savings vs original pipeline):
  $22–$99   ElevenLabs  →  Microsoft Edge TTS (free, 300+ voices)
  $3–$12    DALL-E/Flux →  Pollinations.ai    (free, no key needed)
  $49       NewsAPI     →  pytrends + RSS + yfinance  (free)
  $5–$20    AWS Lambda  →  Local FFmpeg       (free)
  ─────────────────────────────────────────
  Total:  $79–$180/month saved
        """,
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Clear today's checkpoint and re-run the full pipeline from scratch",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="Print all available Microsoft Edge TTS voices and exit",
    )
    parser.add_argument(
        "--fetch-analytics",
        action="store_true",
        help=(
            "Fetch YouTube Analytics for videos uploaded 48+ hours ago, "
            "update performance memory, and improve future runs. "
            "Opens a browser OAuth window."
        ),
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Run the full pipeline (Phases 1–4) but skip the YouTube upload step (Phase 5).",
    )
    parser.add_argument(
        "--approve-packaging",
        action="store_true",
        help="Approve pending packaging for public upload (Phase C CLI gate).",
    )
    parser.add_argument(
        "--reject-packaging",
        action="store_true",
        help="Reject packaging and force this run to upload as private.",
    )
    parser.add_argument(
        "--variant",
        type=int,
        default=None,
        help="0-based packaging variant index to approve (with --approve-packaging).",
    )
    parser.add_argument(
        "--list-packaging",
        action="store_true",
        help="Print pending packaging variants and exit.",
    )
    args = parser.parse_args()

    if args.list_voices:
        from services.tts_service import list_edge_voices
        print("\nAvailable Microsoft Edge TTS voices (English):\n")
        list_edge_voices()
        return

    if args.list_packaging:
        _print_packaging_pending()
        return

    if args.approve_packaging:
        from services.packaging_service import approve_packaging
        _print_packaging_pending()
        data = approve_packaging(variant_index=args.variant)
        chosen = data.get("chosen") or {}
        print(
            f"\nApproved: {chosen.get('variant_id')} "
            f"'{chosen.get('title')}' — re-run: python main.py"
        )
        return

    if args.reject_packaging:
        from services.packaging_service import reject_packaging
        reject_packaging()
        print("Packaging rejected — upload will be private. Re-run: python main.py")
        return

    if args.fetch_analytics:
        from services.yt_analytics_memory_service import fetch_pending_analytics
        logger.info("Fetching YouTube Analytics for all pending videos...")
        fetched = fetch_pending_analytics()
        if fetched:
            logger.info(f"Done. {fetched} video(s) analyzed. Next run will use these insights.")
        else:
            logger.info("No videos ready yet (need 48h+ since upload).")
        return

    if args.restart:
        reset_today()
        logger.info("Pipeline state reset — starting from scratch")

    run_long_video(no_upload=args.no_upload)


if __name__ == "__main__":
    main()
