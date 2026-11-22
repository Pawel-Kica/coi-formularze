"""App logging (app/core/logging.py): every line names the file it belongs to, also from worker threads."""

import io
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import logging as app_logging
from app.core.logging import bound, file_context
from app.services import pipeline


@pytest.fixture
def lines(monkeypatch):
    """What the app's handler writes, as text."""
    out = io.StringIO()
    app_logging.setup("INFO")
    handler = next(h for h in logging.getLogger("app").handlers if isinstance(h, app_logging._Handler))
    monkeypatch.setattr(handler, "stream", out)
    return out


def test_setup_is_idempotent():
    app_logging.setup("INFO")
    app_logging.setup("DEBUG")
    logger = logging.getLogger("app")
    assert sum(isinstance(h, app_logging._Handler) for h in logger.handlers) == 1
    assert logger.level == logging.DEBUG
    app_logging.setup("INFO")


def test_lines_name_the_file_also_in_worker_threads(lines):
    log = logging.getLogger("app.test")
    log.info("outside")
    with file_context("wniosek.pdf"), ThreadPoolExecutor(2) as pool:
        log.info("inside")
        for f in [pool.submit(bound(log.info), "page %d", n) for n in (1, 2)]:
            f.result()
    text = lines.getvalue()
    assert "INFO    app.test [-] outside" in text
    assert "[wniosek.pdf] inside" in text and "[wniosek.pdf] page 1" in text and "[wniosek.pdf] page 2" in text


def test_failed_conversion_is_logged_with_the_file(lines, monkeypatch):
    def unreadable(data, filename):
        raise ValueError("no pages to read")

    monkeypatch.setattr(pipeline, "transcribe", unreadable)
    with pytest.raises(ValueError):
        pipeline.convert(b"%PDF-", "zly.pdf")
    text = lines.getvalue()
    assert "[zly.pdf] converting zly.pdf, 0 KB" in text
    assert "WARNING app.services.pipeline [zly.pdf] conversion failed after" in text and "ValueError: no pages" in text
