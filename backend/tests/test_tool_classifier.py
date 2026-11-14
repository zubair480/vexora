"""Rule baseline and forest wrapper. Synthetic signals only (pipeline logic, not real accuracy)."""
from collections import Counter

import numpy as np
import pytest

from backend import config, imu_features, models
from backend import tool_classifier as tc
from backend.tests.test_tool_synth_helpers import make

N = len(imu_features.FEATURE_NAMES)


def feats(kind, seconds=10, hz=50.0, seed=0):
    return imu_features.feature_matrix(imu_features.extract_windows(make(kind, seconds, hz, seed)))


def predicted(clf, X):
    return [tc.ACTIVITIES[i] for i in clf.predict_proba(X).argmax(axis=1)]


@pytest.fixture(scope="module")
def dataset():
    X, y = [], []
    for cls, kind in (("stationary", "still"), ("walking", "walking"), ("gesture", "gesture"),
                      ("handling", "gesture"), ("tool_use", "tool")):
        for seed in range(2):
            f = feats(kind, 8, seed=seed + (10 if cls == "handling" else 0))
            X.append(f)
            y += [cls] * len(f)
    return np.vstack(X), np.array(y)


@pytest.fixture(scope="module")
def forest(dataset):
    return tc.ForestClassifier(n_estimators=15).fit(*dataset)


def test_constants_match_models():
    assert tc.ACTIVITIES == models.ACTIVITIES
    assert set(tc.LABEL_TO_CLASS) == set(models.LABELS)
    assert set(tc.LABEL_TO_CLASS.values()) == set(tc.TRAIN_CLASSES)
    assert tc.CLASS_TO_ACTIVITY["handling"] == "gesture"
    assert all(tc.CLASS_TO_ACTIVITY[c] in tc.ACTIVITIES for c in tc.TRAIN_CLASSES)


@pytest.mark.parametrize("hz", [50.0, 104.0, 208.0])
@pytest.mark.parametrize("kind,activity", [("still", "stationary"), ("walking", "walking"), ("tool", "tool_use")])
def test_rules_classify_clear_kinds(kind, activity, hz):
    got = predicted(tc.RuleClassifier(), feats(kind, hz=hz, seed=3))
    assert got and all(a == activity for a in got), Counter(got)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_rules_gesture_is_never_tool_use(seed):
    got = Counter(predicted(tc.RuleClassifier(), feats("gesture", 14, seed=seed)))
    assert got["tool_use"] == 0
    assert got["gesture"] >= 0.6 * sum(got.values()), got


def test_rule_probabilities_valid_and_soft():
    X = np.vstack([feats(k, 6) for k in ("still", "walking", "gesture", "tool")])
    p = tc.RuleClassifier().predict_proba(X)
    assert p.shape == (len(X), len(tc.ACTIVITIES))
    assert np.isfinite(p).all() and (p >= 0).all()
    assert np.allclose(p.sum(axis=1), 1.0)
    assert (p.max(axis=1) < 1.0).all() and (p[:, tc.ACTIVITIES.index("unknown")] > 0).all()


def test_rules_degenerate_input_is_unknown_not_a_crash():
    rules = tc.RuleClassifier()
    p = rules.predict_proba(np.full((2, N), np.nan))
    assert np.allclose(p.sum(axis=1), 1.0) and predicted(rules, np.full((2, N), np.nan)) == ["unknown"] * 2
    assert np.allclose(rules.predict_proba(np.zeros(N)).sum(), 1.0)       # a single 1-D row works
    with pytest.raises(ValueError):
        rules.predict_proba(np.zeros((1, N - 1)))


def test_rules_info_says_untuned():
    info = tc.RuleClassifier().info
    assert info["model"] == "rules" and info["trained"] is False and "untuned" in info["note"]
    assert tc.RuleClassifier.name == "rules" and isinstance(tc.RULE_THRESHOLDS, dict)


def test_forest_predicts_and_folds_handling_into_gesture(forest, dataset):
    X, y = dataset
    raw, classes = forest.predict_proba_classes(X)
    assert set(classes) == set(tc.TRAIN_CLASSES) and raw.shape == (len(X), 5)
    p = forest.predict_proba(X)
    assert p.shape == (len(X), 5) and np.allclose(p.sum(axis=1), 1.0)
    assert np.all(p[:, tc.ACTIVITIES.index("unknown")] == 0)
    folded = raw[:, classes.index("gesture")] + raw[:, classes.index("handling")]
    assert np.allclose(p[:, tc.ACTIVITIES.index("gesture")], folded)
    got = np.array(predicted(forest, X))
    assert set(got[y == "tool_use"]) == {"tool_use"} and set(got[y == "walking"]) == {"walking"}
    assert "tool_use" not in set(got[y == "handling"]) and (got[y == "handling"] == "gesture").mean() > 0.5


def test_forest_info_and_feature_subset(forest, dataset):
    info = forest.info
    assert info["model"] == "random_forest" and info["n_windows"] == len(dataset[1])
    assert info["contains_synthetic"] is False and "trained_at" in info
    assert tuple(info["feature_names"]) == tuple(imu_features.features_for_model(50))
    assert not set(info["feature_names"]) & set(imu_features.QUALITY_FEATURES)
    custom = tc.ForestClassifier(n_estimators=5).fit(*dataset, feature_names=("dyn_acc_rms", "high_freq_ratio"),
                                                     info={"contains_synthetic": True, "n_sessions": 10})
    assert custom.info["feature_names"] == ["dyn_acc_rms", "high_freq_ratio"]
    assert custom.info["contains_synthetic"] is True and custom.info["n_sessions"] == 10
    assert custom.pipeline.named_steps["forest"].n_features_in_ == 2


def test_forest_rejects_unknown_labels_and_unfitted_use(dataset):
    X, y = dataset
    with pytest.raises(ValueError):
        tc.ForestClassifier(n_estimators=3).fit(X, np.where(y == "walking", "tool_active", y))
    with pytest.raises(RuntimeError):
        tc.ForestClassifier().predict_proba(X)


def test_forest_save_load_round_trip(forest, dataset, tmp_path):
    path = forest.save(tmp_path / "sub" / "m.joblib")
    assert path.exists()
    again = tc.ForestClassifier.load(path)
    assert again.name == "random_forest" and again.info == forest.info
    assert again.feature_names == forest.feature_names and again.classes == forest.classes
    assert np.allclose(again.predict_proba(dataset[0]), forest.predict_proba(dataset[0]))


def test_default_path_follows_config_models(forest, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS", tmp_path)
    assert forest.save() == tmp_path / "toolsense_rf.joblib"
    assert tc.load_active().name == "random_forest"
    assert tc.ForestClassifier.load().classes == forest.classes


def test_load_active_falls_back_to_rules(dataset, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS", tmp_path)
    assert tc.load_active().name == "rules"                              # no file
    (tmp_path / "toolsense_rf.joblib").write_bytes(b"this is not a model")
    broken = tc.load_active()                                            # broken file
    assert broken.name == "rules" and "ignored" in broken.info["note"]
    synth = tc.ForestClassifier(n_estimators=5).fit(*dataset, info={"contains_synthetic": True})
    synth.save()
    refused = tc.load_active()                                           # synthetic-flagged
    assert refused.name == "rules" and "synthetic" in refused.info["note"]
    assert tc.load_active(allow_synthetic=True).name == "random_forest"


def test_classify_window(forest):
    for clf, kind, activity in ((tc.RuleClassifier(), "tool", "tool_use"), (tc.RuleClassifier(), "still", "stationary"),
                                (forest, "tool", "tool_use"), (forest, "walking", "walking")):
        window = imu_features.latest_window(make(kind, 4, 50.0, seed=5))
        out = tc.classify(clf, window)
        assert set(out) == {"activity", "confidence", "tool_probability", "probabilities", "model"}
        assert out["activity"] == activity and out["model"] == clf.name
        assert tuple(out["probabilities"]) == tc.ACTIVITIES
        assert out["confidence"] == pytest.approx(max(out["probabilities"].values()))
        assert out["tool_probability"] == pytest.approx(out["probabilities"]["tool_use"])
        assert sum(out["probabilities"].values()) == pytest.approx(1.0)
