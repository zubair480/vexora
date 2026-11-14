"""Local asset registry for conveyor CV-102 (be-voice session).

Maps spoken references ("station three, return idler fourteen") to registry IDs.
Deterministic: no AI, no fuzzy guessing. An asset that is not in the registry table is never
returned; anything unclear comes back "ambiguous" or "unknown" so the caller can ask the worker.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import database

CONVEYOR_ID = "CV-102"

database.register_schema(
    """
    CREATE TABLE IF NOT EXISTS asset_stations (
        station_id TEXT PRIMARY KEY, conveyor_id TEXT NOT NULL, name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS asset_registry (
        asset_id TEXT PRIMARY KEY, conveyor_id TEXT NOT NULL, station_id TEXT NOT NULL,
        name TEXT NOT NULL, kind TEXT NOT NULL, number INTEGER NOT NULL
    );
    """
)

_STATIONS = [("S1", "Head/Drive"), ("S2", "Carry Idlers"), ("S3", "Return Idlers"),
             ("S4", "Transition"), ("S5", "Tail/Take-up")]
_IDLERS = (12, 13, 14, 15, 16)
_SEED = {  # station -> [(kind, numbers)]
    "S1": [("DRIVE-PULLEY", (1,)), ("DRIVE-MOTOR", (1,)), ("GEARBOX", (1,)),
           ("HEAD-PULLEY", (1,)), ("PRIMARY-SCRAPER", (1,))],
    "S2": [("CARRY-IDLER", _IDLERS)],
    "S3": [("RETURN-IDLER", _IDLERS)],
    "S4": [("TRANSITION-IDLER", (1, 2)), ("SKIRT-BOARD", (1,)), ("IMPACT-BED", (1,))],
    "S5": [("TAIL-PULLEY", (1,)), ("TAKEUP-PULLEY", (1,)), ("TAKEUP-COUNTERWEIGHT", (1,)),
           ("RETURN-PLOUGH", (1,))],
}

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_SYNONYMS = {"idlers": "idler", "roller": "idler", "rollers": "idler"}


@dataclass
class AssetMatch:
    status: str                    # "matched" | "ambiguous" | "unknown"
    query: str                     # the spoken reference as given
    asset_id: str | None = None    # set only when matched
    station_id: str | None = None  # from the asset when matched, else the resolved station hint
    conveyor_id: str | None = None
    name: str | None = None        # human name of the matched asset
    candidates: list[dict] = field(default_factory=list)  # [{asset_id, station_id, name}, ...]
    reason: str = ""               # short human explanation

    def to_dict(self) -> dict:
        return asdict(self)


def ensure_seeded() -> None:
    """Seed the registry the first time a database file is used. Safe to call every time."""
    con = database.connect()
    try:
        with con:
            if con.execute("SELECT COUNT(*) FROM asset_stations").fetchone()[0]:
                return  # already seeded: later edits to the tables are respected
            con.executemany("INSERT OR IGNORE INTO asset_stations VALUES (?,?,?)",
                            [(sid, CONVEYOR_ID, name) for sid, name in _STATIONS])
            con.executemany(
                "INSERT OR IGNORE INTO asset_registry VALUES (?,?,?,?,?,?)",
                [(f"{kind}-{n:02d}", CONVEYOR_ID, sid,
                  f"{kind.replace('-', ' ').capitalize().replace('Takeup', 'Take-up')} {n}",
                  kind, n)
                 for sid, kinds in _SEED.items() for kind, numbers in kinds for n in numbers])
    finally:
        con.close()


def _rows(sql: str, args: tuple = ()) -> list[dict]:
    ensure_seeded()
    con = database.connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _conveyor_key(conveyor_id) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(conveyor_id or "").upper())


def list_stations(conveyor_id: str | None = None) -> list[dict]:
    rows = _rows("SELECT station_id, conveyor_id, name FROM asset_stations ORDER BY station_id")
    key = _conveyor_key(conveyor_id)
    return [r for r in rows if not key or _conveyor_key(r["conveyor_id"]) == key]


def list_assets(conveyor_id: str | None = None, station_id: str | None = None) -> list[dict]:
    rows = _rows("SELECT asset_id, conveyor_id, station_id, name, kind, number "
                 "FROM asset_registry ORDER BY asset_id")
    key, sid = _conveyor_key(conveyor_id), str(station_id or "").strip().upper()
    return [r for r in rows if (not key or _conveyor_key(r["conveyor_id"]) == key)
            and (not sid or r["station_id"].upper() == sid)]


def get(asset_id: str) -> dict | None:
    rows = _rows("SELECT asset_id, conveyor_id, station_id, name, kind, number FROM asset_registry "
                 "WHERE asset_id = ? COLLATE NOCASE", (str(asset_id or "").strip(),))
    return rows[0] if rows else None


def _parse(text) -> tuple[list[str], list[int]]:
    """Normalise spoken text into (kind words, numbers). "Idler No. fourteen" -> (["idler"], [14])."""
    s = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())      # punctuation, - and _ to spaces
    s = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)     # "s3" -> "s 3"
    tokens = re.sub(r"\btake up\b", "takeup", s).split()
    words: list[str] = []
    numbers: list[int] = []
    i = 0
    while i < len(tokens):
        tok, nxt = tokens[i], tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok.isdigit():
            numbers.append(int(tok))                              # "01" == "1"
        elif tok in _TENS and 1 <= _UNITS.get(nxt, 0) <= 9:
            numbers.append(_TENS[tok] + _UNITS[nxt])              # "twenty one"
            i += 1
        elif tok in _TENS or tok in _UNITS:
            numbers.append(_TENS.get(tok, _UNITS.get(tok)))
        elif tok in ("number", "the") or (
                tok == "no" and (nxt.isdigit() or nxt in _UNITS or nxt in _TENS)):
            pass                                                  # "number 14", "no 14"
        else:
            words.append(_SYNONYMS.get(tok, tok))
        i += 1
    return words, numbers


def resolve_station(text: str | None) -> str | None:
    """"station three", "S3", "s 3", "return idlers", "tail", "take-up" -> "S3" etc., else None."""
    words, numbers = _parse(text)
    stations = list_stations()
    if numbers:
        if len(numbers) != 1 or words not in ([], ["s"], ["station"]):
            return None
        wanted = f"S{numbers[0]}"
        return next((s["station_id"] for s in stations if s["station_id"].upper() == wanted), None)
    wanted_words = set(words) - {"station"}
    if not wanted_words:
        return None
    hits = [s["station_id"] for s in stations if wanted_words <= set(_parse(s["name"])[0])]
    return hits[0] if len(hits) == 1 else None    # "idlers" alone fits S2 and S3: not resolvable


def resolve(asset_ref: str | None, station: str | None = None,
            conveyor_id: str | None = None) -> AssetMatch:
    query = asset_ref if isinstance(asset_ref, str) else ""
    if _conveyor_key(conveyor_id) and _conveyor_key(conveyor_id) != _conveyor_key(CONVEYOR_ID):
        return AssetMatch("unknown", query, reason=f"unknown conveyor '{conveyor_id}': "
                                                   f"only {CONVEYOR_ID} is in the registry")
    hint_given = bool(str(station or "").strip())
    hint = resolve_station(station) if hint_given else None
    base = {"station_id": hint, "conveyor_id": CONVEYOR_ID}
    words, numbers = _parse(query)
    if not words and not numbers:
        return AssetMatch("unknown", query, reason="no asset reference given", **base)

    parsed = [(row, _parse(row["asset_id"])) for row in list_assets(CONVEYOR_ID)]
    found = [row for row, key in parsed if key == (words, numbers)]          # exact registry ID
    if not found:   # every spoken word and number must be consistent with the asset
        found = [row for row, (w, n) in parsed if set(words) <= set(w) and set(numbers) <= set(n)]
    if not found:
        return AssetMatch("unknown", query, **base,
                          reason=f"no asset in the {CONVEYOR_ID} registry matches '{query}'")

    def brief(rows):
        return [{k: r[k] for k in ("asset_id", "station_id", "name")} for r in rows]

    if hint:
        at_station = [row for row in found if row["station_id"] == hint]
        if at_station:
            found = at_station
        elif len(found) == 1:
            row = found[0]
            return AssetMatch("ambiguous", query, candidates=brief(found), **base,
                              reason=f"{row['asset_id']} is registered at {row['station_id']}, "
                                     f"not {hint}: confirm with the worker")
        else:
            return AssetMatch("ambiguous", query, candidates=brief(found), **base,
                              reason=f"{len(found)} assets match '{query}' but none is at {hint}")
    if len(found) > 1:
        return AssetMatch("ambiguous", query, candidates=brief(found), **base,
                          reason=f"{len(found)} assets match '{query}': "
                                 + ", ".join(r["asset_id"] for r in found))
    row = found[0]
    reason = f"'{query}' is {row['asset_id']} at {row['station_id']}"
    if hint_given and not hint:
        reason += f" (station hint '{station}' not recognised, ignored)"
    return AssetMatch("matched", query, asset_id=row["asset_id"], station_id=row["station_id"],
                      conveyor_id=row["conveyor_id"], name=row["name"], reason=reason)
