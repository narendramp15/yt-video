"""
clip_service.py — yt-video

Local CLIP semantic relevance scoring. Answers one question per image:
"does this generated image actually depict its prompt?"

Used by image_service to verify Pollinations.ai output and regenerate
off-topic images with a new seed — the core of content-to-scene accuracy.

Backend: openai/clip-vit-base-patch32 via transformers + torch (CPU, free).
If those packages are not installed, scoring is disabled gracefully and the
pipeline behaves exactly as before (first valid image is accepted).

Install (optional, recommended):
  pip install torch --index-url https://download.pytorch.org/whl/cpu
  pip install transformers

Tuning (.env):
  CLIP_VERIFY        — 1 (default) | 0  to disable verification entirely
  CLIP_MIN_SCORE     — accept threshold, cosine similarity (default 0.24)
  CLIP_MODEL         — HF model id (default openai/clip-vit-base-patch32)
"""

import os
from logger import logger

CLIP_VERIFY    = os.getenv("CLIP_VERIFY", "1").strip().lower() not in {"0", "false", "no", "off"}
CLIP_MIN_SCORE = float(os.getenv("CLIP_MIN_SCORE", "0.24"))
CLIP_MODEL     = os.getenv("CLIP_MODEL", "openai/clip-vit-base-patch32")

# Lazy singletons — model is loaded on first score call, not at import.
_model = None
_processor = None
_torch = None
_unavailable = False   # set True once we know the backend can't load


def is_enabled() -> bool:
    """True if CLIP verification is switched on (does not test the backend)."""
    return CLIP_VERIFY


def _load() -> bool:
    """Lazily load CLIP model + processor. Returns False if unavailable."""
    global _model, _processor, _torch, _unavailable
    if _unavailable:
        return False
    if _model is not None:
        return True
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        logger.info(f"Loading CLIP model ({CLIP_MODEL}, CPU)...")
        _torch = torch
        _model = CLIPModel.from_pretrained(CLIP_MODEL)
        _model.eval()
        _processor = CLIPProcessor.from_pretrained(CLIP_MODEL)
        logger.info("CLIP model ready — image relevance verification enabled")
        return True
    except Exception as exc:
        logger.warning(
            f"CLIP unavailable ({exc}) — image relevance verification disabled. "
            f"Install with: pip install torch transformers"
        )
        _unavailable = True
        return False


# ---------------------------------------------------------------------------
# Session accuracy accumulator — lets a batch report a video-level mean score.
# ---------------------------------------------------------------------------
_scores: list[float] = []


def reset_session() -> None:
    """Clear accumulated scores — call before generating a video's scene set."""
    _scores.clear()


def record_score(score: float) -> None:
    """Record one accepted-image relevance score for the session mean."""
    _scores.append(score)


def session_summary() -> str:
    """Human-readable accuracy summary for the scores recorded so far."""
    if not _scores:
        return "CLIP accuracy: not measured (verification off or backend missing)"
    n = len(_scores)
    mean = sum(_scores) / n
    passed = sum(1 for s in _scores if s >= CLIP_MIN_SCORE)
    return (
        f"CLIP scene accuracy: mean {mean:.3f} over {n} scenes, "
        f"{passed}/{n} ({100 * passed / n:.0f}%) cleared threshold {CLIP_MIN_SCORE}"
    )


def score_image_prompt(image_path: str, prompt: str) -> float | None:
    """
    Cosine similarity between an image and a text prompt in CLIP space.

    Returns a float roughly in [-0.1, 0.4]:
      • matched pairs   typically score 0.25–0.35
      • mismatched ones typically score below 0.20
    Returns None when scoring could not run (backend missing, bad input) —
    callers should treat None as "could not verify", not as a failure.
    """
    if not CLIP_VERIFY:
        return None
    if not prompt or not image_path or not os.path.exists(image_path):
        return None
    if not _load():
        return None

    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        # truncation=True keeps long prompts within CLIP's 77-token limit;
        # the concrete subject leads the prompt, so the tail is safe to drop.
        inputs = _processor(
            text=[prompt], images=img,
            return_tensors="pt", padding=True, truncation=True,
        )
        with _torch.no_grad():
            out = _model(**inputs)
        img_emb = out.image_embeds / out.image_embeds.norm(dim=-1, keepdim=True)
        txt_emb = out.text_embeds / out.text_embeds.norm(dim=-1, keepdim=True)
        return float((img_emb @ txt_emb.T).item())
    except Exception as exc:
        logger.warning(f"CLIP scoring failed for {os.path.basename(image_path)}: {exc}")
        return None
