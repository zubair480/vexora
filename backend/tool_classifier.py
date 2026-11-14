"""Activity classifiers for wrist IMU windows: a transparent rule baseline and a random forest.

Both answer "what is the wearer probably doing" (stationary / walking / gesture / tool_use /
unknown) from `imu_features` window features. This is an ESTIMATE of activity. Wrist
acceleration is used as activity evidence only, never as a tool vibration magnitude.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backend import config
from backend.imu_features import ANALYSIS_BANDS_HZ, FEATURE_NAMES, WindowFeatures, features_for_model

ACTIVITIES = ("stationary", "walking", "gesture", "tool_use", "unknown")   # == models.ACTIVITIES
TRAIN_CLASSES = ("stationary", "walking", "gesture", "handling", "tool_use")
LABEL_TO_CLASS = {"stationary": "stationary", "walking": "walking", "gesture": "gesture",
                  "tool_idle": "handling", "tool_active": "tool_use"}
# "handling" (holding an idle tool) is reported as gesture-like: it is movement, not tool use.
CLASS_TO_ACTIVITY = {"stationary": "stationary", "walking": "walking", "gesture": "gesture",
                     "handling": "gesture", "tool_use": "tool_use", "unknown": "unknown"}
MODEL_NAME = "toolsense_rf.joblib"
SYNTHETIC_MODEL_NAME = "toolsense_rf.synthetic.joblib"   # pipeline-test models never share the real file
RATE_NOTE = "model needs bands above this stream's bandwidth"
MODEL_PATH = config.MODELS / MODEL_NAME   # import-time default only; use model_path() (tests monkeypatch config.MODELS)

# UNTUNED GUESSES pending real band recordings. They were only adjusted far enough to separate
# the crude synthetic test signals; do not read them as validated values. Accel in g, gyro deg/s.
RULE_THRESHOLDS = {
    "still_dyn_rms": 0.03,       # dynamic accel RMS below this (and quiet gyro) -> still
    "still_gyro_mean": 6.0,      # mean gyro magnitude below this -> still
    "tool_high_freq_ratio": 0.5, # share of accel power above 8 Hz that suggests vibration-like motion
    "tool_min_dyn_rms": 0.08,    # vibration-like motion must also carry sustained energy
    "walk_min_hz": 0.5,          # gait band for the dominant frequency
    "walk_max_hz": 3.0,
    "walk_low_band_share": 0.5,  # share of power in the 0.5-3 Hz band for rhythmic gait
    "walk_min_dyn_rms": 0.08,    # moderate energy needed for walking
    "walk_max_crest": 2.1,      # peak / RMS: sustained rhythm is low, short bursts (gestures) high
    "unknown_floor": 0.05,       # baseline doubt kept in every prediction
}


def model_path(path=None) -> Path:
    """Model file location, resolving `config.MODELS` at call time."""
    return Path(path) if path is not None else Path(config.MODELS) / MODEL_NAME


def atomic_replace(path: Path, write) -> Path:
    """Run `write(tmp)` on a temp file in the same folder, then swap it in (no half-written files)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def _col(name: str) -> int:
    return FEATURE_NAMES.index(name)


def _band_cols() -> list[int]:
    """Columns of the band power features, in ANALYSIS_BANDS_HZ order."""
    cols = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith("band_power")]
    return cols[:len(ANALYSIS_BANDS_HZ)]


def _above(x, threshold, width=0.15):
    """Soft step: 0.5 at the threshold, `width` is the relative transition width."""
    scale = max(abs(threshold) * width, 1e-9)
    return 1.0 / (1.0 + np.exp(-np.clip((x - threshold) / scale, -40, 40)))


def _as_matrix(X) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    if X.shape[1] != len(FEATURE_NAMES):
        raise ValueError(f"expected {len(FEATURE_NAMES)} feature columns (FEATURE_NAMES order), got {X.shape[1]}")
    return X


class RuleClassifier:
    """Threshold baseline. Thresholds are untuned guesses (see RULE_THRESHOLDS), not a trained model."""
    name = "rules"

    def __init__(self, thresholds: dict | None = None):
        self.thresholds = {**RULE_THRESHOLDS, **(thresholds or {})}
        self.info = {"model": "rules", "trained": False,
                     "note": "untuned threshold baseline, guesses pending real recordings; estimate only"}

    def predict_proba(self, X) -> np.ndarray:
        X = _as_matrix(X)
        th = self.thresholds
        bad = ~np.isfinite(X).all(axis=1)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        rms = X[:, _col("dyn_acc_rms")]
        gyro = X[:, _col("gyro_mag_mean")]
        dom = X[:, _col("dom_freq_1")]
        hf = X[:, _col("high_freq_ratio")]
        peak = X[:, _col("acc_peak")]
        bands = X[:, _band_cols()]
        low_share = bands[:, 0] / np.maximum(bands.sum(axis=1), 1e-12)   # 0.5-3 Hz share
        crest = peak / np.maximum(rms, 1e-9)

        still = (1 - _above(rms, th["still_dyn_rms"])) * (1 - _above(gyro, th["still_gyro_mean"]))
        moving = 1 - still
        vib = _above(hf, th["tool_high_freq_ratio"]) * _above(rms, th["tool_min_dyn_rms"])
        rhythm = (_above(dom, th["walk_min_hz"]) * (1 - _above(dom, th["walk_max_hz"]))
                  * _above(low_share, th["walk_low_band_share"]) * _above(rms, th["walk_min_dyn_rms"])
                  * (1 - _above(crest, th["walk_max_crest"])))
        scores = np.zeros((len(X), len(ACTIVITIES)))
        scores[:, 0] = still
        scores[:, 1] = moving * (1 - vib) * rhythm
        scores[:, 2] = moving * (1 - vib) * (1 - rhythm)
        scores[:, 3] = moving * vib
        # nothing convincing -> unknown takes over once the best class score drops near 0.5
        scores[:, 4] = th["unknown_floor"] + (1 - scores[:, :4].max(axis=1))
        scores[bad] = (0, 0, 0, 0, 1)
        return scores / scores.sum(axis=1, keepdims=True)


class ForestClassifier:
    """RandomForest (in a Pipeline with StandardScaler) over a named subset of FEATURE_NAMES."""
    name = "random_forest"

    def __init__(self, n_estimators: int = 120, random_state: int = 0):
        self.n_estimators, self.random_state = n_estimators, random_state
        self.pipeline: Pipeline | None = None
        self.feature_names: tuple[str, ...] = ()
        self.classes: list[str] = []
        self.info: dict = {"model": "random_forest", "trained": False}

    def _select(self, X) -> np.ndarray:
        X = np.nan_to_num(_as_matrix(X), nan=0.0, posinf=0.0, neginf=0.0)
        return X[:, [_col(n) for n in self.feature_names]]

    def fit(self, X, y, feature_names=None, info=None) -> "ForestClassifier":
        y = np.asarray(y, dtype=str)
        unknown = sorted(set(y) - set(TRAIN_CLASSES))
        if unknown:
            raise ValueError(f"labels not in TRAIN_CLASSES: {unknown}")
        self.feature_names = tuple(feature_names) if feature_names is not None else tuple(features_for_model(50))
        self.pipeline = Pipeline([
            ("scale", StandardScaler()),
            ("forest", RandomForestClassifier(n_estimators=self.n_estimators, class_weight="balanced",
                                              random_state=self.random_state, min_samples_leaf=2)),
        ]).fit(self._select(X), y)
        self.classes = [str(c) for c in self.pipeline.classes_]
        self.info = {"model": "random_forest", "trained": True, "trained_at": time.time(),
                     "feature_names": list(self.feature_names), "classes": list(self.classes),
                     "n_sessions": None, "n_windows": int(len(y)), "sources": [],
                     "contains_synthetic": False, "sample_hz_min": None, "sample_hz_max": None,
                     **(info or {})}
        return self

    def predict_proba_classes(self, X) -> tuple[np.ndarray, list[str]]:
        """Raw per-TRAIN_CLASS probabilities (only the classes seen in training)."""
        if self.pipeline is None:
            raise RuntimeError("ForestClassifier is not fitted")
        return self.pipeline.predict_proba(self._select(X)), list(self.classes)

    def predict_proba(self, X) -> np.ndarray:
        proba, classes = self.predict_proba_classes(X)
        out = np.zeros((len(proba), len(ACTIVITIES)))
        for j, cls in enumerate(classes):          # handling folds into the gesture column
            out[:, ACTIVITIES.index(CLASS_TO_ACTIVITY[cls])] += proba[:, j]
        return out / np.maximum(out.sum(axis=1, keepdims=True), 1e-12)

    def save(self, path=None) -> Path:
        if self.pipeline is None:
            raise RuntimeError("ForestClassifier is not fitted")
        data = {"pipeline": self.pipeline, "feature_names": list(self.feature_names),
                "classes": list(self.classes), "info": self.info,
                "n_estimators": self.n_estimators, "random_state": self.random_state}
        return atomic_replace(model_path(path), lambda tmp: joblib.dump(data, tmp))

    @classmethod
    def load(cls, path=None) -> "ForestClassifier":
        data = joblib.load(model_path(path))
        if not isinstance(data, dict) or "pipeline" not in data:
            raise ValueError("not a toolsense model file")
        missing = [n for n in data["feature_names"] if n not in FEATURE_NAMES]
        if missing:
            raise ValueError(f"model uses features this build does not compute: {missing}")
        clf = cls(data.get("n_estimators", 120), data.get("random_state", 0))
        clf.pipeline, clf.feature_names = data["pipeline"], tuple(data["feature_names"])
        clf.classes, clf.info = list(data["classes"]), dict(data["info"])
        return clf


def load_active(path=None, allow_synthetic=False):
    """The trained forest if a usable model file exists, else the rule baseline. Never raises.

    Usable = loadable, not synthetic-flagged (unless allowed), and trained with `tool_use` plus
    at least one other class: a model that cannot say "tool" must never drive the live detector.
    """
    rules = RuleClassifier()
    try:
        path = model_path(path)
        if not path.exists():
            return rules
        clf = ForestClassifier.load(path)
        if clf.info.get("contains_synthetic") and not allow_synthetic:
            rules.info["note"] += "; saved model ignored: trained on synthetic data"
            return rules
        if "tool_use" not in clf.classes or len(set(clf.classes) - {"tool_use"}) < 1:
            rules.info["note"] += f"; saved model ignored: no usable tool class (classes {clf.classes})"
            return rules
        return clf
    except Exception as exc:   # broken / incompatible file: the live path must keep working
        rules.info["note"] += f"; saved model ignored: {type(exc).__name__}: {exc}"
        return rules


def classify(clf, window: WindowFeatures) -> dict:
    """Classify one window: estimated activity with soft probabilities.

    Rate guard: a model trained on bands this stream cannot resolve (its Nyquist is too low)
    is not used for the window; the rule baseline answers instead and the result says so.
    """
    note = None
    needed = getattr(clf, "feature_names", None)
    if needed and not set(needed) <= set(features_for_model(window.sample_hz)):
        clf, note = RuleClassifier(), RATE_NOTE
    out = _classify(clf, window)
    if note:
        out["note"] = note
    return out


def _classify(clf, window: WindowFeatures) -> dict:
    p = np.asarray(clf.predict_proba(np.asarray(window.values, dtype=float)[None, :]))[0]
    best = int(np.argmax(p))
    return {"activity": ACTIVITIES[best], "confidence": float(p[best]),
            "tool_probability": float(p[ACTIVITIES.index("tool_use")]),
            "probabilities": {a: float(v) for a, v in zip(ACTIVITIES, p)},
            "model": clf.name}
