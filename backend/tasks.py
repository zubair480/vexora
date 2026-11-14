"""Shift plan for the HAV planner: tasks, workers and configured alternatives (be-hav).

Everything here is configuration entered by people (or the seeded demo plan). Nothing in this
module computes exposure or approves anything. A worker's qualifications may be unknown (None):
that is kept as None and never guessed.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from typing import Optional

from . import config, database

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS hav_tasks (
        task_id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, title TEXT NOT NULL, asset TEXT,
        tool_id TEXT, expected_trigger_minutes REAL NOT NULL DEFAULT 0,
        priority INTEGER NOT NULL DEFAULT 3, required_qualification TEXT,
        dependencies TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending'
    );
    CREATE TABLE IF NOT EXISTS hav_workers (
        worker_id TEXT PRIMARY KEY, name TEXT NOT NULL, qualifications TEXT,
        qualifications_verified INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS hav_alternatives (
        id TEXT PRIMARY KEY, tool_id TEXT NOT NULL, kind TEXT NOT NULL, alt_tool_id TEXT,
        method TEXT, note TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'configured'
    );
    """
)

STATUSES = ("pending", "done")

# (task_id, title, asset, tool_id, minutes, priority, qualification, dependencies)
_SEED_TASKS = (
    ("task-idler-s3", "Replace seized idler at Station 3", "CV-102 / Station 3 / idler",
     "impact_wrench", 25.0, 1, "mechanical_fitter", []),
    ("task-bracket-s3", "Dress idler frame bracket at Station 3", "CV-102 / Station 3 / idler frame",
     "angle_grinder", 15.0, 2, "mechanical_fitter", ["task-idler-s3"]),
    ("task-guard-s3", "Refit guard and test run at Station 3", "CV-102 / Station 3 / guard",
     None, 0.0, 3, None, ["task-bracket-s3"]),
)
# (worker_id, name, qualifications or None = unknown, verified)
_SEED_WORKERS = (
    ("worker-1", "Worker 1", ["mechanical_fitter"], True),
    ("worker-2", "Worker 2", ["mechanical_fitter"], True),
    ("worker-3", "Worker 3", None, False),
)
# (tool_id, alt_tool_id, note)
_SEED_ALTERNATIVES = (
    ("impact_wrench", "low_vibration_wrench",
     "Configured example — confirm the alternative tool is available and suitable for the job"),
)


def _alt_id(tool_id: str, kind: str, value: str) -> str:
    digest = hashlib.sha1(f"{tool_id}|{kind}|{value}".encode("utf-8")).hexdigest()[:12]
    return f"alt-{digest}"


def _seed(con: sqlite3.Connection) -> None:
    """INSERT OR IGNORE on every access: tests switch database per test, edits survive."""
    shift_id = config.current_shift()["shift_id"]
    with con:
        for task_id, title, asset, tool_id, minutes, priority, qual, deps in _SEED_TASKS:
            con.execute(
                "INSERT OR IGNORE INTO hav_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (task_id, shift_id, title, asset, tool_id, minutes, priority, qual,
                 json.dumps(deps), "pending"),
            )
        for worker_id, name, quals, verified in _SEED_WORKERS:
            con.execute(
                "INSERT OR IGNORE INTO hav_workers VALUES (?,?,?,?)",
                (worker_id, name, None if quals is None else json.dumps(quals), int(verified)),
            )
        for tool_id, alt_tool_id, note in _SEED_ALTERNATIVES:
            con.execute(
                "INSERT OR IGNORE INTO hav_alternatives VALUES (?,?,?,?,?,?,?)",
                (_alt_id(tool_id, "tool", alt_tool_id), tool_id, "tool", alt_tool_id, None, note,
                 "configured"),
            )


def _connect() -> sqlite3.Connection:
    con = database.connect()
    try:
        _seed(con)
    except Exception:
        con.close()
        raise
    return con


def _task(row: sqlite3.Row) -> dict:
    try:
        deps = json.loads(row["dependencies"] or "[]")
    except (TypeError, ValueError):
        deps = []
    return {
        "task_id": row["task_id"],
        "shift_id": row["shift_id"],
        "title": row["title"],
        "asset": row["asset"],
        "tool_id": row["tool_id"],
        "expected_trigger_minutes": float(row["expected_trigger_minutes"] or 0.0),
        "priority": int(row["priority"]),
        "required_qualification": row["required_qualification"],
        "dependencies": [str(d) for d in deps],
        "status": row["status"],
    }


def _worker(row: sqlite3.Row) -> dict:
    quals = row["qualifications"]
    return {
        "worker_id": row["worker_id"],
        "name": row["name"],
        "qualifications": None if quals is None else [str(q) for q in json.loads(quals)],
        "qualifications_verified": bool(row["qualifications_verified"]),
    }


def _alternative(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "tool_id": row["tool_id"],
        "kind": row["kind"],
        "alt_tool_id": row["alt_tool_id"],
        "method": row["method"],
        "note": row["note"],
        "source": row["source"],
    }


# ---------------------------------------------------------------- tasks

def list_tasks(shift_id: str, include_done: bool = False) -> list[dict]:
    """Tasks of a shift ordered by priority (1 = first), then task_id."""
    sql = "SELECT * FROM hav_tasks WHERE shift_id = ?"
    if not include_done:
        sql += " AND status != 'done'"
    sql += " ORDER BY priority ASC, task_id ASC"
    con = _connect()
    try:
        return [_task(r) for r in con.execute(sql, (shift_id,)).fetchall()]
    finally:
        con.close()


def get_task(task_id: str) -> Optional[dict]:
    con = _connect()
    try:
        row = con.execute("SELECT * FROM hav_tasks WHERE task_id = ?", (task_id,)).fetchone()
    finally:
        con.close()
    return _task(row) if row else None


def upsert_task(task_id, shift_id, title, asset, tool_id, expected_trigger_minutes, priority=3,
                required_qualification=None, dependencies=None, status="pending") -> dict:
    if not task_id or not shift_id or not title:
        raise ValueError("task_id, shift_id and title are required")
    try:
        minutes = float(expected_trigger_minutes)
    except (TypeError, ValueError):
        raise ValueError("expected_trigger_minutes must be a number") from None
    if not math.isfinite(minutes) or minutes < 0:
        raise ValueError("expected_trigger_minutes must be finite and not negative")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    deps = [str(d) for d in (dependencies or [])]
    con = _connect()
    try:
        with con:
            con.execute(
                """
                INSERT INTO hav_tasks VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    shift_id=excluded.shift_id, title=excluded.title, asset=excluded.asset,
                    tool_id=excluded.tool_id,
                    expected_trigger_minutes=excluded.expected_trigger_minutes,
                    priority=excluded.priority,
                    required_qualification=excluded.required_qualification,
                    dependencies=excluded.dependencies, status=excluded.status
                """,
                (task_id, shift_id, title, asset, tool_id or None, minutes, int(priority),
                 required_qualification or None, json.dumps(deps), status),
            )
        row = con.execute("SELECT * FROM hav_tasks WHERE task_id = ?", (task_id,)).fetchone()
    finally:
        con.close()
    return _task(row)


def set_status(task_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    con = _connect()
    try:
        with con:
            cur = con.execute("UPDATE hav_tasks SET status = ? WHERE task_id = ?", (status, task_id))
        if cur.rowcount == 0:
            raise KeyError(task_id)
        row = con.execute("SELECT * FROM hav_tasks WHERE task_id = ?", (task_id,)).fetchone()
    finally:
        con.close()
    return _task(row)


def next_task(shift_id: str) -> Optional[dict]:
    """First pending task (priority order) whose dependencies are all done."""
    con = _connect()
    try:
        done = {r["task_id"] for r in
                con.execute("SELECT task_id FROM hav_tasks WHERE status = 'done'").fetchall()}
        rows = con.execute(
            "SELECT * FROM hav_tasks WHERE shift_id = ? AND status = 'pending' "
            "ORDER BY priority ASC, task_id ASC", (shift_id,)).fetchall()
    finally:
        con.close()
    for row in rows:
        task = _task(row)
        # a dependency that is unknown is not "done": the task stays gated
        if all(dep in done for dep in task["dependencies"]):
            return task
    return None


# ---------------------------------------------------------------- workers

def list_workers() -> list[dict]:
    con = _connect()
    try:
        rows = con.execute("SELECT * FROM hav_workers ORDER BY worker_id ASC").fetchall()
    finally:
        con.close()
    return [_worker(r) for r in rows]


def get_worker(worker_id: str) -> Optional[dict]:
    con = _connect()
    try:
        row = con.execute("SELECT * FROM hav_workers WHERE worker_id = ?", (worker_id,)).fetchone()
    finally:
        con.close()
    return _worker(row) if row else None


def upsert_worker(worker_id: str, name: str, qualifications: Optional[list[str]],
                  qualifications_verified: bool = False) -> dict:
    """`qualifications=None` means unknown and is stored as unknown (never as an empty list)."""
    if not worker_id or not name:
        raise ValueError("worker_id and name are required")
    if qualifications is not None and isinstance(qualifications, (str, bytes)):
        raise ValueError("qualifications must be a list of strings or None")
    quals = None if qualifications is None else json.dumps([str(q) for q in qualifications])
    con = _connect()
    try:
        with con:
            con.execute(
                """
                INSERT INTO hav_workers VALUES (?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET name=excluded.name,
                    qualifications=excluded.qualifications,
                    qualifications_verified=excluded.qualifications_verified
                """,
                (worker_id, name, quals, int(bool(qualifications_verified))),
            )
        row = con.execute("SELECT * FROM hav_workers WHERE worker_id = ?", (worker_id,)).fetchone()
    finally:
        con.close()
    return _worker(row)


# ---------------------------------------------------------------- alternatives

def alternatives_for(tool_id: Optional[str]) -> list[dict]:
    """Only alternatives a person configured for this tool. Nothing is inferred."""
    if not tool_id:
        return []
    con = _connect()
    try:
        rows = con.execute(
            "SELECT * FROM hav_alternatives WHERE tool_id = ? ORDER BY kind DESC, id ASC",
            (tool_id,)).fetchall()
    finally:
        con.close()
    return [_alternative(r) for r in rows]


def add_alternative(tool_id: str, alt_tool_id: Optional[str] = None, method: Optional[str] = None,
                    note: str = "") -> dict:
    if not tool_id:
        raise ValueError("tool_id is required")
    alt_tool_id = (alt_tool_id or "").strip() or None
    method = (method or "").strip() or None
    if (alt_tool_id is None) == (method is None):
        raise ValueError("give exactly one of alt_tool_id or method")
    kind = "tool" if alt_tool_id else "method"
    alt_id = _alt_id(tool_id, kind, alt_tool_id or method)
    con = _connect()
    try:
        with con:
            con.execute(
                "INSERT OR REPLACE INTO hav_alternatives VALUES (?,?,?,?,?,?,?)",
                (alt_id, tool_id, kind, alt_tool_id, method, note or "", "configured"),
            )
        row = con.execute("SELECT * FROM hav_alternatives WHERE id = ?", (alt_id,)).fetchone()
    finally:
        con.close()
    return _alternative(row)
