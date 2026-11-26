import json
from pathlib import Path

from yt_video.migrate import migrate_state, needs_migration

SQLITE = b"SQLite format 3\x00" + b"\x00" * 100


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "output").mkdir(parents=True)
    return repo, tmp_path / "state"


def test_copies_and_keeps_source(tmp_path):
    repo, state = _dirs(tmp_path)
    src = repo / "output" / "tts_style_memory.json"
    src.write_text(json.dumps({"a": 1}), encoding="utf-8")
    report = migrate_state(state, repo)
    assert report.copied == ["tts_style_memory.json"]
    assert (state / "tts_style_memory.json").read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert src.exists()


def test_costs_db_legacy_parent_path(tmp_path):
    repo, state = _dirs(tmp_path)
    (tmp_path / "output").mkdir()
    import sqlite3

    legacy = tmp_path / "output" / "costs.db"
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    report = migrate_state(state, repo)
    assert "costs.db" in report.copied
    assert (state / "costs.db").read_bytes()[:16] == SQLITE[:16]


def test_existing_target_not_overwritten(tmp_path):
    repo, state = _dirs(tmp_path)
    state.mkdir()
    (state / "costs.db").write_bytes(b"keep")
    (repo / "output" / "costs.db").write_bytes(SQLITE)
    report = migrate_state(state, repo)
    assert "costs.db" in report.skipped_exists
    assert (state / "costs.db").read_bytes() == b"keep"


def test_invalid_json_skipped_with_name(tmp_path):
    repo, state = _dirs(tmp_path)
    (repo / "output" / "review_log.json").write_text("{bad", encoding="utf-8")
    report = migrate_state(state, repo)
    assert report.skipped_invalid == ["review_log.json"]
    assert not (state / "review_log.json").exists()


def test_invalid_first_source_does_not_fall_through(tmp_path):
    repo, state = _dirs(tmp_path)
    (repo / "output" / "costs.db").write_bytes(b"garbage")
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "costs.db").write_bytes(SQLITE)
    report = migrate_state(state, repo)
    assert report.skipped_invalid == ["costs.db"]
    assert not (state / "costs.db").exists()


def test_missing_sources_silent(tmp_path):
    repo, state = _dirs(tmp_path)
    report = migrate_state(state, repo)
    assert report.copied == []
    assert sorted(report.skipped_missing) == sorted(
        ["tts_style_memory.json", "costs.db", "review_log.json", "analytics_log.json", "x_post_log.json"]
    )


def test_idempotent(tmp_path):
    repo, state = _dirs(tmp_path)
    (repo / "output" / "x_post_log.json").write_text("[]", encoding="utf-8")
    assert migrate_state(state, repo).copied == ["x_post_log.json"]
    second = migrate_state(state, repo)
    assert second.copied == []
    assert second.skipped_exists == ["x_post_log.json"]


def test_needs_migration(tmp_path):
    state = tmp_path / "state"
    assert needs_migration(state)
    state.mkdir()
    (state / "costs.db").write_bytes(SQLITE)
    assert not needs_migration(state)


def test_copy_failure_recorded_no_partial(tmp_path, monkeypatch):
    repo, state = _dirs(tmp_path)
    (repo / "output" / "review_log.json").write_text("[]", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("locked")

    monkeypatch.setattr("yt_video.migrate.shutil.copy2", boom)
    report = migrate_state(state, repo)
    assert report.failed == ["review_log.json"]
    assert report.copied == []
    assert not (state / "review_log.json").exists()
    assert list(state.glob("*.tmp")) == []


def test_db_copy_includes_wal_rows(tmp_path):
    import sqlite3

    repo, state = _dirs(tmp_path)
    src = repo / "output" / "costs.db"
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    try:
        assert (repo / "output" / "costs.db-wal").exists()
        report = migrate_state(state, repo)
        assert report.copied == ["costs.db"]
    finally:
        conn.close()
    out = sqlite3.connect(state / "costs.db")
    try:
        assert out.execute("SELECT x FROM t").fetchall() == [(42,)]
    finally:
        out.close()
    assert list(state.glob("*.tmp")) == []


def test_ndjson_review_log_is_copied(tmp_path):
    repo, state = _dirs(tmp_path)
    lines = [json.dumps({"topic": f"t{i}", "score": i, "review": "ok"}) for i in range(3)]
    (repo / "output" / "review_log.json").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = migrate_state(state, repo)
    assert report.copied == ["review_log.json"]
    assert report.skipped_invalid == []
    assert (state / "review_log.json").read_text(encoding="utf-8").count("\n") == 3


def test_empty_review_log_is_valid(tmp_path):
    repo, state = _dirs(tmp_path)
    (repo / "output" / "review_log.json").write_text("\n\n", encoding="utf-8")
    assert migrate_state(state, repo).copied == ["review_log.json"]
