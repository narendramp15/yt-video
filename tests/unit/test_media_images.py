"""Scene image / thumbnail tests: fake provider chain, stub CLIP verifier."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

from yt_video.media.clip import ClipVerifier
from yt_video.media.images import generate_scene_images, generate_thumbnail
from yt_video.providers.image.chain import ImageChain
from yt_video.providers.image.fake import FakeImageProvider
from yt_video.providers.image.validate import validate_image
from yt_video.media.render import fill_missing_scene_images


class SeedRecordingProvider(FakeImageProvider):
    def __init__(self) -> None:
        super().__init__()
        self.seeds: list[int] = []
        self.prompts: list[str] = []

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        self.seeds.append(seed)
        self.prompts.append(prompt)
        return super().generate(prompt, width, height, seed)


class StubVerifier:
    """Duck-typed ClipVerifier returning scripted scores."""

    enabled = True
    min_score = 0.24

    def __init__(self, scores: list[float]) -> None:
        self._scores = list(scores)
        self.recorded: list[float] = []

    def score(self, image_path: Path, prompt: str) -> float | None:
        return self._scores.pop(0)

    def record(self, score: float) -> None:
        self.recorded.append(score)

    def reset(self) -> None:
        self.recorded.clear()

    def summary(self) -> str:
        return "stub"


def _chain(tmp_path: Path) -> tuple[ImageChain, SeedRecordingProvider]:
    provider = SeedRecordingProvider()
    return ImageChain([provider], tmp_path / "cache", sleep=lambda s: None), provider


def _disabled() -> ClipVerifier:
    return ClipVerifier(False, 0.24, "unused")


def test_scene_images_one_per_label_distinct_seeds(tmp_path):
    chain, provider = _chain(tmp_path)
    labels = ["intro", "s1", "outro"]
    out = generate_scene_images(
        chain, _disabled(), labels, "Volcanoes", "how they form", {}, {}, tmp_path / "imgs"
    )
    assert set(out) == set(labels)
    for label, path in out.items():
        assert Path(path) == tmp_path / "imgs" / f"{label}_scene.jpg"
        assert Path(path).is_file()
    assert len(provider.seeds) == 3
    assert len(set(provider.seeds)) == 3
    assert not list((tmp_path / "imgs").glob("*.try*"))


def test_missing_prompt_override_falls_back_to_topic(tmp_path):
    chain, provider = _chain(tmp_path)
    out = generate_scene_images(
        chain, _disabled(), ["intro", "s1"], "Volcanoes", "", {"s1": "narration."},
        {"intro": "lava flow at dusk"}, tmp_path,
    )
    assert set(out) == {"intro", "s1"}
    assert provider.prompts[0].startswith("lava flow at dusk.")
    assert provider.prompts[1].startswith("narration.")
    out2 = generate_scene_images(chain, _disabled(), ["s9"], "Volcanoes", "", {}, {}, tmp_path)
    assert "s9" in out2 and provider.prompts[-1].startswith("Volcanoes.")


def test_low_clip_score_regenerates_once(tmp_path):
    chain, provider = _chain(tmp_path)
    verifier = StubVerifier([0.1, 0.9])
    out = generate_scene_images(chain, verifier, ["s1"], "Volcanoes", "", {}, {}, tmp_path)
    assert "s1" in out
    assert provider.calls == 2
    assert provider.seeds[1] - provider.seeds[0] == 1009
    assert verifier.recorded == [0.9]


def test_thumbnail_has_hook_text_overlay(tmp_path):
    chain, provider = _chain(tmp_path)
    out = generate_thumbnail(
        chain, "Why Volcanoes Erupt", "Volcanoes", "", "ERUPTION ALERT", tmp_path / "thumb.jpg"
    )
    assert out == tmp_path / "thumb.jpg"
    with Image.open(out) as img:
        assert img.size == (1280, 720)
        composed = img.convert("RGB")
    raw = Image.open(
        chain.generate("x", 1280, 720, provider.seeds[0], tmp_path / "raw.png").path
    ).convert("RGB")
    box = (0, 560, 1280, 720)  # lower third, where the overlay lands
    assert list(composed.crop(box).getdata()) != list(raw.crop(box).getdata())
    plain = generate_thumbnail(
        chain, "Why Volcanoes Erupt", "Volcanoes", "", "ERUPTION ALERT", tmp_path / "plain.jpg",
        text_enabled=False,
    )
    with Image.open(plain) as img:
        assert img.size == (1280, 720)


def test_disabled_verifier_scores_none_and_never_imports_torch():
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from yt_video.media.clip import ClipVerifier\n"
        "v = ClipVerifier(False, 0.24, 'm')\n"
        "assert v.score(Path('nope.png'), 'p') is None\n"
        "assert 'torch' not in sys.modules and 'transformers' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, encoding="utf-8")


def test_clip_summary_text():
    v = ClipVerifier(True, 0.24, "m")
    assert v.summary() == "CLIP accuracy: not measured (verification off or backend missing)"
    v.record(0.30)
    v.record(0.10)
    assert v.summary() == (
        "CLIP scene accuracy: mean 0.200 over 2 scenes, 1/2 (50%) cleared threshold 0.24"
    )


def test_render_module_does_not_import_moviepy_at_module_level():
    code = (
        "import sys\n"
        "import yt_video.media.render\n"
        "assert 'moviepy' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, encoding="utf-8")


def test_fake_provider_output_passes_validation_at_full_size():
    fake = FakeImageProvider()
    for size in ((1920, 1080), (1280, 720)):
        data = fake.generate("p", *size, 7)
        assert validate_image(data, *size) is None
        assert data == fake.generate("p", *size, 7)
        assert data != fake.generate("p", *size, 8)


def test_fill_missing_scene_images_uses_previous_then_following():
    labels = ["intro", "s1", "s2", "outro"]
    out = fill_missing_scene_images(labels, {"s1": "a.jpg", "outro": "b.jpg"})
    assert out == {"intro": "a.jpg", "s1": "a.jpg", "s2": "a.jpg", "outro": "b.jpg"}


def test_fill_missing_scene_images_no_images_raises():
    import pytest

    with pytest.raises(ValueError):
        fill_missing_scene_images(["intro"], {})
