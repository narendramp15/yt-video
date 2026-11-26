import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from yt_video.costs import CostLedger
from yt_video.providers.image.chain import ImageChain, build_image_chain
from yt_video.providers.image.cloudflare import CloudflareProvider
from yt_video.providers.image.fake import FakeImageProvider
from yt_video.providers.image.openai_image import OPENAI_IMAGE_COST_USD, OpenAIImageProvider
from yt_video.providers.image.placeholder import PlaceholderProvider
from yt_video.providers.image.pollinations import PollinationsProvider

FIXTURE = Path(__file__).parent.parent / "fixtures" / "cloudflare" / "flux_schnell_success.json"


class _Resp:
    def __init__(self, content=b"", headers=None, payload=None):
        self.content = content
        self.headers = headers or {}
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_chain_skips_unavailable_and_falls_through(tmp_path):
    cf = CloudflareProvider(None, None)
    poll = FakeImageProvider(name="pollinations", fail=True)
    chain = ImageChain([cf, poll, PlaceholderProvider()], tmp_path / "cache", sleep=lambda s: None)
    result = chain.generate("a bull", 320, 180, 1, tmp_path / "out" / "img.jpg")
    assert result.provider == "placeholder"
    assert not result.cached
    assert poll.calls == 3
    assert Image.open(result.path).size == (320, 180)
    assert not (tmp_path / "cache").exists() or not list((tmp_path / "cache").iterdir())


def test_chain_cache_hit(tmp_path):
    fake = FakeImageProvider()
    chain = ImageChain([fake], tmp_path / "cache", sleep=lambda s: None)
    first = chain.generate("p", 64, 64, 3, tmp_path / "a.png")
    second = chain.generate("p", 64, 64, 3, tmp_path / "b.png")
    assert not first.cached and second.cached
    assert fake.calls == 1
    assert (tmp_path / "b.png").read_bytes() == (tmp_path / "a.png").read_bytes()


def test_chain_raises_when_all_fail(tmp_path):
    chain = ImageChain([FakeImageProvider(fail=True)], tmp_path / "cache", sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        chain.generate("p", 10, 10, 1, tmp_path / "x.png")


def test_pollinations_url_has_seed_size_nologo():
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"], seen["headers"] = url, headers
        return _Resp(b"\xff\xd8img", {"Content-Type": "image/jpeg"})

    data = PollinationsProvider("flux", "k", http_get=fake_get).generate("hello world", 1280, 720, 7)
    assert data == b"\xff\xd8img"
    for part in ("width=1280", "height=720", "seed=7", "nologo=true", "model=flux", "hello%20world"):
        assert part in seen["url"]
    assert seen["headers"] == {"Authorization": "Bearer k"}


def test_pollinations_network_error_returns_none():
    def boom(url, headers=None, timeout=None):
        raise ConnectionError("down")

    assert PollinationsProvider("flux", None, http_get=boom).generate("p", 10, 10, 1) is None


def test_cloudflare_decodes_fixture_and_resizes():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return _Resp(payload=payload)

    provider = CloudflareProvider("acct", "tok", http_post=fake_post)
    data = provider.generate("a bull", 1280, 720, 7)
    assert Image.open(io.BytesIO(data)).size == (1280, 720)
    assert seen["url"].endswith("/accounts/acct/ai/run/@cf/black-forest-labs/flux-1-schnell")
    assert seen["body"] == {"prompt": "a bull", "steps": 4}
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_cloudflare_error_returns_none():
    def boom(url, headers=None, json=None, timeout=None):
        raise ConnectionError("down")

    assert CloudflareProvider("a", "t", http_post=boom).generate("p", 10, 10, 1) is None


def test_cloudflare_unavailable_without_creds():
    assert not CloudflareProvider(None, None).available()
    assert not CloudflareProvider("a", None).available()
    assert CloudflareProvider("a", "t").available()


def test_build_chain_default_order_and_openai_opt_in(settings):
    names = [p.name for p in build_image_chain(settings).providers]
    assert names == ["pollinations", "cloudflare", "placeholder"]
    settings.image_providers = ["openai", "pollinations"]
    assert [p.name for p in build_image_chain(settings).providers] == ["openai", "pollinations"]


def test_build_chain_unknown_name_raises(settings):
    settings.image_providers = ["nope"]
    with pytest.raises(ValueError):
        build_image_chain(settings)


def test_openai_image_records_cost(tmp_path, monkeypatch):
    b64 = base64.b64encode(b"imgbytes").decode()

    class _Images:
        def generate(self, **kw):
            assert kw["size"] == "1536x1024"
            return SimpleNamespace(data=[SimpleNamespace(b64_json=b64, url=None)])

    class _Client:
        def __init__(self, api_key):
            self.images = _Images()

    monkeypatch.setattr("openai.OpenAI", _Client)
    ledger = CostLedger(tmp_path / "c.db")
    provider = OpenAIImageProvider("key", "gpt-image-1", "low", ledger=ledger, run_id="r1")
    assert provider.generate("p", 1280, 720, 1) == b"imgbytes"
    (row,) = ledger.run_rows("r1")
    assert row["kind"] == "image" and row["provider"] == "openai"
    assert row["cost_usd"] == OPENAI_IMAGE_COST_USD


def test_openai_image_unavailable_without_key():
    assert not OpenAIImageProvider(None, "gpt-image-1", "low").available()


class _Scripted:
    """Provider returning a scripted sequence of byte payloads."""

    name, model = "scripted", "m"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def available(self):
        return True

    def generate(self, prompt, width, height, seed):
        self.calls += 1
        return self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]


def _uniform_png():
    buf = io.BytesIO()
    Image.new("RGB", (128, 128), (0, 0, 0)).save(buf, "PNG")
    return buf.getvalue() + bytes(4096)  # padded past the min-bytes check


def _good_png(w=64, h=64):
    return FakeImageProvider().generate("p", w, h, 1)


@pytest.mark.parametrize("bad", [b"tiny", b"x" * 5000, _uniform_png()], ids=["tiny", "undecodable", "uniform"])
def test_invalid_output_falls_through_and_is_not_cached(tmp_path, bad):
    sleeps = []
    bad_provider = _Scripted([bad])
    good = FakeImageProvider(name="good")
    chain = ImageChain([bad_provider, good], tmp_path / "cache", sleep=sleeps.append)
    result = chain.generate("p", 64, 64, 1, tmp_path / "o.png")
    assert result.provider == "good"
    assert bad_provider.calls == 3
    assert sleeps == [5, 10]
    assert len(list((tmp_path / "cache").iterdir())) == 1  # only the good provider's entry


def test_invalid_then_valid_retries_once(tmp_path):
    sleeps = []
    p = _Scripted([b"tiny", _good_png()])
    chain = ImageChain([p], tmp_path / "cache", sleep=sleeps.append)
    result = chain.generate("p", 64, 64, 1, tmp_path / "o.png")
    assert result.provider == "scripted" and p.calls == 2 and sleeps == [5]


def test_invalid_cache_entry_is_deleted_and_refetched(tmp_path):
    p = _Scripted([_good_png()])
    chain = ImageChain([p], tmp_path / "cache", sleep=lambda s: None)
    key = chain._key(p, "p", 64, 64, 1)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / f"{key}.img").write_bytes(b"junk")
    result = chain.generate("p", 64, 64, 1, tmp_path / "o.png")
    assert not result.cached and p.calls == 1
    assert (tmp_path / "cache" / f"{key}.img").read_bytes() == _good_png()
    assert not list((tmp_path / "cache").glob("*.tmp"))
