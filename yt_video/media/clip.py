"""Local CLIP semantic relevance scoring (ported from services/clip_service.py).

Answers one question per image: "does this generated image depict its prompt?"
torch/transformers are imported lazily and only when the verifier is enabled.
A disabled verifier, or one whose backend fails to load, returns None.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from yt_video.logging import get_logger

logger = get_logger(__name__)


class ClipVerifier:
    def __init__(self, enabled: bool, min_score: float, model_id: str) -> None:
        self.enabled = enabled
        self.min_score = min_score
        self.model_id = model_id
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self._unavailable = False
        self._scores: list[float] = []

    def _load(self) -> bool:
        """Lazily load CLIP model + processor. Returns False if unavailable."""
        if self._unavailable:
            return False
        if self._model is not None:
            return True
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor

            logger.info("Loading CLIP model (%s, CPU)...", self.model_id)
            self._torch = torch
            model = CLIPModel.from_pretrained(self.model_id)
            model.eval()
            self._processor = CLIPProcessor.from_pretrained(self.model_id)
            self._model = model
            logger.info("CLIP model ready - image relevance verification enabled")
            return True
        except Exception as exc:  # noqa: BLE001 - any backend failure disables CLIP
            logger.warning(
                "CLIP unavailable (%s) - image relevance verification disabled. "
                "Install with: uv sync --extra clip",
                exc,
            )
            self._unavailable = True
            return False

    # -- session accuracy accumulator ------------------------------------
    def reset(self) -> None:
        """Clear accumulated scores; call before generating a video's scene set."""
        self._scores.clear()

    def record(self, score: float) -> None:
        """Record one accepted-image relevance score for the session mean."""
        self._scores.append(score)

    def summary(self) -> str:
        if not self._scores:
            return "CLIP accuracy: not measured (verification off or backend missing)"
        n = len(self._scores)
        mean = sum(self._scores) / n
        passed = sum(1 for s in self._scores if s >= self.min_score)
        return (
            f"CLIP scene accuracy: mean {mean:.3f} over {n} scenes, "
            f"{passed}/{n} ({100 * passed / n:.0f}%) cleared threshold {self.min_score}"
        )

    def score(self, image_path: Path, prompt: str) -> float | None:
        """Cosine similarity between image and prompt in CLIP space.

        Matched pairs typically score 0.25-0.35, mismatched below 0.20.
        None means "could not verify" (disabled, backend missing, bad input).
        """
        if not self.enabled:
            return None
        image_path = Path(image_path)
        if not prompt or not image_path.exists():
            return None
        if not self._load():
            return None
        try:
            from PIL import Image

            img = Image.open(image_path).convert("RGB")
            # truncation keeps long prompts within CLIP's 77-token limit; the
            # concrete subject leads the prompt, so the tail is safe to drop.
            inputs = self._processor(
                text=[prompt], images=img, return_tensors="pt", padding=True, truncation=True
            )
            with self._torch.no_grad():
                out = self._model(**inputs)
            img_emb = out.image_embeds / out.image_embeds.norm(dim=-1, keepdim=True)
            txt_emb = out.text_embeds / out.text_embeds.norm(dim=-1, keepdim=True)
            return float((img_emb @ txt_emb.T).item())
        except Exception as exc:  # noqa: BLE001
            logger.warning("CLIP scoring failed for %s: %s", image_path.name, exc)
            return None
