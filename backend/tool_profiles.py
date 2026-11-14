"""Tool vibration profiles (be-hav).

The ONLY place a vibration magnitude (a_hv, m/s2) may come from. Values are entered by a person
from the manufacturer's declared or measured figure; the seeds are labelled engineering examples.
Nothing here is ever derived from the wrist band.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from . import database

EXAMPLE_SOURCE = "Configured engineering example — replace with the manufacturer's declared or measured value"
NO_VALUE_SOURCE = "No vibration value configured — enter the manufacturer's declared or measured value"

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS hav_tool_profiles (
        tool_id TEXT PRIMARY KEY, name TEXT NOT NULL, a_hv_ms2 REAL,
        source TEXT NOT NULL, updated_at REAL NOT NULL
    );
    """
)

_SEEDS = (
    ("impact_wrench", "Impact wrench", 8.0, EXAMPLE_SOURCE),
    ("angle_grinder", "Angle grinder", 6.0, EXAMPLE_SOURCE),
    ("low_vibration_wrench", "Low-vibration impact wrench", 4.0, EXAMPLE_SOURCE),
    ("needle_scaler", "Needle scaler", None, NO_VALUE_SOURCE),
)


@dataclass
class ToolProfile:
    tool_id: str
    name: str
    a_hv_ms2: float | None
    source: str
    updated_at: float

    def to_dict(self) -> dict:
        return asdict(self)


def _seed(con) -> None:
    # INSERT OR IGNORE on every access: a fresh database gets the examples, user edits survive.
    now = time.time()
    con.executemany(
        "INSERT OR IGNORE INTO hav_tool_profiles (tool_id, name, a_hv_ms2, source, updated_at) "
        "VALUES (?,?,?,?,?)",
        [(tool_id, name, value, source, now) for tool_id, name, value, source in _SEEDS],
    )


def _row(row) -> ToolProfile:
    value = row["a_hv_ms2"]
    return ToolProfile(row["tool_id"], row["name"], None if value is None else float(value),
                       row["source"], float(row["updated_at"]))


def get(tool_id: str | None) -> ToolProfile | None:
    """The profile, or None for a missing / unknown tool id."""
    if not tool_id or not isinstance(tool_id, str):
        return None
    con = database.connect()
    try:
        with con:
            _seed(con)
        row = con.execute("SELECT * FROM hav_tool_profiles WHERE tool_id = ?", (tool_id,)).fetchone()
    finally:
        con.close()
    return _row(row) if row else None


def list_profiles() -> list[ToolProfile]:
    con = database.connect()
    try:
        with con:
            _seed(con)
        rows = con.execute("SELECT * FROM hav_tool_profiles ORDER BY name, tool_id").fetchall()
    finally:
        con.close()
    return [_row(row) for row in rows]


def upsert(tool_id: str, name: str, a_hv_ms2: float | None, source: str | None = None) -> ToolProfile:
    """Create or replace a profile. A value needs a stated source; no value is allowed."""
    if not isinstance(tool_id, str) or not tool_id.strip():
        raise ValueError("tool_id is required")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    tool_id, name = tool_id.strip(), name.strip()
    if a_hv_ms2 is None:
        value, source = None, NO_VALUE_SOURCE
    else:
        if isinstance(a_hv_ms2, bool) or not isinstance(a_hv_ms2, (int, float)):
            raise ValueError("a_hv_ms2 must be a number in m/s2, or null")
        value = float(a_hv_ms2)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("a_hv_ms2 must be finite and greater than 0, or null")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("a vibration value needs a source (manufacturer's declared or measured value)")
        source = source.strip()
    now = time.time()
    con = database.connect()
    try:
        with con:
            _seed(con)
            con.execute(
                "INSERT OR REPLACE INTO hav_tool_profiles (tool_id, name, a_hv_ms2, source, updated_at) "
                "VALUES (?,?,?,?,?)",
                (tool_id, name, value, source, now),
            )
    finally:
        con.close()
    return ToolProfile(tool_id, name, value, source, now)
