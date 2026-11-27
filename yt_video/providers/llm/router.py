"""LLM router: provider fallback, validation retry, and a per-run circuit breaker."""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, ValidationError

from yt_video.costs import CostLedger
from yt_video.logging import get_logger
from yt_video.providers.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    ProviderError,
    RawCompletion,
)
from yt_video.settings import Settings

log = get_logger(__name__)

_RETRY_SUFFIX = "\n\nYour previous reply failed validation:\n{errors}\nReturn corrected JSON only."


class LLMRouter:
    def __init__(
        self,
        providers: list[LLMProvider],
        ledger: CostLedger | None,
        run_id: str,
        claude_only: bool = False,
    ) -> None:
        self._providers = [p for p in providers if p.name == "claude_cli"] if claude_only else list(providers)
        self._ledger = ledger
        self._run_id = run_id
        self.disabled: set[str] = set()
        self._breaker_reason: dict[str, str] = {}
        self._cleanup_dirs: list[Path] = []

    def close(self) -> None:
        """Best-effort removal of per-run temp dirs (Claude scratch). Idempotent."""
        while self._cleanup_dirs:
            shutil.rmtree(self._cleanup_dirs.pop(), ignore_errors=True)

    def _trip(self, name: str, reason: str) -> None:
        """Open the breaker for the rest of the run; warn once (label only, not persisted)."""
        if name in self.disabled:
            return
        self.disabled.add(name)
        self._breaker_reason[name] = reason
        log.warning("%s circuit breaker open (%s): skipping it for the rest of this run", name, reason)

    def complete(self, req: LLMRequest, stage: str) -> LLMResult:
        attempts: list[tuple[str, str]] = []
        for provider in self._providers:
            if provider.name in self.disabled:
                attempts.append((provider.name, f"breaker_open({self._breaker_reason[provider.name]})"))
                continue
            if not provider.available():
                self._note_failure(provider.name, "unavailable", attempts)
                continue
            result = self._try_provider(provider, req, stage, attempts)
            if result is not None:
                return result
        raise LLMError(attempts)

    def _try_provider(
        self, provider: LLMProvider, req: LLMRequest, stage: str, attempts: list[tuple[str, str]]
    ) -> LLMResult | None:
        current = req
        for attempt in (1, 2):
            try:
                raw = provider.complete(current)
            except ProviderError as err:
                self._record_failed(provider.name, stage, err, err.kind)
                log.warning("%s attempt %d failed (%s): %s", provider.name, attempt, err.kind, err)
                if err.kind == "invalid_output" and attempt == 1:
                    # Model returned no parseable JSON: one corrective retry, like a validation failure.
                    current = replace(req, prompt=req.prompt + _RETRY_SUFFIX.format(errors=str(err)))
                    continue
                if err.kind == "usage_limit":
                    self._trip(provider.name, "usage_limit")
                self._note_failure(provider.name, err.kind, attempts)
                return None
            parsed, errors = self._validate(req, raw)
            if errors is None:
                if raw.overage:  # UNVERIFIED success-path signal (Ruling 21): keep result, stop using Claude
                    self._trip(provider.name, "overage")
                return self._finish(raw, parsed, stage, attempts)
            self._record_failed(provider.name, stage, raw, "invalid_output")
            log.warning("%s attempt %d failed validation: %s", provider.name, attempt, errors)
            if attempt == 1:
                current = replace(req, prompt=req.prompt + _RETRY_SUFFIX.format(errors=errors))
        self._note_failure(provider.name, "invalid_output", attempts)
        return None

    @staticmethod
    def _validate(req: LLMRequest, raw: RawCompletion) -> tuple[BaseModel | None, str | None]:
        if req.schema is None:
            return None, None
        try:
            return req.schema.model_validate(raw.data), None
        except ValidationError as exc:
            return None, str(exc)

    @staticmethod
    def _note_failure(name: str, kind: str, attempts: list[tuple[str, str]]) -> None:
        attempts.append((name, kind))
        log.warning("LLM fallback: %s failed (%s)", name, kind)

    def _record_failed(self, provider: str, stage: str, src: ProviderError | RawCompletion, reason: str) -> None:
        """Ruling 7 / M2: a failed call that billed or reported usage still goes in the ledger."""
        notional = getattr(src, "notional_cost_usd", None)
        has_usage = (
            (src.cost_usd or 0) > 0
            or (src.input_tokens or 0) > 0
            or (src.output_tokens or 0) > 0
            or (notional or 0) > 0
        )
        if self._ledger is None or not has_usage:
            return
        self._ledger.record(
            self._run_id, stage, "llm", provider, getattr(src, "model", None) or "unknown",
            src.input_tokens, src.output_tokens, src.cost_usd or 0.0, notional, reason,
        )

    def _finish(
        self, raw: RawCompletion, parsed: BaseModel | None, stage: str, attempts: list[tuple[str, str]]
    ) -> LLMResult:
        reason = "; ".join(f"{p}: {r}" for p, r in attempts) or None
        if self._ledger is not None:
            self._ledger.record(
                self._run_id, stage, "llm", raw.provider, raw.model, raw.input_tokens,
                raw.output_tokens, raw.cost_usd, raw.notional_cost_usd, reason,
            )
        return LLMResult(**vars(raw), parsed=parsed, fallback_reason=reason)


def build_router(
    settings: Settings, ledger: CostLedger | None, run_id: str, claude_only: bool
) -> LLMRouter:
    providers: list[LLMProvider] = []
    cleanup: list[Path] = []
    for name in settings.llm_providers:
        if name == "claude_cli":
            from yt_video.providers.llm.claude_cli import ClaudeCLIProvider

            # Outside the repo (M1); removed by LLMRouter.close().
            scratch = Path(tempfile.mkdtemp(prefix="yt-video-claude-"))
            cleanup.append(scratch)
            providers.append(ClaudeCLIProvider(
                cli_path=settings.claude_cli_path,
                model_map={"script": settings.claude_model_script, "light": settings.claude_model_light},
                timeouts={"script": settings.claude_timeout_script_s, "light": settings.claude_timeout_light_s},
                scratch_dir=scratch,
            ))
        elif name == "openai":
            from yt_video.providers.llm.openai_api import OpenAIProvider

            providers.append(OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_fallback_model,
                pricing=settings.openai_pricing,
            ))
        else:
            raise ValueError(f"unknown LLM provider: {name!r}")
    router = LLMRouter(providers, ledger, run_id, claude_only)
    router._cleanup_dirs.extend(cleanup)
    return router
