"""Asset registry: seeding and deterministic resolution of spoken references. No band involved."""
import pytest

from backend import assets, database

pytestmark = pytest.mark.usefixtures("tmp_db")

REQUIRED = """DRIVE-PULLEY-01 DRIVE-MOTOR-01 GEARBOX-01 HEAD-PULLEY-01 PRIMARY-SCRAPER-01
CARRY-IDLER-12 CARRY-IDLER-13 CARRY-IDLER-14 CARRY-IDLER-15 CARRY-IDLER-16
RETURN-IDLER-12 RETURN-IDLER-13 RETURN-IDLER-14 RETURN-IDLER-15 RETURN-IDLER-16
TRANSITION-IDLER-01 TRANSITION-IDLER-02 SKIRT-BOARD-01 IMPACT-BED-01
TAIL-PULLEY-01 TAKEUP-PULLEY-01 TAKEUP-COUNTERWEIGHT-01 RETURN-PLOUGH-01""".split()


def _execute(sql, args=()):
    con = database.connect()
    try:
        with con:
            return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _ids(match):
    return [c["asset_id"] for c in match.candidates]


def test_five_stations_listed():
    stations = assets.list_stations()
    assert [s["station_id"] for s in stations] == ["S1", "S2", "S3", "S4", "S5"]
    assert stations[2] == {"station_id": "S3", "conveyor_id": "CV-102", "name": "Return Idlers"}
    assert len(assets.list_stations("cv 102")) == 5
    assert assets.list_stations("CV-999") == []


def test_seed_contents_and_filters():
    rows = assets.list_assets()
    assert sorted(r["asset_id"] for r in rows) == sorted(REQUIRED)
    assert set(rows[0]) == {"asset_id", "conveyor_id", "station_id", "name", "kind", "number"}
    assert [r["asset_id"] for r in assets.list_assets(station_id="s4")] == [
        "IMPACT-BED-01", "SKIRT-BOARD-01", "TRANSITION-IDLER-01", "TRANSITION-IDLER-02"]
    assert len(assets.list_assets("cv102", "S3")) == 5
    assert assets.list_assets("CV-ZZZ") == []


def test_seeding_is_idempotent_and_persisted():
    assets.ensure_seeded()
    assets.ensure_seeded()
    assets.list_assets()
    assert _execute("SELECT COUNT(*) FROM asset_registry")[0][0] == len(REQUIRED)
    assert _execute("SELECT COUNT(*) FROM asset_stations")[0][0] == 5


def test_seeded_on_first_use_without_explicit_call():
    assert assets.resolve("gearbox").status == "matched"
    assert _execute("SELECT COUNT(*) FROM asset_registry")[0][0] == len(REQUIRED)


def test_get_is_case_insensitive():
    row = assets.get("return-idler-14")
    assert row["asset_id"] == "RETURN-IDLER-14" and row["station_id"] == "S3"
    assert row["kind"] == "RETURN-IDLER" and row["number"] == 14
    assert assets.get("RETURN-IDLER-99") is None
    assert assets.get("") is None


@pytest.mark.parametrize("text, expected", [
    ("station three", "S3"), ("Station 3", "S3"), ("S3", "S3"), ("s 3", "S3"), ("s3", "S3"),
    ("return idlers", "S3"), ("carry idlers", "S2"), ("tail", "S5"), ("take-up", "S5"),
    ("take up", "S5"), ("head", "S1"), ("drive", "S1"), ("Head/Drive", "S1"),
    ("transition", "S4"), ("station one", "S1"),
    ("station nine", None), ("idlers", None), ("warehouse", None), ("", None), (None, None),
])
def test_resolve_station(text, expected):
    assert assets.resolve_station(text) == expected


@pytest.mark.parametrize("ref, expected", [
    ("return idler 14", "RETURN-IDLER-14"), ("RETURN-IDLER-14", "RETURN-IDLER-14"),
    ("Return idler fourteen.", "RETURN-IDLER-14"), ("return roller number 14", "RETURN-IDLER-14"),
    ("return idler no. 14", "RETURN-IDLER-14"), ("return idler #14", "RETURN-IDLER-14"),
    ("return_idlers 014", "RETURN-IDLER-14"), ("carry rollers sixteen", "CARRY-IDLER-16"),
    ("tail pulley", "TAIL-PULLEY-01"), ("gearbox", "GEARBOX-01"), ("gearbox one", "GEARBOX-01"),
    ("the drive motor 1", "DRIVE-MOTOR-01"), ("take up pulley", "TAKEUP-PULLEY-01"),
    ("take-up counterweight", "TAKEUP-COUNTERWEIGHT-01"), ("transition idler two", "TRANSITION-IDLER-02"),
    ("impact bed 01", "IMPACT-BED-01"), ("plough", "RETURN-PLOUGH-01"),
])
def test_resolve_matched(ref, expected):
    match = assets.resolve(ref)
    assert (match.status, match.asset_id) == ("matched", expected)
    assert match.query == ref and match.conveyor_id == "CV-102"
    assert match.station_id == assets.get(expected)["station_id"]
    assert match.name == assets.get(expected)["name"] and match.candidates == []


def test_matched_to_dict():
    data = assets.resolve("return idler 14", conveyor_id="cv 102").to_dict()
    assert data["status"] == "matched" and data["asset_id"] == "RETURN-IDLER-14"
    assert data["station_id"] == "S3" and data["name"] == "Return idler 14"
    assert set(data) == {"status", "query", "asset_id", "station_id", "conveyor_id", "name",
                         "candidates", "reason"}


def test_bare_idler_number_is_ambiguous():
    match = assets.resolve("idler 14")
    assert match.status == "ambiguous" and match.asset_id is None and match.station_id is None
    assert _ids(match) == ["CARRY-IDLER-14", "RETURN-IDLER-14"]
    assert match.candidates[1] == {"asset_id": "RETURN-IDLER-14", "station_id": "S3",
                                   "name": "Return idler 14"}


def test_missing_number_is_ambiguous():
    assert _ids(assets.resolve("transition idler")) == ["TRANSITION-IDLER-01", "TRANSITION-IDLER-02"]
    match = assets.resolve("return idler")
    assert match.status == "ambiguous" and match.asset_id is None
    assert _ids(match) == [f"RETURN-IDLER-{n}" for n in (12, 13, 14, 15, 16)]
    assert assets.resolve("pulley").status == "ambiguous"


@pytest.mark.parametrize("station", ["station three", "S3", "s 3", "return idlers"])
def test_station_hint_narrows(station):
    match = assets.resolve("idler fourteen", station=station)
    assert (match.status, match.asset_id, match.station_id) == ("matched", "RETURN-IDLER-14", "S3")
    assert assets.resolve("idler 14", station="carry idlers").asset_id == "CARRY-IDLER-14"


def test_station_hint_conflict_is_not_silently_resolved():
    match = assets.resolve("return idler 14", station="S1")
    assert match.status == "ambiguous" and match.asset_id is None
    assert _ids(match) == ["RETURN-IDLER-14"]
    assert "S3" in match.reason and "S1" in match.reason
    assert match.station_id == "S1"          # the hint; the candidate carries the asset's station
    many = assets.resolve("idler 14", station="tail")
    assert many.status == "ambiguous" and _ids(many) == ["CARRY-IDLER-14", "RETURN-IDLER-14"]


def test_unrecognised_station_hint_is_ignored_and_reported():
    match = assets.resolve("return idler 14", station="warehouse")
    assert match.status == "matched" and match.station_id == "S3"
    assert "warehouse" in match.reason


@pytest.mark.parametrize("ref", ["return idler 99", "idler 40", "return idler 17", "widget 3",
                                 "flux capacitor", "return idler 14 15", "", "   ", "?!", None])
def test_unknown_never_invents(ref):
    match = assets.resolve(ref)
    assert match.status == "unknown" and match.asset_id is None and match.candidates == []
    assert match.reason


def test_unknown_keeps_resolved_station_hint():
    match = assets.resolve("return idler 99", station="station three")
    assert (match.status, match.station_id) == ("unknown", "S3")


def test_conveyor_check():
    for ok in ("CV-102", "cv 102", "cv102", "", None, "  "):
        assert assets.resolve("gearbox", conveyor_id=ok).status == "matched"
    match = assets.resolve("gearbox", conveyor_id="CV-205")
    assert match.status == "unknown" and match.asset_id is None and "CV-205" in match.reason


def test_resolve_reads_the_database_not_the_seed_list():
    assert assets.resolve("return idler 14").status == "matched"
    _execute("DELETE FROM asset_registry WHERE asset_id = 'RETURN-IDLER-14'")
    assert assets.resolve("return idler 14").status == "unknown"       # and it is not re-seeded
    assert assets.resolve("idler 14").asset_id == "CARRY-IDLER-14"
    assert assets.get("RETURN-IDLER-14") is None

    assert assets.resolve("belt cleaner").status == "unknown"
    _execute("INSERT INTO asset_registry VALUES (?,?,?,?,?,?)",
             ("BELT-CLEANER-02", "CV-102", "S5", "Belt cleaner 2", "BELT-CLEANER", 2))
    match = assets.resolve("belt cleaner two", station="tail")
    assert (match.status, match.asset_id, match.station_id) == ("matched", "BELT-CLEANER-02", "S5")
