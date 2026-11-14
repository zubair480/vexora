"""Training pipeline: loading, session-level split, honest reporting. Synthetic signals only."""
import json

import numpy as np
import pytest

from backend import config, train
from backend import tool_classifier as tc
from backend.tests.test_tool_synth_helpers import make, sequence, write_recording

KIND = {"stationary": "still", "walking": "walking", "gesture": "gesture",
        "tool_idle": "gesture", "tool_active": "tool"}


def write_set(root, per_label, seconds=8.0, source="synthetic"):
    """`per_label` sessions of each label (or a {label: count} dict), short synthetic recordings."""
    counts = per_label if isinstance(per_label, dict) else {label: per_label for label in KIND}
    for li, (label, count) in enumerate(counts.items()):
        for i in range(count):
            write_recording(root, f"{label}-{i}", label, make(KIND[label], seconds, 50.0, seed=li * 20 + i),
                            source=source)


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """config.RECORDINGS / config.MODELS pointed at tmp_path: never the real folders."""
    rec, mod = tmp_path / "recordings", tmp_path / "models"
    rec.mkdir()
    monkeypatch.setattr(config, "RECORDINGS", rec)
    monkeypatch.setattr(config, "MODELS", mod)
    return rec, mod


@pytest.fixture(scope="module")
def ok_run(tmp_path_factory):
    """One full run on 3 synthetic sessions per class, shared by the tests that inspect it."""
    base = tmp_path_factory.mktemp("ok")
    rec, mod = base / "recordings", base / "models"
    write_set(rec / "fixtures", 3)                      # nested folder: loading is recursive
    report = train.run(rec, mod, include_synthetic=True, verbose=False, n_estimators=15)
    return report, rec, mod


def test_load_recordings_filters_and_lists_skipped(dirs):
    rec, _ = dirs
    write_recording(rec, "real-1", "walking", make("walking", 4), source="band")
    write_recording(rec / "fixtures", "syn-1", "tool_active", make("tool", 4))
    write_recording(rec, "nolabel", "not_a_label", make("still", 4), source="band")
    (rec / "broken").mkdir()
    (rec / "broken" / "meta.json").write_text("{not json", encoding="utf-8")
    (rec / "broken" / "samples.csv").write_text("n,t\n", encoding="utf-8")
    got = train.load_recordings()
    assert [r.session_id for r in got] == ["real-1"]
    assert got[0].label == "walking" and got[0].samples.shape == (200, 9) and got[0].path == rec / "real-1"
    assert len(got.skipped) == 3 and any("synthetic" in s["reason"] for s in got.skipped)
    both = train.load_recordings(rec, include_synthetic=True)
    assert sorted(r.session_id for r in both) == ["real-1", "syn-1"]
    assert train.load_recordings(rec / "does-not-exist") == []


def test_collection_status():
    empty = train.collection_status([])
    assert empty["complete"] is False
    assert {k: v["missing"] for k, v in empty.items() if k != "complete"} == train.TARGETS
    recs = [train.Recording(f"s{i}", "tool_active", {}, np.zeros((2, 9)), None) for i in range(4)]
    status = train.collection_status(recs)
    assert status["tool_active"] == {"have": 4, "target": 10, "missing": 6}
    assert status["walking"] == {"have": 0, "target": 5, "missing": 5}
    full = [train.Recording(f"{label}{i}", label, {}, np.zeros((2, 9)), None)
            for label, n in train.TARGETS.items() for i in range(n)]
    assert train.collection_status(full)["complete"] is True


def test_build_dataset_rows_and_groups():
    recs = [train.Recording("a", "tool_idle", {}, make("gesture", 6), None),
            train.Recording("b", "tool_active", {}, make("tool", 6), None)]
    X, y, groups, meta = train.build_dataset(recs)
    assert X.shape[0] == len(y) == len(groups) == len(meta) > 4
    assert set(y[groups == "a"]) == {"handling"} and set(y[groups == "b"]) == {"tool_use"}
    assert meta[0]["session_id"] == "a" and np.isfinite(X).all()
    X0, y0, g0, m0 = train.build_dataset([])
    assert X0.shape[0] == 0 and len(y0) == len(g0) == len(m0) == 0


def test_split_by_session_has_no_session_overlap():
    classes = ["stationary", "walking", "gesture", "handling", "tool_use"]
    y, groups = [], []
    for ci, cls in enumerate(classes):
        for s in range(4):
            y += [cls] * (5 + s)
            groups += [f"{cls}-{s}"] * (5 + s)
    y, groups = np.array(y), np.array(groups)
    for seed in range(5):
        tr, te = train.split_by_session(y, groups, seed=seed)
        assert set(groups[tr]) & set(groups[te]) == set()
        assert len(tr) + len(te) == len(y) and not set(tr) & set(te)
        assert set(y[tr]) == set(classes) and set(y[te]) == set(classes)
        for g in set(groups):                            # one recording's windows stay together
            assert np.isin(np.flatnonzero(groups == g), te).all() or np.isin(np.flatnonzero(groups == g), tr).all()
    a, b = train.split_by_session(y, groups, seed=1), train.split_by_session(y, groups, seed=1)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_split_keeps_single_session_classes_in_train():
    y = np.array(["walking"] * 4 + ["tool_use"] * 8)
    groups = np.array(["w0"] * 4 + ["t0"] * 4 + ["t1"] * 4)
    tr, te = train.split_by_session(y, groups)
    assert "w0" in set(groups[tr]) and set(y[te]) == {"tool_use"} and len(set(groups[te])) == 1


def test_run_empty_dir_is_no_data_and_writes_no_model(dirs, capsys):
    rec, mod = dirs
    report = train.run()
    assert report["status"] == "no_data" and report["accuracy_claim"] is False
    assert all(report["collection"][label]["missing"] == n for label, n in train.TARGETS.items())
    assert not (mod / "toolsense_rf.joblib").exists()
    assert json.loads((mod / train.REPORT_NAME).read_text(encoding="utf-8"))["status"] == "no_data"
    assert "honesty" in report and "baseline" not in report
    assert "missing" in capsys.readouterr().out
    assert tc.load_active().name == "rules"


def test_default_run_ignores_synthetic_recordings(dirs):
    rec, mod = dirs
    write_set(rec, 3, seconds=4.0)
    report = train.run(verbose=False)
    assert report["status"] == "no_data" and report["contains_synthetic"] is False
    assert len(report["skipped"]) == 15 and not (mod / "toolsense_rf.joblib").exists()


def test_run_few_sessions_is_insufficient_and_claims_nothing(dirs, capsys):
    rec, mod = dirs
    write_set(rec, {"stationary": 2, "tool_active": 2, "walking": 1})
    report = train.run(include_synthetic=True, n_estimators=10)
    out = capsys.readouterr().out
    assert report["status"] == "insufficient_data" and report["accuracy_claim"] is False
    assert report["sessions_per_class"] == {"stationary": 2, "walking": 1, "gesture": 0, "tool_idle": 0, "tool_active": 2}
    missing = {label: report["collection"][label]["missing"] for label in train.TARGETS}
    assert missing == {"stationary": 3, "walking": 4, "gesture": 5, "tool_idle": 5, "tool_active": 8}
    assert report["collection"]["complete"] is False
    assert "NOT A VALID ACCURACY ESTIMATE: too few sessions" in out and "must NOT be presented as real" in out
    assert set(report["test_sessions"]) & set(report["train_sessions"]) == set()
    assert len(report["test_sessions"]) == 2             # one held out per class that has two
    model = tc.ForestClassifier.load(mod / tc.SYNTHETIC_MODEL_NAME)  # saved so the pipeline can be exercised
    assert not (mod / tc.MODEL_NAME).exists() and not (mod / train.REPORT_NAME).exists()
    assert model.info["accuracy_claim"] is False and model.info["contains_synthetic"] is True
    assert tc.load_active().name == "rules"


def test_run_single_class_fits_nothing(dirs):
    rec, mod = dirs
    write_set(rec, {"tool_active": 4}, seconds=4.0)
    report = train.run(include_synthetic=True, verbose=False)
    assert report["status"] == "insufficient_data" and report["accuracy_claim"] is False
    assert report["model_saved"] is False and not list(mod.glob("*.joblib"))


def test_run_ok_report(ok_run):
    report, rec, mod = ok_run
    assert report["status"] == "ok" and report["contains_synthetic"] is True
    assert report["accuracy_claim"] is False             # synthetic data never supports a claim
    assert report["sources"] == ["synthetic"] and report["final_model_fit_on"] == "all_sessions"
    assert report["sessions_per_class"] == {label: 3 for label in train.TARGETS}
    for key in ("status", "accuracy_claim", "collection", "sessions_per_class", "sources", "contains_synthetic",
                "window_s", "overlap", "generated_at", "skipped", "honesty", "labels", "train_sessions",
                "test_sessions", "trigger_time", "baseline", "forest"):
        assert key in report, key
    on_disk = json.loads((mod / train.SYNTHETIC_REPORT_NAME).read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(report))
    n = len(report["labels"])
    for key in ("baseline", "forest"):
        cm = np.array(on_disk[key]["confusion_matrix"])
        assert cm.shape == (n, n) and cm.sum() == on_disk[key]["n_windows"] > 0
        assert "accuracy" in on_disk[key]["classification_report"] or "micro avg" in on_disk[key]["classification_report"]
    assert set(report["train_sessions"]) & set(report["test_sessions"]) == set()
    assert len(report["test_sessions"]) == 5 and len(report["train_sessions"]) == 10
    tool = report["labels"].index("tool_use")            # crude signals: tool windows are found
    assert np.array(report["forest"]["confusion_matrix"])[tool, tool] == np.array(report["forest"]["confusion_matrix"])[tool].sum()


def test_ok_model_is_flagged_and_refused_by_default(ok_run, monkeypatch):
    report, rec, mod = ok_run
    path = mod / tc.SYNTHETIC_MODEL_NAME
    assert path.exists() and not (mod / tc.MODEL_NAME).exists() and not (mod / train.REPORT_NAME).exists()
    monkeypatch.setattr(config, "MODELS", mod)
    assert tc.load_active().name == "rules"              # the default path never picks the synthetic file
    refused = tc.load_active(path)                       # and pointing at it is still refused
    assert refused.name == "rules" and "synthetic" in refused.info["note"]
    model = tc.load_active(path, allow_synthetic=True)
    assert model.info["sample_hz_max"] == pytest.approx(50.0, rel=0.02) and model.info["accuracy_claim"] is False
    assert model.name == "random_forest" and model.info["contains_synthetic"] is True
    assert model.info["n_sessions"] == 15 and model.info["sample_hz_min"] == pytest.approx(50.0, rel=0.02)
    assert set(model.classes) == set(tc.TRAIN_CLASSES)
    assert not set(model.info["feature_names"]) & {"saturated", "completeness", "band_power_25_50", "band_power_50_100"}


def test_main_cli(dirs, capsys):
    rec, mod = dirs
    assert train.main(["--recordings", str(rec), "--models", str(mod)]) == 0
    assert "no_data" in capsys.readouterr().out


def test_trigger_time_comparison_none_without_ground_truth():
    rec = train.Recording("x", "tool_active", {}, make("tool", 3), None)
    assert train.trigger_time_comparison(rec, tc.RuleClassifier()) is None


def test_trigger_time_comparison_matches_known_tool_seconds(dirs):
    pytest.importorskip("backend.tool_sessions")
    rec, _ = dirs
    arr = sequence([("still", 6), ("tool", 20), ("still", 6)], seed=4)
    write_recording(rec, "trig-1", "tool_active", arr, trigger_seconds=20.0)
    write_recording(rec, "trig-2", "tool_active", arr, ground_truth_trigger_s=20.0)
    for recording in train.load_recordings(include_synthetic=True):
        out = train.trigger_time_comparison(recording, tc.RuleClassifier())
        assert set(out) == {"session_id", "truth_s", "predicted_s", "error_s", "abs_error_s", "relative_error", "n_sessions"}
        assert out["truth_s"] == 20.0 and out["n_sessions"] == 1
        assert out["abs_error_s"] <= 3.0 and out["error_s"] == pytest.approx(out["predicted_s"] - 20.0)
        assert out["relative_error"] == pytest.approx(out["abs_error_s"] / 20.0)
    still = train.Recording("s", "stationary", {"trigger_seconds": 0}, make("still", 8), None)
    assert train.trigger_time_comparison(still, tc.RuleClassifier())["predicted_s"] == 0.0


# ---- review fixes -------------------------------------------------------------------------
# Below, synthetic signals are written with source="band" ONLY to exercise the claim logic in
# tmp folders; they are still synthetic test signals and prove nothing about real accuracy.

@pytest.fixture(scope="module")
def band_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("band")
    rec, mod = base / "recordings", base / "models"
    write_set(rec, 3, source="band")
    return train.run(rec, mod, verbose=False, n_estimators=15), rec, mod


def test_band_run_claims_and_uses_real_files(band_run):
    report, rec, mod = band_run
    assert report["status"] == "ok" and report["accuracy_claim"] is True and report["accuracy_claim_reasons"] == []
    assert report["sources"] == ["band"] and report["contains_synthetic"] is False
    assert (mod / tc.MODEL_NAME).exists() and (mod / train.REPORT_NAME).exists()
    assert not (mod / tc.SYNTHETIC_MODEL_NAME).exists() and not (mod / train.SYNTHETIC_REPORT_NAME).exists()
    model = tc.load_active(mod / tc.MODEL_NAME)
    assert model.name == "random_forest" and model.info["accuracy_claim"] is True
    assert model.info["sample_hz_min"] <= model.info["sample_hz_max"]
    assert not list(mod.glob("*.tmp")) and not list(mod.glob(".*"))      # atomic writes leave nothing behind


def test_run_without_tool_class_never_claims_or_saves(dirs, capsys):
    rec, mod = dirs                                      # reviewer scenario: 3 stationary + 3 walking, real source
    write_set(rec, {"stationary": 3, "walking": 3}, source="band")
    report = train.run(n_estimators=10)
    out = capsys.readouterr().out
    assert report["status"] == "insufficient_data" and report["accuracy_claim"] is False
    assert any("tool_active" in r for r in report["accuracy_claim_reasons"])
    assert report["collection"]["tool_active"]["missing"] == 10 and "tool_active" in out and train.NOT_VALID in out
    assert report["model_saved"] is False and not list(mod.glob("*.joblib"))
    assert tc.load_active().name == "rules"


@pytest.mark.parametrize("source", ["replay", None, "synthetic"])
def test_only_band_source_supports_a_claim(dirs, source):
    rec, mod = dirs
    write_set(rec, {"stationary": 3, "tool_active": 3}, seconds=6.0, source=source)
    report = train.run(include_synthetic=source == "synthetic", verbose=False, n_estimators=10)
    assert report["status"] == "ok" and report["accuracy_claim"] is False
    assert any("source" in r for r in report["accuracy_claim_reasons"])
    name = tc.SYNTHETIC_MODEL_NAME if source == "synthetic" else tc.MODEL_NAME
    assert tc.ForestClassifier.load(mod / name).info["accuracy_claim"] is False


def test_sessions_counted_from_windows_not_folders(dirs):
    rec, mod = dirs
    write_set(rec, {"stationary": 3, "tool_active": 2}, seconds=6.0, source="band")
    write_recording(rec, "tool_active-tiny", "tool_active", make("tool", 0.2), source="band")   # no window
    report = train.run(verbose=False, n_estimators=10)
    assert report["sessions_per_class"]["tool_active"] == 2 and report["collection"]["tool_active"]["have"] == 2
    assert report["status"] == "insufficient_data" and report["accuracy_claim"] is False
    assert any("tool_active-tiny" in s["path"] and "windows" in s["reason"] for s in report["skipped"])
    assert tc.ForestClassifier.load(mod / tc.MODEL_NAME).info["n_sessions"] == 5


def test_no_claim_when_a_class_is_missing_from_the_held_out_set(dirs, monkeypatch):
    rec, mod = dirs
    write_set(rec, {"stationary": 3, "tool_active": 3}, seconds=6.0, source="band")

    def lopsided(y, groups, **kw):                       # holds out one stationary session only
        test = groups == "stationary-0"
        return np.flatnonzero(~test), np.flatnonzero(test)
    monkeypatch.setattr(train, "split_by_session", lopsided)
    report = train.run(verbose=False, n_estimators=10)
    assert report["accuracy_claim"] is False and report["status"] == "insufficient_data"
    assert any("held-out" in r and "tool_use" in r for r in report["accuracy_claim_reasons"])

    monkeypatch.setattr(train, "split_by_session", lambda y, groups, **kw: (np.arange(len(y)), np.array([], int)))
    report = train.run(verbose=False, n_estimators=10)   # nothing held out at all
    assert report["accuracy_claim"] is False and report["status"] == "insufficient_data"
    assert "baseline" not in report and any("held out" in r for r in report["accuracy_claim_reasons"])


def test_synthetic_run_does_not_overwrite_real_model_or_report(band_run, monkeypatch):
    report, rec, mod = band_run
    before = {n: (mod / n).read_bytes() for n in (tc.MODEL_NAME, train.REPORT_NAME)}
    synth = train.run(rec, mod, include_synthetic=True, verbose=False, n_estimators=5)
    try:
        assert synth["model_saved"] is True and (mod / tc.SYNTHETIC_MODEL_NAME).exists()
        assert (mod / train.SYNTHETIC_REPORT_NAME).exists()
        assert {n: (mod / n).read_bytes() for n in before} == before
        monkeypatch.setattr(config, "MODELS", mod)
        live = tc.load_active()
        assert live.name == "random_forest" and live.info["trained_at"] == pytest.approx(
            tc.ForestClassifier.load(mod / tc.MODEL_NAME).info["trained_at"])
    finally:
        (mod / tc.SYNTHETIC_MODEL_NAME).unlink(missing_ok=True)
        (mod / train.SYNTHETIC_REPORT_NAME).unlink(missing_ok=True)


def test_split_uses_group_shuffle_split_and_falls_back(monkeypatch):
    classes = ["stationary", "walking", "tool_use"]
    y = np.array([c for c in classes for _ in range(3 * 4)])
    groups = np.array([f"{c}-{i}" for c in classes for i in range(3) for _ in range(4)])
    calls = []
    real = train.GroupShuffleSplit

    class Spy(real):
        def __init__(self, *a, **kw):
            calls.append(kw.get("random_state"))
            super().__init__(*a, **kw)
    monkeypatch.setattr(train, "GroupShuffleSplit", Spy)
    tr, te = train.split_by_session(y, groups, seed=7)
    assert calls and calls[0] == 7 and len(calls) <= 50
    assert set(groups[tr]) & set(groups[te]) == set() and set(y[tr]) == set(y[te]) == set(classes)

    class Useless:                                       # never yields an acceptable split
        def __init__(self, *a, **kw):
            calls.append("useless")

        def split(self, X, y, groups):
            yield np.flatnonzero(groups != "tool_use-0"), np.flatnonzero(groups == "tool_use-0")
    monkeypatch.setattr(train, "GroupShuffleSplit", Useless)
    tr, te = train.split_by_session(y, groups, seed=7)
    assert calls.count("useless") == 50
    assert set(groups[tr]) & set(groups[te]) == set() and set(y[tr]) == set(y[te]) == set(classes)
    one = train.split_by_session(np.array(["walking"] * 3), np.array(["w"] * 3))     # single session
    assert len(one[0]) == 3 and len(one[1]) == 0
