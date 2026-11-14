"""Train and honestly evaluate the toolsense activity classifier from labelled recordings.

    python -m backend.train [--recordings DIR] [--models DIR] [--include-synthetic] [--window 1.5] [--overlap 0.5]

Evaluation is always on HELD-OUT SESSIONS (a session's windows never sit on both sides of the
split). With too few sessions no accuracy is claimed. Synthetic recordings are test-only and are
excluded unless explicitly included, in which case the model and report are flagged.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.metrics import classification_report, confusion_matrix

from backend import config, imu_features, models
from backend import tool_classifier as tc

TARGETS = {"stationary": 5, "walking": 5, "gesture": 5, "tool_idle": 5, "tool_active": 10}
MIN_SESSIONS_PER_CLASS = 3      # below this for any class present -> no accuracy claim
REPORT_NAME = "toolsense_report.json"
MIN_WINDOW_COMPLETENESS = 0.75  # windows with more missing data than this are not used
HONESTY = ("Estimated activity classifier from wrist motion, evaluated on held-out sessions; "
           "it is not a certified measurement of tool use or vibration exposure.")
NOT_VALID = "NOT A VALID ACCURACY ESTIMATE: too few sessions"


@dataclass
class Recording:
    session_id: str
    label: str
    meta: dict
    samples: np.ndarray
    path: Path


class RecordingList(list):
    """list[Recording] that also remembers what was skipped and why."""

    def __init__(self, items=(), skipped=None):
        super().__init__(items)
        self.skipped: list[dict] = skipped or []


def load_recordings(root=None, include_synthetic=False) -> RecordingList:
    """Every folder under root (recursive) holding samples.csv + meta.json with a known label."""
    root = Path(root) if root is not None else Path(config.RECORDINGS)
    out = RecordingList()
    if not root.exists():
        return out
    for meta_path in sorted(root.rglob("meta.json")):
        folder = meta_path.parent
        skip = lambda reason: out.skipped.append({"path": str(folder), "reason": reason})
        if not (folder / "samples.csv").exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            label = meta.get("label")
            if label not in models.LABELS:
                skip(f"unlabelled or unknown label: {label!r}")
                continue
            if meta.get("source") == "synthetic" and not include_synthetic:
                skip("synthetic recording excluded (use --include-synthetic for pipeline tests only)")
                continue
            samples = imu_features.load_samples_csv(folder / "samples.csv")
            if samples.ndim != 2 or len(samples) < 2:
                skip("no samples")
                continue
        except Exception as exc:
            skip(f"unreadable: {type(exc).__name__}: {exc}")
            continue
        out.append(Recording(str(meta.get("session_id") or folder.name), label, meta, samples, folder))
    return out


def collection_status(recordings) -> dict:
    """Per label: sessions we have vs TARGETS; `complete` when nothing is missing."""
    have = Counter(r.label for r in recordings)
    status = {label: {"have": have.get(label, 0), "target": target,
                      "missing": max(0, target - have.get(label, 0))} for label, target in TARGETS.items()}
    status["complete"] = all(v["missing"] == 0 for v in status.values())
    return status


def build_dataset(recordings, window_s=1.5, overlap=0.5):
    """One row per window: X (FEATURE_NAMES order), y (TRAIN_CLASSES), groups (session_id), meta."""
    rows, y, groups, meta = [], [], [], []
    for rec in recordings:
        for w in imu_features.extract_windows(rec.samples, window_s=window_s, overlap=overlap):
            if w.completeness < MIN_WINDOW_COMPLETENESS:
                continue
            rows.append(np.asarray(w.values, dtype=float))
            y.append(tc.LABEL_TO_CLASS[rec.label])
            groups.append(rec.session_id)
            meta.append({"session_id": rec.session_id, "label": rec.label, "start_time": w.start_time,
                         "end_time": w.end_time, "sample_hz": w.sample_hz, "completeness": w.completeness})
    X = np.vstack(rows) if rows else np.zeros((0, len(imu_features.FEATURE_NAMES)))
    return X, np.asarray(y, dtype=str), np.asarray(groups, dtype=str), meta


def split_by_session(y, groups, test_size=0.3, seed=0):
    """Session-level stratified split: whole sessions are held out, never single windows.

    Per class, at least one session is held out when the class has >= 2 sessions and at least
    one always stays in train, so every class appears in train.
    """
    y, groups = np.asarray(y, dtype=str), np.asarray(groups, dtype=str)
    rng = np.random.default_rng(seed)
    by_class: dict[str, list[str]] = {}
    for g in dict.fromkeys(groups):                     # a session carries one label
        by_class.setdefault(Counter(y[groups == g]).most_common(1)[0][0], []).append(g)
    test_groups: set[str] = set()
    for cls in sorted(by_class):
        sessions = sorted(by_class[cls])
        if len(sessions) < 2:
            continue
        k = min(len(sessions) - 1, max(1, int(round(test_size * len(sessions)))))
        test_groups.update(rng.permutation(sessions)[:k].tolist())
    is_test = np.isin(groups, sorted(test_groups))
    return np.flatnonzero(~is_test), np.flatnonzero(is_test)


def evaluate(clf, X, y, labels) -> dict:
    """classification_report + confusion_matrix in activity space (handling counts as gesture)."""
    truth = [tc.CLASS_TO_ACTIVITY[c] for c in y]
    proba = np.asarray(clf.predict_proba(X))
    pred = [tc.ACTIVITIES[i] for i in proba.argmax(axis=1)]
    labels = list(labels)
    return {"labels": labels, "n_windows": len(truth),
            "classification_report": classification_report(truth, pred, labels=labels, output_dict=True, zero_division=0),
            "confusion_matrix": confusion_matrix(truth, pred, labels=labels).tolist(),
            "text": classification_report(truth, pred, labels=labels, zero_division=0)}


def trigger_time_comparison(recording, clf=None, config=None) -> dict | None:
    """Detected tool-use seconds vs the recording's ground-truth trigger seconds (None if absent)."""
    truth = recording.meta.get("trigger_seconds", recording.meta.get("ground_truth_trigger_s"))
    if truth is None:
        return None
    from backend import tool_sessions            # lazy: the detector imports the classifier
    truth = float(truth)
    sessions = tool_sessions.detect_sessions(recording.samples, clf if clf is not None else tc.load_active(), config)
    predicted = float(sum(s["duration_s"] for s in sessions))
    error = predicted - truth
    return {"session_id": recording.session_id, "truth_s": truth, "predicted_s": predicted,
            "error_s": error, "abs_error_s": abs(error),
            "relative_error": (abs(error) / truth) if truth > 0 else None, "n_sessions": len(sessions)}


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    return obj


def run(recordings_dir=None, models_dir=None, include_synthetic=False, window_s=1.5, overlap=0.5,
        verbose=True, n_estimators=120) -> dict:
    """Load recordings, evaluate on held-out sessions, save model + report. Returns the report."""
    say = print if verbose else (lambda *a, **k: None)
    models_dir = Path(models_dir) if models_dir is not None else Path(config.MODELS)
    recs = load_recordings(recordings_dir, include_synthetic)
    collection = collection_status(recs)
    per_label = Counter(r.label for r in recs)
    sources = sorted({str(r.meta.get("source", "unknown")) for r in recs})
    synthetic = "synthetic" in sources
    report = {"status": "no_data", "accuracy_claim": False, "collection": collection,
              "sessions_per_class": {label: per_label.get(label, 0) for label in TARGETS},
              "sources": sources, "contains_synthetic": synthetic, "window_s": window_s,
              "overlap": overlap, "generated_at": time.time(), "skipped": recs.skipped,
              "honesty": HONESTY, "model_saved": False}

    def finish():
        models_dir.mkdir(parents=True, exist_ok=True)
        clean = _jsonable(report)
        (models_dir / REPORT_NAME).write_text(json.dumps(clean, indent=2), encoding="utf-8")
        return clean

    def say_missing():
        say("Collection status vs targets (sessions):")
        for label in TARGETS:
            c = collection[label]
            say(f"  {label:<12} have {c['have']:>2} / target {c['target']:>2}   missing {c['missing']}")

    if synthetic:
        say("WARNING: synthetic recordings included. This model/report is a pipeline test and must "
            "NOT be presented as real performance.")
    if not recs:
        say("No labelled recordings found: nothing to train, no model written.")
        say_missing()
        return finish()

    present = sorted(per_label)
    classes_present = sorted({tc.LABEL_TO_CLASS[label] for label in present})
    enough = len(classes_present) >= 2 and all(per_label[label] >= MIN_SESSIONS_PER_CLASS for label in present)
    report["status"] = "ok" if enough else "insufficient_data"
    # A claim needs enough sessions AND real data: synthetic signals can never support one.
    report["accuracy_claim"] = bool(enough and not synthetic)
    if not enough:
        say(NOT_VALID)
        say_missing()
    if len(classes_present) < 2:
        say("Fewer than 2 classes present: no model fitted.")
        return finish()

    X, y, groups, _ = build_dataset(recs, window_s, overlap)
    if len(set(y)) < 2:
        report["status"] = "insufficient_data"
        report["accuracy_claim"] = False
        say("Recordings too short to give windows for 2 classes: no model fitted.")
        return finish()
    hz_min = float(min(imu_features.measure_rate(r.samples) for r in recs))
    names = tuple(imu_features.features_for_model(hz_min))
    info = {"n_sessions": len(recs), "sessions_per_class": dict(per_label), "sources": sources,
            "contains_synthetic": synthetic, "sample_hz_min": hz_min, "window_s": window_s,
            "overlap": overlap, "accuracy_claim": report["accuracy_claim"]}

    train_idx, test_idx = split_by_session(y, groups)
    if len(test_idx) and len(set(y[train_idx])) >= 2:
        forest = tc.ForestClassifier(n_estimators).fit(X[train_idx], y[train_idx], names, info)
        rules = tc.RuleClassifier()
        test_sessions = sorted(set(groups[test_idx]))
        acts = {tc.CLASS_TO_ACTIVITY[c] for c in y[test_idx]}
        for clf in (rules, forest):
            acts |= {tc.ACTIVITIES[i] for i in np.asarray(clf.predict_proba(X[test_idx])).argmax(axis=1)}
        labels = [a for a in tc.ACTIVITIES if a in acts]
        report.update(labels=labels, train_sessions=sorted(set(groups[train_idx])), test_sessions=test_sessions,
                      evaluated_on="held_out_sessions")
        for key, clf in (("baseline", rules), ("forest", forest)):
            res = evaluate(clf, X[test_idx], y[test_idx], labels)
            text = res.pop("text")
            report[key] = {**res, "model": clf.name}
            say(f"\n=== {key} ({clf.name}) on {len(test_sessions)} held-out sessions ===")
            if not enough:
                say(NOT_VALID)
            say(text)
            say(f"confusion matrix (rows = truth, columns = predicted) {labels}:")
            say(np.array(res["confusion_matrix"]))
        trigger = []
        for rec in recs:
            if rec.session_id not in test_sessions:
                continue
            try:
                cmp = trigger_time_comparison(rec, forest)
            except ImportError as exc:
                report["trigger_time_note"] = f"session detector unavailable: {exc}"
                break
            if cmp is not None:
                trigger.append(cmp)
        report["trigger_time"] = trigger
        for t in trigger:
            say(f"trigger time {t['session_id']}: truth {t['truth_s']:.1f} s, estimated {t['predicted_s']:.1f} s")
    else:
        report["evaluation_note"] = "no class has 2 sessions: nothing could be held out, no metrics"
        say("No class has 2 sessions, so nothing could be held out: no metrics.")

    final = tc.ForestClassifier(n_estimators).fit(X, y, names, info)   # refit on everything for live use
    path = final.save(models_dir / tc.MODEL_NAME)
    report.update(model_saved=True, model_path=path, final_model_fit_on="all_sessions",
                  feature_names=list(names), sample_hz_min=hz_min, n_windows=int(len(y)))
    say(f"\nModel refitted on all sessions and saved to {path}"
        + (" (flagged contains_synthetic: ignored by the live detector)" if synthetic else ""))
    return finish()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Train the toolsense activity classifier (estimate, not a measurement).")
    ap.add_argument("--recordings", default=None)
    ap.add_argument("--models", default=None)
    ap.add_argument("--include-synthetic", action="store_true")
    ap.add_argument("--window", type=float, default=1.5)
    ap.add_argument("--overlap", type=float, default=0.5)
    args = ap.parse_args(argv)
    report = run(args.recordings, args.models, args.include_synthetic, args.window, args.overlap)
    print(f"status: {report['status']}   accuracy_claim: {report['accuracy_claim']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
