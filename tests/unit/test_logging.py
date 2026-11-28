from yt_video.logging import add_run_file_handler, get_logger


def test_run_file_handler_writes_utf8(tmp_path):
    log = tmp_path / "sub" / "run.log"
    h = add_run_file_handler(log)
    try:
        get_logger().info("héllo")
        h.flush()
    finally:
        get_logger().removeHandler(h)
        h.close()
    assert "héllo" in log.read_text(encoding="utf-8")


def test_module_loggers_reach_run_file(tmp_path):
    """Records from get_logger(__name__) in any yt_video module land in the run log."""
    log = tmp_path / "run.log"
    h = add_run_file_handler(log)
    try:
        get_logger("yt_video.providers.llm.router").warning("fallback seen")
        h.flush()
    finally:
        get_logger().removeHandler(h)
        h.close()
    assert "fallback seen" in log.read_text(encoding="utf-8")


def test_stdlib_modules_use_app_logger():
    """Modules must not use logging.getLogger directly; their records would miss pipeline.log."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "yt_video"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.name != "logging.py" and "logging.getLogger(" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
