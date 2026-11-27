from importlib.metadata import version


def test_edge_tts_is_at_least_7_3_1():
    """7.2.x gets HTTP 403 from the Edge TTS endpoint."""
    parts = tuple(int(p) for p in version("edge-tts").split(".")[:3])
    assert parts >= (7, 3, 1)
