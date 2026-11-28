"""Scene alignment."""

from yt_video.domain.sync import align_scenes

SCRIPT = (
    "[SCENE:intro]\nWelcome to the show about markets and money today.\n"
    "[SCENE:s1]\nReliance announced a massive investment in artificial intelligence chips.\n"
    "[SCENE:s2]\nAnalysts say this changes the competitive landscape for everyone involved.\n"
)


def _words() -> list[dict]:
    narration: list[str] = []
    for line in SCRIPT.splitlines():
        if not line.startswith("[SCENE:"):
            narration.extend(line.split())
    return [
        {"word": tok, "start": round(i * 0.5, 2), "end": round(i * 0.5 + 0.4, 2)}
        for i, tok in enumerate(narration)
    ]


def test_align_scenes_monotonic():
    starts = align_scenes(_words(), SCRIPT, 20.0)
    assert list(starts) == ["intro", "s1", "s2"]
    values = list(starts.values())
    assert values[0] == 0.0
    assert all(a < b for a, b in zip(values, values[1:]))


def test_align_scenes_falls_back_to_proportional_without_words():
    starts = align_scenes([], SCRIPT, 20.0)
    values = list(starts.values())
    assert values[0] == 0.0
    assert all(a < b for a, b in zip(values, values[1:]))
    assert values[-1] < 20.0


def test_align_scenes_no_markers_is_empty():
    assert align_scenes([], "just narration", 10.0) == {}
