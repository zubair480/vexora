"""SQLite store for work orders. One file, `workorders.db` in the repo root (gitignored)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    return Path(os.environ.get("WORKORDER_DB", ROOT / "workorders.db"))


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path()), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS work_orders (
        n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, created_ts REAL NOT NULL,
        status TEXT NOT NULL, sample INTEGER NOT NULL, request TEXT NOT NULL, data TEXT NOT NULL)""")
    return con


def create(request: dict, sample: bool) -> dict:
    con = _con()
    try:
        with con:
            cur = con.execute("INSERT INTO work_orders (created_ts, status, sample, request, data) VALUES (?,?,?,?,?)",
                              (time.time(), "DRAFT", int(sample), json.dumps(request), "{}"))
            wo_id = f"WO-{cur.lastrowid:04d}"
            con.execute("UPDATE work_orders SET id = ? WHERE n = ?", (wo_id, cur.lastrowid))
    finally:
        con.close()
    return get(wo_id)


def update(wo_id: str, status: str, **data) -> None:
    con = _con()
    try:
        with con:
            row = con.execute("SELECT data FROM work_orders WHERE id = ?", (wo_id,)).fetchone()
            merged = {**json.loads(row["data"]), **data}
            con.execute("UPDATE work_orders SET status = ?, data = ? WHERE id = ?", (status, json.dumps(merged), wo_id))
    finally:
        con.close()


def _row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "created_ts": row["created_ts"], "status": row["status"],
            "sample": bool(row["sample"]), "request": json.loads(row["request"]), **json.loads(row["data"])}


def get(wo_id: str) -> Optional[dict]:
    con = _con()
    try:
        row = con.execute("SELECT * FROM work_orders WHERE id = ?", (wo_id,)).fetchone()
    finally:
        con.close()
    return _row(row) if row else None


def list_all(limit: int = 50) -> list[dict]:
    con = _con()
    try:
        rows = con.execute("SELECT * FROM work_orders ORDER BY n DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    return [_row(r) for r in rows]
