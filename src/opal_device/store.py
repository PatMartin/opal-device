"""SQLite persistence: single shared connection serialized through a lock.

Observed inventories and remote caches live in SQLite; human-managed
YAML seeds are applied on top. `write_txn()` is the unit-of-work context
manager for atomic check-and-mutate; it nests via SAVEPOINT.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import constants

_REVISION_KEY = "config_revision"


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._txn_depth = 0
        self._savepoint_seq = 0
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    # -- schema -----------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta ("
                "  key TEXT PRIMARY KEY,"
                "  value TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, '0')",
                (_REVISION_KEY,),
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS settings ("
                "  key TEXT PRIMARY KEY,"
                "  value TEXT NOT NULL,"
                "  updated_at TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS devices ("
                "  id TEXT PRIMARY KEY,"
                "  qualified_name TEXT NOT NULL UNIQUE,"
                "  name TEXT NOT NULL,"
                "  host_id TEXT NOT NULL,"
                "  host_name TEXT NOT NULL,"
                "  type TEXT NOT NULL,"
                "  backend TEXT NOT NULL,"
                "  status TEXT NOT NULL,"
                "  origin_peer_id TEXT,"
                "  doc TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS plugin_state ("
                "  id TEXT PRIMARY KEY,"
                "  enabled INTEGER NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  event TEXT NOT NULL,"
                "  device_id TEXT,"
                "  peer_id TEXT,"
                "  data TEXT NOT NULL,"
                "  time TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS defaults ("
                "  scope TEXT NOT NULL,"
                "  target TEXT NOT NULL,"
                "  value_map TEXT NOT NULL,"
                "  PRIMARY KEY (scope, target)"
                ")"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS groups ("
                "  id TEXT PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  mode TEXT NOT NULL,"
                "  doc TEXT NOT NULL"
                ")"
            )

    # -- helpers ----------------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @contextmanager
    def write_txn(self) -> Iterator[None]:
        """Atomic write transaction; nests via SAVEPOINT."""
        with self._lock:
            if self._txn_depth == 0:
                self._conn.execute("BEGIN IMMEDIATE")
                self._txn_depth = 1
                try:
                    yield
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                else:
                    self._conn.execute("COMMIT")
                finally:
                    self._txn_depth = 0
            else:
                self._savepoint_seq += 1
                name = f"sp_{self._savepoint_seq}"
                self._conn.execute(f"SAVEPOINT {name}")
                self._txn_depth += 1
                try:
                    yield
                except Exception:
                    self._conn.execute(f"ROLLBACK TO {name}")
                    self._conn.execute(f"RELEASE {name}")
                    raise
                else:
                    self._conn.execute(f"RELEASE {name}")
                finally:
                    self._txn_depth -= 1

    # -- config revision / ETag -------------------------------------------

    def revision(self) -> int:
        row = self.fetchone(
            "SELECT value FROM meta WHERE key = ?", (_REVISION_KEY,)
        )
        return int(row["value"]) if row else 0

    def bump_revision(self) -> int:
        """Increment the global config revision; call inside write_txn."""
        rev = self.revision() + 1
        self.execute(
            "UPDATE meta SET value = ? WHERE key = ?",
            (str(rev), _REVISION_KEY),
        )
        return rev

    def etag(self) -> str:
        return f'"cfg-{self.revision()}"'

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def default_db_path(data_dir: str | Path | None = None) -> str:
    if data_dir is None:
        data_dir = os.environ.get(constants.ENV_DATA_DIR, constants.DEFAULT_DATA_DIR)
    if str(data_dir) == ":memory:":
        return ":memory:"
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return str(root / constants.DB_FILENAME)
