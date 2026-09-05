"""SQLite connection management.

FastAPI runs the synchronous handlers in a worker thread pool, and a SQLite
connection object may not be shared across threads.  Rather than a global
connection guarded by a lock (which serialises every read), this module opens a
short-lived connection per unit of work.  For a single-elder device this is
cheap and it removes a whole class of race conditions.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # An in-memory database must keep one connection alive or it vanishes.
        self._shared: sqlite3.Connection | None = None
        if self.path == ':memory:':
            self._shared = self._configure(sqlite3.connect(':memory:', check_same_thread=False))

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> sqlite3.Connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA journal_mode = WAL')
        connection.execute('PRAGMA busy_timeout = 5000')
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success and rolls back on failure."""
        if self._shared is not None:
            try:
                yield self._shared
                self._shared.commit()
            except Exception:
                self._shared.rollback()
                raise
            return
        connection = self._configure(sqlite3.connect(self.path, check_same_thread=False,
                                                     timeout=5.0))
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None
