"""Work-order drafts (be-voice).

A draft is created when an AI-extracted defect report matches a registered asset. It only
moves forward through an explicit, named human review action: nothing here auto-approves.
This module publishes no events; the caller (inspection_agent) does.
"""
from __future__ import annotations

import time
import uuid

from . import database

STATUSES = ("draft", "submitted", "approved", "rejected")
ACTIONS = ("submit", "approve", "reject")

# (current status, action) -> new status. Anything not listed is refused.
_TRANSITIONS = {
    ("draft", "submit"): "submitted",
    ("submitted", "approve"): "approved",
    ("submitted", "reject"): "rejected",
    ("draft", "reject"): "rejected",
}

_FIELDS = (
    "work_order_id", "defect_id", "inspection_id", "asset_id", "station_id", "conveyor_id",
    "task", "priority", "status", "source", "shift_id", "worker_id", "created_ts", "updated_ts",
    "reviewed_by", "review_note",
)

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS work_orders (
        work_order_id TEXT PRIMARY KEY, defect_id TEXT NOT NULL, inspection_id TEXT NOT NULL,
        asset_id TEXT NOT NULL, station_id TEXT, conveyor_id TEXT, task TEXT NOT NULL,
        priority TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
        shift_id TEXT NOT NULL, worker_id TEXT NOT NULL, created_ts REAL NOT NULL,
        updated_ts REAL NOT NULL, reviewed_by TEXT, review_note TEXT
    );
    CREATE INDEX IF NOT EXISTS work_orders_shift ON work_orders (shift_id, created_ts);
    CREATE TABLE IF NOT EXISTS work_order_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id TEXT NOT NULL, action TEXT NOT NULL,
        from_status TEXT NOT NULL, to_status TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT,
        ts REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS work_order_reviews_wo ON work_order_reviews (work_order_id, id);
    """
)


class WorkOrderError(Exception):
    pass


class WorkOrderNotFound(WorkOrderError):
    pass


class InvalidTransition(WorkOrderError):
    """Also raised for an unknown action or a missing reviewer."""


def create(*, defect_id: str, inspection_id: str, asset_id: str, station_id: str | None,
           conveyor_id: str | None, task: str, priority: str, shift_id: str, worker_id: str,
           source: str = "ai", work_order_id: str | None = None) -> dict:
    """Store a new work order. It is always a draft, whatever the caller wants."""
    now = time.time()
    order = {
        "work_order_id": work_order_id or "WO-" + uuid.uuid4().hex[:8].upper(),
        "defect_id": defect_id, "inspection_id": inspection_id, "asset_id": asset_id,
        "station_id": station_id, "conveyor_id": conveyor_id, "task": task,
        "priority": priority, "status": "draft", "source": source, "shift_id": shift_id,
        "worker_id": worker_id, "created_ts": now, "updated_ts": now,
        "reviewed_by": None, "review_note": None,
    }
    con = database.connect()
    try:
        with con:
            con.execute(
                "INSERT INTO work_orders (%s) VALUES (%s)"
                % (",".join(_FIELDS), ",".join("?" * len(_FIELDS))),
                [order[name] for name in _FIELDS],
            )
    finally:
        con.close()
    return order


def get(work_order_id: str) -> dict | None:
    con = database.connect()
    try:
        row = con.execute(
            "SELECT %s FROM work_orders WHERE work_order_id = ?" % ",".join(_FIELDS),
            (work_order_id,),
        ).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def list_work_orders(shift_id: str | None = None, status: str | None = None) -> list[dict]:
    """Oldest first, optionally filtered by shift and/or status."""
    sql, where, args = "SELECT %s FROM work_orders" % ",".join(_FIELDS), [], []
    if shift_id is not None:
        where.append("shift_id = ?")
        args.append(shift_id)
    if status is not None:
        where.append("status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_ts ASC, rowid ASC"
    con = database.connect()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]


def _refusal(status: str, action: str) -> str:
    if status in ("approved", "rejected"):
        return "work order is already %s; that is final" % status
    if status == "draft" and action == "approve":
        return "a draft cannot be approved: submit it for review first"
    return "cannot %s a work order that is %s" % (action, status)


def review(work_order_id: str, action: str, reviewer: str, note: str | None = None) -> dict:
    """Apply one human review action and log it. Returns the updated work order."""
    if action not in ACTIONS:
        raise InvalidTransition("unknown action %r; use one of %s" % (action, ", ".join(ACTIONS)))
    reviewer = reviewer.strip() if isinstance(reviewer, str) else ""
    if not reviewer:
        raise InvalidTransition("a named human reviewer is required")
    con = database.connect()
    try:
        row = con.execute(
            "SELECT status FROM work_orders WHERE work_order_id = ?", (work_order_id,)
        ).fetchone()
        if row is None:
            raise WorkOrderNotFound("no work order %s" % work_order_id)
        current = row["status"]
        target = _TRANSITIONS.get((current, action))
        if target is None:
            raise InvalidTransition(_refusal(current, action))
        now = time.time()
        with con:  # status check + update + log row in one transaction
            cur = con.execute(
                "UPDATE work_orders SET status = ?, updated_ts = ?, reviewed_by = ?, "
                "review_note = ? WHERE work_order_id = ? AND status = ?",
                (target, now, reviewer, note, work_order_id, current),
            )
            if cur.rowcount != 1:
                raise InvalidTransition(
                    "work order %s changed while it was being reviewed; reload it" % work_order_id
                )
            con.execute(
                "INSERT INTO work_order_reviews (work_order_id, action, from_status, to_status, "
                "reviewer, note, ts) VALUES (?,?,?,?,?,?,?)",
                (work_order_id, action, current, target, reviewer, note, now),
            )
    finally:
        con.close()
    return get(work_order_id)


def history(work_order_id: str) -> list[dict]:
    """The review log for one work order, oldest first."""
    con = database.connect()
    try:
        rows = con.execute(
            "SELECT work_order_id, action, from_status, to_status, reviewer, note, ts "
            "FROM work_order_reviews WHERE work_order_id = ? ORDER BY id ASC",
            (work_order_id,),
        ).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]
