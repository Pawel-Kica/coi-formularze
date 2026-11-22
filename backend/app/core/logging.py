"""Logging of the app's own loggers (`app.*`): one stderr handler, LOG_LEVEL, and the file being worked on.

Uvicorn configures only its own loggers, so without `setup` the app's records reach Python's last-resort handler
(warnings as bare text, info dropped). Every line names the uploaded file it belongs to, set once per use case with
`file_context` and carried into worker threads with `bound`:

    2026-11-22 10:15:03 INFO    app.llm.client [wniosek.pdf] model call: deepinfra/Qwen/... 6.2 s, 1630+412 tokens, stop

Never log secrets or form contents: file names, sizes, counts and timings only.
"""

import contextvars
import logging
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager

FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(file)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_file: contextvars.ContextVar[str] = contextvars.ContextVar("log_file", default="-")


class _FileFilter(logging.Filter):
    """Adds `record.file`: the file of the current use case, "-" outside one."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.file = _file.get()
        return True


class _Handler(logging.StreamHandler):
    """The app's handler, a class of its own so `setup` can tell it apart and stay idempotent (uvicorn --reload)."""


def setup(level: str = "INFO") -> None:
    """Send `app.*` records to stderr at `level`. Records still propagate, so pytest's caplog sees them; the root
    logger has no handler under uvicorn, so nothing prints twice."""
    logger = logging.getLogger("app")
    logger.setLevel(level.upper())
    if any(isinstance(h, _Handler) for h in logger.handlers):
        return
    handler = _Handler(sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    handler.addFilter(_FileFilter())
    logger.addHandler(handler)


@contextmanager
def file_context(filename: str) -> Iterator[None]:
    """Every record logged inside (in this thread, or in threads started through `bound`) names `filename`."""
    token = _file.set(filename or "-")
    try:
        yield
    finally:
        _file.reset(token)


def bound[T](fn: Callable[..., T]) -> Callable[..., T]:
    """`fn` run in a copy of the caller's context (the file name), for a worker thread. One call per copy: bind
    again for every task, a context cannot be entered by two threads at once."""
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.run(fn, *args, **kwargs)
