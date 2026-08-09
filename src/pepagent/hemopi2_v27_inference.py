from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from pepagent.hemopi2_adapter import (
    HEMOPI2_CLASSIFIER_SHA256,
    HEMOPI2_REGRESSOR_SHA256,
    HEMOPI2_SMOKE_SEQUENCES,
    _require_estimator,
    assemble_feature_matrices,
    canonical_smoke_bytes,
    load_restricted_sklearn_pickle,
)


def official_hc50_um(raw_prediction: np.ndarray) -> np.ndarray:
    """Apply the archived HemoPI2 regression reporting contract."""

    raw = np.asarray(raw_prediction, dtype=np.float64)
    if raw.ndim != 1 or not np.isfinite(raw).all():
        raise ValueError("raw HemoPI2 regression output must be a finite vector")
    transformed = np.exp(-raw)
    reported = pd.Series(transformed).round(3).to_numpy(dtype=np.float64)
    if reported.shape != raw.shape or not np.isfinite(reported).all():
        raise ValueError("reported HemoPI2 HC50 output failed shape or finiteness")
    return reported


def run_v27_predictions(
    sequences: list[str],
    model_root: Path,
    data_root: Path,
    *,
    evidence_scope: str,
) -> list[dict[str, object]]:
    matrices = assemble_feature_matrices(sequences, data_root)
    classifier = _require_estimator(
        load_restricted_sklearn_pickle(
            model_root / "hemopi2_ml_clf.sav",
            expected_sha256=HEMOPI2_CLASSIFIER_SHA256,
        ),
        "random_forest_model_1",
    )
    regressor = _require_estimator(
        load_restricted_sklearn_pickle(
            model_root / "HemoPI2_reg.sav",
            expected_sha256=HEMOPI2_REGRESSOR_SHA256,
        ),
        "random_forest_hc50",
    )
    if not callable(getattr(classifier, "predict_proba", None)):
        raise TypeError("random_forest_model_1 has no callable predict_proba method")

    classifier_values, classifier_names = matrices["classification"]
    regression_values, regression_names = matrices["regression"]
    classifier_frame = pd.DataFrame(classifier_values, columns=classifier_names)
    regression_frame = pd.DataFrame(regression_values, columns=regression_names)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        probabilities = np.asarray(classifier.predict_proba(classifier_frame))
        labels = np.asarray(classifier.predict(classifier_frame))
        raw_regression = np.asarray(regressor.predict(regression_frame))
    if captured:
        messages = "; ".join(str(item.message) for item in captured)
        raise RuntimeError(f"v27 smoke emitted warnings: {messages}")

    classes = list(getattr(classifier, "classes_", []))
    if 1 not in classes:
        raise ValueError("classifier classes do not contain positive class 1")
    positive_index = classes.index(1)
    if probabilities.shape != (len(sequences), len(classes)):
        raise ValueError("classifier probability shape drifted")
    if labels.shape != (len(sequences),) or raw_regression.shape != (len(sequences),):
        raise ValueError("v27 smoke prediction shape drifted")
    if not np.isfinite(probabilities).all():
        raise ValueError("classification predictions contain non-finite values")
    hc50_um = official_hc50_um(raw_regression)

    return [
        {
            "sequence": sequence,
            "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
            "hemopi2_classification_score": float(
                probabilities[index, positive_index]
            ),
            "hemopi2_classification_label": int(labels[index]),
            "hemopi2_hc50_um": float(hc50_um[index]),
            "validator_version": "HemoPI2-Zenodo-14676712-rf-only-v27",
            "evidence_scope": evidence_scope,
        }
        for index, sequence in enumerate(sequences)
    ]


def run_fixed_v27_smoke_once(model_root: Path, data_root: Path) -> bytes:
    records = run_v27_predictions(
        list(HEMOPI2_SMOKE_SEQUENCES),
        model_root,
        data_root,
        evidence_scope="nonformal_determinism_smoke_only",
    )
    return canonical_smoke_bytes(records)
