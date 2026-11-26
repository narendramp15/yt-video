import shutil

import pytest

from yt_video.models import ContentAngle
from yt_video.providers.llm.base import LLMRequest
from yt_video.providers.llm.claude_cli import ClaudeCLIProvider

pytestmark = pytest.mark.live


def test_claude_cli_structured_call(tmp_path):
    cli = shutil.which("claude")
    if cli is None:
        pytest.skip("claude CLI not installed")
    provider = ClaudeCLIProvider(
        cli, {"script": "sonnet", "light": "haiku"}, {"script": 600, "light": 180}, tmp_path
    )
    raw = provider.complete(
        LLMRequest(
            task="live_angle",
            system="You are a concise assistant.",
            prompt="Give a one-sentence content angle for a video about UPI payments in India.",
            schema=ContentAngle,
            tier="light",
        )
    )
    assert ContentAngle.model_validate(raw.data).angle.strip()
    assert raw.cost_usd == 0.0
    print(raw.model, raw.input_tokens, raw.output_tokens, raw.notional_cost_usd)
