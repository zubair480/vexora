"""SQLite. Shared file: owned by the integration session (see CONTRACT.md).

Other modules add their own tables with `register_schema(sql)` at import time and use
`connect()` for their own reads and writes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from . import config
from .models import Event

_lock = threading.Lock()
_schemas: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, type TEXT NOT NULL, ts REAL NOT NULL, source TEXT NOT NULL,
        shift_id TEXT NOT NULL, worker_id TEXT NOT NULL, station_id TEXT, asset_id TEXT,
        payload TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS events_shift_ts ON events (shift_id, ts);
    """
]
_applied: set[tuple[str, int]] = set()


def register_schema(sql: str) -> None:
    if sql not in _schemas:
        _schemas.append(sql)


def connect() -> sqlite3.Connection:
    """A new connection with every registered table present. Close it when done."""
    con = sqlite3.connect(str(config.DB_PATH), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    with _lock:
        for i, sql in enumerate(_schemas):
            key = (str(config.DB_PATH), i)
            if key not in _applied:
                con.executescript(sql)
                _applied.add(key)
    return con


def reset_for_tests() -> None:
    _applied.clear()


def insert_event(event: Event) -> None:
    con = connect()
    try:
        with con:
            con.execute(
                "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                (event.id, event.type, event.ts, event.source, event.shift_id, event.worker_id,
                 event.station_id, event.asset_id, json.dumps(event.payload)),
            )
    finally:
        con.close()


def list_events(shift_id: str, types: Optional[list[str]] = None, limit: int = 1000) -> list[dict]:
    sql, args = "SELECT * FROM events WHERE shift_id = ?", [shift_id]
    if types:
        sql += " AND type IN (%s)" % ",".join("?" * len(types))
        args += list(types)
    sql += " ORDER BY ts ASC LIMIT ?"
    args.append(limit)
    con = connect()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        out.append(item)
    return out
