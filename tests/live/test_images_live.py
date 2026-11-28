import pytest

from yt_video.providers.image.pollinations import PollinationsProvider

pytestmark = pytest.mark.live


def test_pollinations_returns_jpeg():
    data = PollinationsProvider("flux", None).generate("a red apple on a table", 256, 256, 1)
    assert data is not None and data.startswith(b"\xff\xd8")
