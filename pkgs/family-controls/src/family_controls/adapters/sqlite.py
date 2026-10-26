"""Transactional state and operation receipts, preserving the original database."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from ..domain import Request, State, Unavailable, Window, midnight_after, minute


class Repository:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            self.db.close()
            raise RuntimeError("Database was created by a newer family-controls version")
        if (
            version < 2
            and self.db.execute("SELECT name FROM sqlite_master WHERE name='state'").fetchone()
        ):
            backup = Path(path + ".before-v2")
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as destination:
                    self.db.backup(destination)
                backup.chmod(0o600)
        self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS state (
                user TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(user, key));
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                user TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, user TEXT NOT NULL, fingerprint TEXT NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL, outcome TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS applied (
                user TEXT PRIMARY KEY, revision INTEGER NOT NULL, day TEXT NOT NULL,
                updated_at TEXT NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS relay_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=2;
            COMMIT;
        """)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
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

    def load(self, user: str, now: datetime) -> State:
        try:
            return self._load(user, now)
        except (ValueError, TypeError, KeyError) as exc:
            raise Unavailable("Stored policy needs recovery; original data preserved") from exc

    def _load(self, user: str, now: datetime) -> State:
        with self.lock:
            values = dict(self.db.execute("SELECT key,value FROM state WHERE user=?", (user,)))
        if "policy_v2" in values:
            data = json.loads(values["policy_v2"])
            data["extra_windows"] = [Window(**item) for item in data["extra_windows"]]
            if data["request"]:
                data["request"] = Request(**data["request"])
            return State(**data).today(now)
        # Read legacy data without deleting it or resetting Timekpr's usage balances.
        overlay = json.loads(values.get("effective_overlay", "{}"))
        state = State(
            day=overlay.get("date", ""),
            delta_seconds=int(overlay.get("capMinutes", 0)) * 60,
            until=minute(overlay["until"]) * 60 if overlay.get("until") else None,
            blocked_until=values.get("blocked_until_date"),
            muted="requests_muted" in values,
        )
        if raw := values.get("pending_request"):
            created = datetime.fromisoformat(json.loads(raw)["requestedAt"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=now.tzinfo)
            state.request = Request(
                str(uuid5(NAMESPACE_URL, user + raw)),
                created.isoformat(),
                midnight_after(created).isoformat(),
            )
            state.last_request_at = created.isoformat()
        return state.today(now)

    def save(self, user: str, state: State) -> None:
        self.db.execute(
            "INSERT INTO state VALUES(?, 'policy_v2', ?) ON CONFLICT(user,key) "
            "DO UPDATE SET value=excluded.value",
            (user, json.dumps(asdict(state))),
        )

    def operation(self, operation: str) -> tuple[str, str] | None:
        with self.lock:
            return self.db.execute(
                "SELECT fingerprint,outcome FROM operations WHERE id=?", (operation,)
            ).fetchone()

    def record(
        self, operation: str, user: str, fingerprint: str, revision: int, now: datetime
    ) -> None:
        self.db.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?)",
            (operation, user, fingerprint, revision, now.isoformat(), "pending"),
        )

    def audit(self, user: str, action: str, details: dict, now: datetime) -> None:
        self.db.execute(
            "INSERT INTO audit_log(created_at,user,action,details) VALUES(?,?,?,?)",
            (now.isoformat(), user, action, json.dumps(details, sort_keys=True)),
        )

    def applied(self, user: str, revision: int, now: datetime, error: str | None) -> None:
        with self.transaction():
            self.db.execute(
                "INSERT INTO applied VALUES(?,?,?,?,?) ON CONFLICT(user) DO UPDATE SET "
                "revision=excluded.revision,day=excluded.day,updated_at=excluded.updated_at,error=excluded.error",
                (user, revision, now.date().isoformat(), now.isoformat(), error),
            )
            if error is None:
                self.db.execute(
                    "UPDATE operations SET outcome=CASE WHEN revision=? THEN 'applied' "
                    "ELSE 'superseded' END WHERE user=? AND revision<=? AND outcome='pending'",
                    (revision, user, revision),
                )

    def application_status(self, user: str) -> dict:
        with self.lock:
            row = self.db.execute(
                "SELECT revision,day,updated_at,error FROM applied WHERE user=?", (user,)
            ).fetchone()
        return dict(zip(("revision", "day", "updatedAt", "error"), row, strict=True)) if row else {}

    def history(self, user: str, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT a.created_at,a.action,a.details,o.outcome FROM audit_log a "
                "LEFT JOIN operations o ON o.id=json_extract(a.details,'$.operationId') "
                "WHERE a.user=? ORDER BY a.id DESC LIMIT ?",
                (user, limit),
            ).fetchall()
        return [
            {"at": at, "action": action, "details": json.loads(details), "outcome": outcome}
            for at, action, details, outcome in rows
        ]

    def prune(self, now: datetime, days: int) -> None:
        # Operation IDs remain as compact tombstones: pruning them permits old retries to reapply.
        with self.transaction():
            self.db.execute(
                "DELETE FROM audit_log WHERE created_at < ?",
                ((now - timedelta(days=days)).isoformat(),),
            )

    def prune_relay(self, now: datetime) -> None:
        with self.transaction():
            self.db.execute(
                "DELETE FROM relay_state WHERE json_extract(value,'$.expires') < ?",
                (now.isoformat(),),
            )
            self.db.execute(
                "DELETE FROM relay_state WHERE key LIKE 'button-actor:%' AND NOT EXISTS "
                "(SELECT 1 FROM relay_state b WHERE b.key='button:' || substr(relay_state.key,14))"
            )

    def relay_get(self, key: str, default: object = None) -> object:
        with self.lock:
            row = self.db.execute("SELECT value FROM relay_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def relay_items(self, prefix: str) -> list[tuple[str, dict]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT key,value FROM relay_state WHERE substr(key,1,?)=? ORDER BY rowid",
                (len(prefix), prefix),
            ).fetchall()
        return [(key, json.loads(value)) for key, value in rows]

    def relay_set(self, key: str, value: object) -> None:
        with self.transaction():
            if value is None:
                self.db.execute("DELETE FROM relay_state WHERE key=?", (key,))
            else:
                self.db.execute(
                    "INSERT INTO relay_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )

    def claim(self, key: str, value: str) -> str:
        """Remember the first authenticated actor of a shared button atomically."""
        with self.transaction():
            self.db.execute(
                "INSERT INTO relay_state VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                (key, json.dumps(value)),
            )
            return json.loads(
                self.db.execute("SELECT value FROM relay_state WHERE key=?", (key,)).fetchone()[0]
            )

    def close(self) -> None:
        self.db.close()
