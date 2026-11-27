"""Claude CLI provider: ``claude -p`` as a subprocess on the owner's subscription."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Literal

from yt_video.models import llm_schema
from yt_video.logging import get_logger
from yt_video.providers.llm.base import (
    LLMRequest,
    ProviderError,
    RawCompletion,
    extract_json,
)

logger = get_logger(__name__)

# Case-insensitive. "overloaded"/529 is NOT a limit status (plain "exit"), and
# "Extra usage is required for long context" is a request-shape error, not a limit
# (Ruling 4). That exclusion applies only to the envelope's own ``result`` message.
USAGE_LIMIT_PATTERNS: tuple[str, ...] = (
    r"hit your .{0,30}limit",
    r"usage limit reached",
    r"credit balance is too low",
    r"rate_limit",
    r"extra usage(?! is required for long context)",
)
_LONG_CONTEXT_RE = re.compile(r"extra usage is required for long context", re.IGNORECASE)
_LIMIT_RE = re.compile("|".join(USAGE_LIMIT_PATTERNS), re.IGNORECASE)
# Success-path (rc==0) overage notices only. Narrower than _LIMIT_RE on purpose: bare
# ``rate_limit`` and ``credit balance is too low`` are omitted because a 429 that
# recovered on retry is logged to stderr on a normal success (Ruling 24).
_SUCCESS_OVERAGE_RE = re.compile(
    "|".join((
        r"hit your .{0,30}limit",
        r"usage limit reached",
        r"extra usage(?! is required for long context)",
    )),
    re.IGNORECASE,
)
_LIMIT_STATUSES = {429, 402}
_EXCERPT = 300


def _excerpt(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= _EXCERPT else text[:_EXCERPT] + "..."


def build_claude_args(
    cli_path: str, model: str, system_prompt_file: Path, schema: dict | None
) -> list[str]:
    args = [
        cli_path, "-p", "--model", model,
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--system-prompt-file", str(system_prompt_file),
    ]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    return args


def _parse_envelope(stdout: str) -> dict | None:
    try:
        obj = json.loads(stdout)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _usage(env: dict) -> tuple[int | None, int | None, str | None]:
    usage = env.get("usage")
    inp = out = None
    if isinstance(usage, dict):
        inp = sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        out = int(usage.get("output_tokens") or 0)
    models = env.get("modelUsage")
    model = next(iter(models), None) if isinstance(models, dict) and models else None
    return inp, out, model


def _classify_limit(
    message: str, api_error_status: int | None, haystack: str | None = None
) -> Literal["usage_limit", "exit"]:
    """Single home of the usage-limit rule.

    ``message`` is the envelope's ``result`` (or the raw output when there is no
    envelope): a long-context message there is never a limit. Otherwise a 429/402
    status wins, then the patterns over ``haystack`` (message + stdout + stderr).
    """
    if _LONG_CONTEXT_RE.search(message):
        return "exit"
    if api_error_status in _LIMIT_STATUSES or _LIMIT_RE.search(haystack if haystack is not None else message):
        return "usage_limit"
    return "exit"


def _success_overage(env: dict, stderr: str) -> bool:
    """Overage notice on a successful call. UNVERIFIED: the shapes below are not
    confirmed against a real overage run (fixtures are synthetic). Scans stderr
    only, never ``result``, and uses the narrower ``_SUCCESS_OVERAGE_RE`` instead of
    ``_LIMIT_RE``: a call that recovered from a temporary 429 is a normal success
    and may log ``rate_limit`` to stderr, which must not trip the breaker.
    ``usage.fallback_credit`` is null on real success fixtures, so any truthy value
    is treated as a soft overage flag.
    """
    if _SUCCESS_OVERAGE_RE.search(stderr or ""):
        return True
    usage = env.get("usage")
    return isinstance(usage, dict) and bool(usage.get("fallback_credit"))


def parse_claude_envelope(stdout: str, stderr: str, returncode: int) -> RawCompletion:
    env = _parse_envelope(stdout)
    if env is None:
        if returncode == 0:
            raise ProviderError("exit", f"unparseable claude output: {_excerpt(stdout or stderr)}")
        kind = _classify_limit(f"{stdout}\n{stderr}", None)
        raise ProviderError(kind, f"claude exited {returncode}: {_excerpt(stderr or stdout)}")

    result = env.get("result")
    text = result if isinstance(result, str) else ""
    inp, out, model = _usage(env)

    if env.get("is_error") is True or returncode != 0:
        haystack = f"{text}\n{stdout}\n{stderr}"
        cost = env.get("total_cost_usd")
        raise ProviderError(
            _classify_limit(text, env.get("api_error_status"), haystack),
            f"claude error (rc={returncode}): {_excerpt(text or stderr or stdout)}",
            input_tokens=inp,
            output_tokens=out,
            model=model,
            notional_cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        )

    data = env.get("structured_output")
    if not isinstance(data, dict):
        data = extract_json(text)
    cost = env.get("total_cost_usd")
    return RawCompletion(
        provider="claude_cli",
        model=model or "",
        text=text,
        data=data,
        input_tokens=inp,
        output_tokens=out,
        cost_usd=0.0,
        notional_cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        overage=_success_overage(env, stderr),
    )


class ClaudeCLIProvider:
    name = "claude_cli"

    def __init__(
        self,
        cli_path: str,
        model_map: dict[str, str],
        timeouts: dict[str, int],
        scratch_dir: Path,
        run_cmd: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self._cli = cli_path
        self._models = model_map
        self._timeouts = timeouts
        self._scratch = Path(scratch_dir)
        self._run = run_cmd

    def available(self) -> bool:
        return shutil.which(self._cli) is not None

    def complete(self, req: LLMRequest) -> RawCompletion:
        alias = self._models[req.tier]
        timeout = req.timeout_s if req.timeout_s is not None else self._timeouts[req.tier]
        self._scratch.mkdir(parents=True, exist_ok=True)
        system_file = self._scratch / f"{req.task}_system.txt"
        system_file.write_text(req.system, encoding="utf-8")
        schema = llm_schema(req.schema) if req.schema is not None else None
        args = build_claude_args(self._cli, alias, system_file, schema)
        try:
            proc = self._run(
                args,
                input=req.prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=self._scratch,
            )
        except OSError as exc:  # FileNotFoundError, WinError 5/206, ...
            raise ProviderError("unavailable", f"claude CLI could not run: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("timeout", f"claude timed out after {timeout}s") from exc
        raw = parse_claude_envelope(proc.stdout or "", proc.stderr or "", proc.returncode)
        raw.model = raw.model or alias
        if req.schema is not None and raw.data is None:
            raise ProviderError(
                "invalid_output",
                f"no JSON in claude output: {_excerpt(raw.text)}",
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                model=raw.model,
            )
        return raw
