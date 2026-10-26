"""Atomic central state and durable, independently resumable event streams."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class BackendStore:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=5)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS documents (
                collection TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(collection,key));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, audience TEXT NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_audience ON events(audience,id);
            CREATE INDEX IF NOT EXISTS commands_pending_child
                ON documents(json_extract(value,'$.child'))
                WHERE collection='commands'
                    AND json_extract(value,'$.status') IN ('queued','accepted');
            CREATE INDEX IF NOT EXISTS commands_child
                ON documents(json_extract(value,'$.child')) WHERE collection='commands';
            CREATE INDEX IF NOT EXISTS usage_child_day ON documents(
                collection,json_extract(value,'$.child'),json_extract(value,'$.day'));
        """)
        Path(path).chmod(0o600)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            else:
                self.db.execute("COMMIT")

    def get(self, collection: str, key: str) -> dict | None:
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM documents WHERE collection=? AND key=?", (collection, key)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, collection: str, key: str, value: dict) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO documents VALUES(?,?,?) ON CONFLICT(collection,key) "
                "DO UPDATE SET value=excluded.value",
                (collection, key, json.dumps(value, sort_keys=True)),
            )

    def delete(self, collection: str, key: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM documents WHERE collection=? AND key=?", (collection, key))

    def items(self, collection: str) -> list[tuple[str, dict]]:
        with self.lock:
            return [
                (key, json.loads(value))
                for key, value in self.db.execute(
                    "SELECT key,value FROM documents WHERE collection=? ORDER BY rowid",
                    (collection,),
                )
            ]

    def event(self, audience: str, kind: str, data: dict, at: str) -> int:
        with self.lock:
            cursor = self.db.execute(
                "INSERT INTO events(audience,kind,data,created_at) VALUES(?,?,?,?)",
                (audience, kind, json.dumps(data), at),
            )
            return int(cursor.lastrowid or 0)

    def usage_range(self, collection: str, child: str, start: str, end: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT value FROM documents WHERE collection=? "
                "AND json_extract(value,'$.child')=? "
                "AND json_extract(value,'$.day') BETWEEN ? AND ? "
                "ORDER BY json_extract(value,'$.day'),key",
                (collection, child, start, end),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def pending_commands(self, child: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT value FROM documents WHERE collection='commands' "
                "AND json_extract(value,'$.child')=? "
                "AND json_extract(value,'$.status') IN ('queued','accepted') ORDER BY rowid",
                (child,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def command_history(self, child: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT value FROM documents WHERE collection='commands' "
                "AND json_extract(value,'$.child')=? ORDER BY rowid DESC LIMIT 100",
                (child,),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def events(self, audience: str, after: int, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,kind,data,created_at FROM events WHERE audience=? AND id>? "
                "ORDER BY id LIMIT ?",
                (audience, after, limit),
            ).fetchall()
        return [
            {"id": identifier, "kind": kind, "data": json.loads(data), "createdAt": at}
            for identifier, kind, data, at in rows
        ]

    def close(self) -> None:
        self.db.close()
