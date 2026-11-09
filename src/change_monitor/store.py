"""SQLite persistence: observation history and per-watch state."""

from __future__ import annotations

import csv
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watch      TEXT NOT NULL,
    ts         TEXT NOT NULL,
    status     TEXT NOT NULL,
    value      TEXT,
    value_num  REAL,
    hash       TEXT,
    error      TEXT,
    alerted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_observations_watch_ts ON observations (watch, ts);
CREATE TABLE IF NOT EXISTS watch_state (
    watch                 TEXT PRIMARY KEY,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    failing_alerted       INTEGER NOT NULL DEFAULT 0,
    triggered             INTEGER NOT NULL DEFAULT 0,
    last_checked          TEXT,
    last_alert            TEXT
);
"""

CSV_COLUMNS = ("id", "watch", "ts", "status", "value", "value_num", "alerted", "error")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Observation:
    """One check of one watch."""

    watch: str
    ts: datetime
    status: str  # "ok" | "error"
    value: str | None = None
    value_num: float | None = None
    hash: str | None = None
    error: str | None = None
    alerted: bool = False
    id: int | None = None

    @classmethod
    def ok(cls, watch: str, ts: datetime, value: str, value_num: float | None = None) -> "Observation":
        return cls(watch=watch, ts=ts, status="ok", value=value, value_num=value_num, hash=value_hash(value))

    @classmethod
    def failure(cls, watch: str, ts: datetime, error: str) -> "Observation":
        return cls(watch=watch, ts=ts, status="error", error=error)


@dataclass
class WatchState:
    """Mutable bookkeeping per watch."""

    watch: str
    consecutive_failures: int = 0
    failing_alerted: bool = False
    triggered: bool = False
    last_checked: datetime | None = None
    last_alert: datetime | None = None


class Store:
    """Thin wrapper around a SQLite database file (``":memory:"`` for tests)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- observations ---------------------------------------------------- #

    def record(self, observation: Observation) -> int:
        """Insert an observation and return its id."""
        cursor = self._conn.execute(
            "INSERT INTO observations (watch, ts, status, value, value_num, hash, error, alerted)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation.watch,
                _iso(observation.ts),
                observation.status,
                observation.value,
                observation.value_num,
                observation.hash,
                observation.error,
                int(observation.alerted),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def last_ok(self, watch: str) -> Observation | None:
        """Most recent successful observation for ``watch``."""
        row = self._conn.execute(
            "SELECT * FROM observations WHERE watch = ? AND status = 'ok' ORDER BY id DESC LIMIT 1",
            (watch,),
        ).fetchone()
        return _row_to_observation(row) if row else None

    def history(self, watch: str, limit: int = 20) -> list[Observation]:
        """Latest ``limit`` observations, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM observations WHERE watch = ? ORDER BY id DESC LIMIT ?",
            (watch, limit),
        ).fetchall()
        return [_row_to_observation(row) for row in rows]

    def iter_observations(self, watch: str | None = None) -> Iterator[Observation]:
        query = "SELECT * FROM observations"
        params: tuple = ()
        if watch is not None:
            query += " WHERE watch = ?"
            params = (watch,)
        for row in self._conn.execute(query + " ORDER BY id", params):
            yield _row_to_observation(row)

    def watch_names(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT watch FROM observations ORDER BY watch").fetchall()
        return [row["watch"] for row in rows]

    def export_csv(self, path: str | Path, watch: str | None = None) -> int:
        """Write observations to CSV; returns the number of rows."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            for obs in self.iter_observations(watch):
                writer.writerow(
                    [obs.id, obs.watch, _iso(obs.ts), obs.status, obs.value, obs.value_num, int(obs.alerted), obs.error]
                )
                count += 1
        return count

    # -- state ------------------------------------------------------------ #

    def state(self, watch: str) -> WatchState:
        row = self._conn.execute("SELECT * FROM watch_state WHERE watch = ?", (watch,)).fetchone()
        if row is None:
            return WatchState(watch=watch)
        return WatchState(
            watch=watch,
            consecutive_failures=row["consecutive_failures"],
            failing_alerted=bool(row["failing_alerted"]),
            triggered=bool(row["triggered"]),
            last_checked=_parse_ts(row["last_checked"]),
            last_alert=_parse_ts(row["last_alert"]),
        )

    def save_state(self, state: WatchState) -> None:
        self._conn.execute(
            "INSERT INTO watch_state (watch, consecutive_failures, failing_alerted, triggered, last_checked, last_alert)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(watch) DO UPDATE SET consecutive_failures = excluded.consecutive_failures,"
            " failing_alerted = excluded.failing_alerted, triggered = excluded.triggered,"
            " last_checked = excluded.last_checked, last_alert = excluded.last_alert",
            (
                state.watch,
                state.consecutive_failures,
                int(state.failing_alerted),
                int(state.triggered),
                _iso(state.last_checked) if state.last_checked else None,
                _iso(state.last_alert) if state.last_alert else None,
            ),
        )
        self._conn.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _row_to_observation(row: sqlite3.Row) -> Observation:
    return Observation(
        id=row["id"],
        watch=row["watch"],
        ts=_parse_ts(row["ts"]) or utcnow(),
        status=row["status"],
        value=row["value"],
        value_num=row["value_num"],
        hash=row["hash"],
        error=row["error"],
        alerted=bool(row["alerted"]),
    )
