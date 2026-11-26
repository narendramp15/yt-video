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
