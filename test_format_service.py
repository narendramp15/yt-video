"""Phase C format picker heuristics."""

from services.format_service import pick_format, format_prompt_block, get_format, list_formats


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
    spec = pick_format("Market moves", angle="daily", weekday=4)
    assert spec.id == "what_changed"


def test_pick_format_week_keyword():
    spec = pick_format("What changed this week in AI", weekday=1)
    assert spec.id == "what_changed"


def test_pick_format_digits_numbers():
    spec = pick_format("UPI hits 15 billion", weekday=1)
    assert spec.id == "numbers"


def test_pick_format_explain_one_thing():
    spec = pick_format("How to read RBI policy", angle="explained", weekday=2)
    assert spec.id == "one_thing"


def test_pick_format_comeback():
    spec = pick_format("Kohli's batting comeback story", weekday=2)
    assert spec.id == "comeback"


def test_pick_format_default_trend_vs_reality():
    spec = pick_format("Viral gadget claim", angle="hype check", weekday=1)
    assert spec.id == "trend_vs_reality"


def test_format_prompt_block_contains_beat_sheet():
    spec = get_format("numbers")
    block = format_prompt_block(spec)
    assert "SHOW FORMAT" in block
    assert "numbers" in block
    assert "Beat sheet" in block


if __name__ == "__main__":
    test_list_formats_has_five()
    test_pick_format_friday_what_changed()
    test_pick_format_week_keyword()
    test_pick_format_digits_numbers()
    test_pick_format_explain_one_thing()
    test_pick_format_comeback()
    test_pick_format_default_trend_vs_reality()
    test_format_prompt_block_contains_beat_sheet()
    print("format_service tests passed")
