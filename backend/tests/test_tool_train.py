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
    model = tc.ForestClassifier.load(mod / "toolsense_rf.joblib")   # saved so the live path can be exercised
    assert model.info["accuracy_claim"] is False and model.info["contains_synthetic"] is True
    assert tc.load_active().name == "rules"


def test_run_single_class_fits_nothing(dirs):
    rec, mod = dirs
    write_set(rec, {"tool_active": 4}, seconds=4.0)
    report = train.run(include_synthetic=True, verbose=False)
    assert report["status"] == "insufficient_data" and report["accuracy_claim"] is False
    assert report["model_saved"] is False and not (mod / "toolsense_rf.joblib").exists()


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
    on_disk = json.loads((mod / train.REPORT_NAME).read_text(encoding="utf-8"))
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
    path = mod / "toolsense_rf.joblib"
    assert path.exists()
    monkeypatch.setattr(config, "MODELS", mod)
    refused = tc.load_active()
    assert refused.name == "rules" and "synthetic" in refused.info["note"]
    model = tc.load_active(allow_synthetic=True)
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
