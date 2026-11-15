"""BeltGuard SQLite store (S2 band-backend).

Ten entities from the brief, every row with `id`, `ts` (unix seconds, laptop clock) and
`source` ("band" | "user" | "ai" | "rules" | "system" | "sim"). One connection guarded by a
lock: the server writes a few rows a second at most, so plain SQLite is plenty.
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# BELTGUARD_DB points a test server at a copy, so tests never touch the live shift.
DB_PATH = Path(os.environ["BELTGUARD_DB"]) if os.environ.get("BELTGUARD_DB") else ROOT / "data" / "beltguard.db"

# Columns stored as JSON text and decoded on the way out.
JSON_COLUMNS = {"steps", "refs", "data", "components"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (id TEXT PRIMARY KEY, ts REAL, source TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  name TEXT, role TEXT);
CREATE TABLE IF NOT EXISTS conveyors (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  name TEXT, site TEXT);
CREATE TABLE IF NOT EXISTS stations (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  conveyor_id TEXT, number INTEGER, name TEXT, pos REAL, components TEXT);
CREATE TABLE IF NOT EXISTS inspection_events (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  worker_id TEXT, conveyor_id TEXT, station_id TEXT, kind TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS defects (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  inspection_event_id TEXT, worker_id TEXT, conveyor_id TEXT, station_id TEXT,
  component TEXT, condition TEXT, severity TEXT, transcript TEXT);
CREATE TABLE IF NOT EXISTS tool_profiles (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  name TEXT, vibration_m_s2 REAL, vib_source TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS tool_sessions (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  worker_id TEXT, conveyor_id TEXT, station_id TEXT, work_order_id TEXT, tool_id TEXT,
  t_start REAL, t_end REAL, wall_start REAL, wall_end REAL, trigger_s REAL,
  confidence REAL, status TEXT, points REAL);
CREATE TABLE IF NOT EXISTS exposure_records (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  worker_id TEXT, tool_session_id TEXT, tool_id TEXT, vibration_m_s2 REAL,
  vib_source TEXT, trigger_s REAL, points REAL, status TEXT, method TEXT);
CREATE TABLE IF NOT EXISTS work_orders (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  defect_id TEXT, conveyor_id TEXT, station_id TEXT, title TEXT, priority TEXT,
  priority_reason TEXT, status TEXT, steps TEXT, requires_isolation INTEGER,
  tool_id TEXT, est_trigger_min REAL, data TEXT);
CREATE TABLE IF NOT EXISTS shift_events (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  lane TEXT, kind TEXT, title TEXT, detail TEXT, station_id TEXT, refs TEXT);
CREATE INDEX IF NOT EXISTS ix_events_ts ON shift_events(ts);
CREATE TABLE IF NOT EXISTS transcripts (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  text TEXT, result_status TEXT, defect_id TEXT, work_order_id TEXT,
  question TEXT, extracted_by TEXT, station_id TEXT, input TEXT, intent TEXT, response TEXT);
CREATE INDEX IF NOT EXISTS ix_transcripts_ts ON transcripts(ts);
CREATE TABLE IF NOT EXISTS tool_assignments (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
  worker_id TEXT, tool_id TEXT, returned_ts REAL);
CREATE TABLE IF NOT EXISTS motion_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, source TEXT NOT NULL,
  session_id TEXT, t_band_ms REAL, n INTEGER,
  ax REAL, ay REAL, az REAL, gx REAL, gy REAL, gz REAL, rate INTEGER, label TEXT);
CREATE INDEX IF NOT EXISTS ix_samples_session_t ON motion_samples(session_id, t_band_ms);
"""

PREFIX = {
    "inspection_events": "IE", "defects": "DEF", "tool_sessions": "TS",
    "exposure_records": "EX", "work_orders": "WO", "shift_events": "EV", "tool_assignments": "TA",
    "transcripts": "TR",
}

WORKER = {"id": "W-01", "name": "Technician W-01", "role": "Conveyor maintenance technician"}
CONVEYOR = {"id": "CV-102", "name": "Overland conveyor CV-102", "site": "Demo site"}
STATIONS = [
    {"id": "S1", "number": 1, "name": "Tail pulley & loading zone", "pos": 0.08,
     "components": ["tail pulley", "loading chute", "skirt board", "impact idler", "impact bed", "belt scraper"]},
    {"id": "S2", "number": 2, "name": "Carry idlers & belt tracking", "pos": 0.30,
     "components": ["carry idler", "troughing idler", "training idler", "belt tracking", "belt edge"]},
    {"id": "S3", "number": 3, "name": "Return idlers", "pos": 0.52,
     "components": ["return idler", "return roller", "v-return idler", "return belt"]},
    {"id": "S4", "number": 4, "name": "Take-up & belt tension", "pos": 0.74,
     "components": ["take-up", "gravity take-up", "counterweight", "bend pulley", "snub pulley"]},
    {"id": "S5", "number": 5, "name": "Head/drive pulley & discharge", "pos": 0.94,
     "components": ["head pulley", "drive pulley", "gearbox", "drive motor", "discharge chute", "primary scraper"]},
]


class Store:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(meta)")}
        if "key" in cols:                                 # older layout: give meta id/ts/source too
            self.conn.execute("ALTER TABLE meta RENAME COLUMN key TO id")
        if "ts" not in cols:
            self.conn.execute("ALTER TABLE meta ADD COLUMN ts REAL")
        if "source" not in cols:
            self.conn.execute("ALTER TABLE meta ADD COLUMN source TEXT")
        self.conn.execute("UPDATE meta SET ts = COALESCE(ts, ?), source = COALESCE(source, 'system')", (time.time(),))
        # source = who said it (band-mic / user); extracted_by = what turned the words into a record (ai / rules)
        for table in ("defects", "work_orders"):
            if "extracted_by" not in {r[1] for r in self.conn.execute("PRAGMA table_info(%s)" % table)}:
                self.conn.execute("ALTER TABLE %s ADD COLUMN extracted_by TEXT" % table)
        # input = how the words arrived (voice from the band mic / typed); source stays in the contract set
        tcols = {r[1] for r in self.conn.execute("PRAGMA table_info(transcripts)")}
        for col in ("input", "intent", "response", "acted"):   # acted: what BeltGuard did with the sentence
            if col not in tcols:
                self.conn.execute("ALTER TABLE transcripts ADD COLUMN %s TEXT" % col)
        self.conn.commit()

    # ---- generic helpers -------------------------------------------------------------
    def _encode(self, fields: dict) -> dict:
        out = {}
        for k, v in fields.items():
            if k in JSON_COLUMNS and v is not None and not isinstance(v, str):
                v = json.dumps(v)
            elif isinstance(v, bool):
                v = int(v)
            out[k] = v
        return out

    def _decode(self, row) -> dict:
        d = dict(row)
        for k in JSON_COLUMNS & d.keys():
            if isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        if "requires_isolation" in d and d["requires_isolation"] is not None:
            d["requires_isolation"] = bool(d["requires_isolation"])
        return d

    def next_id(self, table: str) -> str:
        with self.lock:
            n = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return "%s-%d" % (PREFIX.get(table, "ID"), 1001 + n)

    def insert(self, table: str, source: str, **fields) -> dict:
        fields = dict(fields)
        fields.setdefault("id", self.next_id(table))
        fields.setdefault("ts", time.time())
        fields["source"] = source
        enc = self._encode(fields)
        cols = ", ".join(enc)
        marks = ", ".join("?" for _ in enc)
        with self.lock:
            self.conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", list(enc.values()))
            self.conn.commit()
        return self.get(table, fields["id"])

    def update(self, table: str, row_id: str, **fields) -> dict | None:
        if fields:
            enc = self._encode(fields)
            sets = ", ".join(f"{k} = ?" for k in enc)
            with self.lock:
                self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*enc.values(), row_id])
                self.conn.commit()
        return self.get(table, row_id)

    def get(self, table: str, row_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return self._decode(row) if row else None

    def query(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._decode(r) for r in rows]

    def since(self, table: str, ts: float, limit: int | None = None, order: str = "ts") -> list[dict]:
        sql = f"SELECT * FROM {table} WHERE ts >= ? ORDER BY {order}"
        if limit:
            sql = f"SELECT * FROM (SELECT * FROM {table} WHERE ts >= ? ORDER BY ts DESC LIMIT {int(limit)}) ORDER BY ts"
        return self.query(sql, (ts,))

    # ---- meta ------------------------------------------------------------------------
    def meta(self, key: str, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE id = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value, source: str = "system") -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta (id, ts, source, value) VALUES (?, ?, ?, ?)",
                              (key, time.time(), source, json.dumps(value)))
            self.conn.commit()

    # ---- seed ------------------------------------------------------------------------
    def seed(self, tools: list[dict]) -> None:
        now = time.time()
        if not self.get("workers", WORKER["id"]):
            self.insert("workers", "system", ts=now, **WORKER)
        if not self.get("conveyors", CONVEYOR["id"]):
            self.insert("conveyors", "system", ts=now, **CONVEYOR)
        for st in STATIONS:
            if not self.get("stations", st["id"]):
                self.insert("stations", "system", ts=now, conveyor_id=CONVEYOR["id"], **st)
        self.sync_tools(tools)
        if self.meta("shift_start") is None:
            self.set_meta("shift_start", now)

    def sync_tools(self, tools: list[dict]) -> None:
        for t in tools:
            if not t.get("id"):
                continue
            self.insert("tool_profiles", "system", id=t["id"], name=t.get("name", t["id"]),
                        vibration_m_s2=t.get("vibration_m_s2"), vib_source=t.get("source"), data=t)

    def worker(self) -> dict:
        return self.get("workers", WORKER["id"]) or dict(WORKER)

    def conveyor(self) -> dict:
        return self.get("conveyors", CONVEYOR["id"]) or dict(CONVEYOR)

    def stations(self) -> list[dict]:
        return self.query("SELECT * FROM stations ORDER BY number")


class SampleWriter:
    """Every real band reading goes into SQLite table motion_samples.

    The band worker only queues a tuple (cheap); this thread writes the queue in batches
    (executemany) about every 0.5 s on its own connection, so neither the band handlers nor the
    server's event loop ever wait for the disk. WAL mode lets readers (/api/samples) run meanwhile.
    Gyro values are the band's raw readings (the bias is subtracted later, in the pipeline)."""

    COLS = ("ts", "source", "session_id", "t_band_ms", "n", "ax", "ay", "az", "gx", "gy", "gz", "rate", "label")

    def __init__(self, path: Path = DB_PATH, every: float = 0.5) -> None:
        self.path = path
        self.every = every
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.written = 0
        self.batches = 0
        self.last_error = None
        self.sessions: dict = {}             # session_id -> {"rows", "first_ts", "last_ts", "rate", "source"}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="beltguard-samples", daemon=True)
        self._thread.start()

    def add(self, row: tuple) -> None:
        self.q.put(row)

    def _drain(self) -> list:
        rows = []
        while True:
            try:
                rows.append(self.q.get_nowait())
            except queue.Empty:
                return rows

    def _run(self) -> None:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        sql = "INSERT INTO motion_samples (%s) VALUES (%s)" % (", ".join(self.COLS), ", ".join("?" * len(self.COLS)))
        while not self._stop.wait(self.every):
            self._flush(conn, sql)
        self._flush(conn, sql)
        conn.close()

    def _flush(self, conn, sql) -> None:
        rows = self._drain()
        if not rows:
            return
        try:
            conn.executemany(sql, rows)
            conn.commit()
        except sqlite3.Error as e:           # e.g. locked for longer than the timeout: try again next round
            self.last_error = str(e)
            for r in rows[-200000:]:
                self.q.put(r)
            return
        self.written += len(rows)
        self.batches += 1
        for r in rows:
            s = self.sessions.setdefault(r[2], {"rows": 0, "first_ts": r[0], "last_ts": r[0], "rate": r[11],
                                                "source": r[1]})
            s["rows"] += 1
            s["last_ts"] = r[0]
            s["rate"] = r[11]

    def stats(self) -> dict:
        return {"written": self.written, "batches": self.batches, "queued": self.q.qsize(),
                "last_error": self.last_error, "every_s": self.every, "sessions": self.sessions}

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(3)
