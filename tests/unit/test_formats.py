"""Phase C format picker heuristics (ported from test_format_service.py)."""

from datetime import date

from yt_video.domain.formats import format_prompt_block, get_format, list_formats, pick_format


def _day(weekday: int) -> date:
    """2026-11-23 is a Monday, so weekday N is that date plus N days."""
    return date(2026, 11, 23 + weekday)


def test_list_formats_has_five():
    formats = list_formats()
    assert len(formats) == 5
    ids = {f.id for f in formats}
    assert ids == {
        "trend_vs_reality",
        "what_changed",
        "one_thing",
        "numbers",
        "comeback",
    }


def test_pick_format_friday_what_changed():
    spec = pick_format("Market moves", angle="daily", today=_day(4))
    assert spec.id == "what_changed"


def test_pick_format_week_keyword():
    spec = pick_format("What changed this week in AI", today=_day(1))
    assert spec.id == "what_changed"


def test_pick_format_digits_numbers():
    spec = pick_format("UPI hits 15 billion", today=_day(1))
    assert spec.id == "numbers"


def test_pick_format_explain_one_thing():
    spec = pick_format("How to read RBI policy", angle="explained", today=_day(2))
    assert spec.id == "one_thing"


def test_pick_format_comeback():
    spec = pick_format("Kohli's batting comeback story", today=_day(2))
    assert spec.id == "comeback"


def test_pick_format_default_trend_vs_reality():
    spec = pick_format("Viral gadget claim", angle="hype check", today=_day(1))
    assert spec.id == "trend_vs_reality"


def test_format_prompt_block_contains_beat_sheet():
    spec = get_format("numbers")
    block = format_prompt_block(spec)
    assert "SHOW FORMAT" in block
    assert "numbers" in block
    assert "Beat sheet" in block


def test_pick_format_takes_explicit_date():
    assert date(2026, 11, 27).weekday() == 4  # Friday
    assert pick_format("Viral gadget claim", today=date(2026, 11, 27)).id == "what_changed"
