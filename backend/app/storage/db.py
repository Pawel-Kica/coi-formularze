"""The one SQLite file (HISTORY_DB) both stores keep their tables in."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager

from app.core.config import settings


@contextmanager
def connect(schema: str) -> Iterator[sqlite3.Connection]:
    """One connection per call (commit on success), the store's table created on first use."""
    with closing(sqlite3.connect(settings.history_db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(schema)
        with conn:
            yield conn
