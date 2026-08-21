from __future__ import annotations

import csv
import hashlib
import io
import math
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from pepagent.hemopi2_v27_inference import run_v27_predictions
from pepagent.provenance.hashing import sha256_json

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
CROSS_VALIDATION_MEMBER = "hemopi2/Dataset/cross_val_dataset.csv"
INDEPENDENT_MEMBER = "hemopi2/Dataset/independent_dataset.csv"
CROSS_VALIDATION_SHA256 = (
    "7bdaf3ede499d1eda2712585d2e52d7700f3f138776d1a0a46e2ca88e8152da0"
)
INDEPENDENT_SHA256 = (
    "500013c2244219762ff3ff4a03401c7419790c83d0ef0c3aeebfdbea426b3eb5"
)


@dataclass(frozen=True)
class ReferenceRecord:
    sequence: str
    label: int

    @property
    def sequence_sha256(self) -> str:
        return hashlib.sha256(self.sequence.encode()).hexdigest()


def read_reference_dataset(payload: bytes, *, expected_sha256: str) -> list[ReferenceRecord]:
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("HemoPI2 reference dataset SHA-256 drifted")
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
    records: list[ReferenceRecord] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) < 3:
            raise ValueError(f"HemoPI2 reference row {row_number} is incomplete")
        sequence = row[0].strip().upper()
        if not sequence or set(sequence) - set(AMINO_ACIDS):
            raise ValueError(f"HemoPI2 reference row {row_number} has an invalid sequence")
        try:
            label = int(row[-1])
        except ValueError as exc:
            raise ValueError(f"HemoPI2 reference row {row_number} has an invalid label") from exc
        if label not in {0, 1}:
            raise ValueError(f"HemoPI2 reference row {row_number} label is outside 0/1")
        records.append(ReferenceRecord(sequence=sequence, label=label))
    if len({record.sequence for record in records}) != len(records):
        raise ValueError("HemoPI2 reference dataset contains duplicate sequences")
    return records


def load_archived_reference_datasets(
    archive_path: Path,
) -> tuple[list[ReferenceRecord], list[ReferenceRecord]]:
    with zipfile.ZipFile(archive_path) as archive:
        cross_validation = read_reference_dataset(
            archive.read(CROSS_VALIDATION_MEMBER), expected_sha256=CROSS_VALIDATION_SHA256
        )
        independent = read_reference_dataset(
            archive.read(INDEPENDENT_MEMBER), expected_sha256=INDEPENDENT_SHA256
        )
    if {record.sequence for record in cross_validation} & {
        record.sequence for record in independent
    }:
        raise ValueError("HemoPI2 train and independent reference sets overlap exactly")
    return cross_validation, independent


def _kmers(sequence: str, size: int = 3) -> set[str]:
    return {
        sequence[index : index + size]
        for index in range(max(0, len(sequence) - size + 1))
    }


def _composition(sequence: str) -> np.ndarray:
    return np.asarray([sequence.count(residue) / len(sequence) for residue in AMINO_ACIDS])


def domain_features(
    training: Sequence[ReferenceRecord], independent: Sequence[ReferenceRecord]
) -> np.ndarray:
    """Return label-free novelty, composition distance, and length-tail features."""

    training_kmers = [_kmers(record.sequence) for record in training]
    training_composition = np.asarray(
        [_composition(record.sequence) for record in training]
    )
    training_lengths = np.asarray([len(record.sequence) for record in training])
    features: list[tuple[float, float, float]] = []
    for record in independent:
        kmers = _kmers(record.sequence)
        maximum_jaccard = max(
            len(kmers & reference) / len(kmers | reference)
            if kmers | reference
            else 1.0
            for reference in training_kmers
        )
        minimum_composition_distance = float(
            np.min(
                np.abs(training_composition - _composition(record.sequence)).sum(axis=1)
                / 2.0
            )
        )
        length = len(record.sequence)
        empirical_cdf = (
            np.count_nonzero(training_lengths <= length)
            - 0.5 * np.count_nonzero(training_lengths == length)
        ) / len(training_lengths)
        features.append(
            (
                1.0 - maximum_jaccard,
                minimum_composition_distance,
                abs(2.0 * empirical_cdf - 1.0),
            )
        )
    result = np.asarray(features, dtype=np.float64)
    if result.shape != (len(independent), 3) or not np.isfinite(result).all():
        raise ValueError("HemoPI2 applicability-domain features are invalid")
    return result


def frozen_calibration_ood_split(
    training: Sequence[ReferenceRecord],
    independent: Sequence[ReferenceRecord],
    *,
    ood_fraction: float = 0.25,
) -> tuple[list[int], list[int], np.ndarray]:
    """Make a label-free split using equal rank weight across three domain axes."""

    if not 0.0 < ood_fraction < 0.5:
        raise ValueError("HemoPI2 OOD fraction must be between zero and one half")
    features = domain_features(training, independent)
    ranks = np.empty_like(features)
    for feature_index in range(features.shape[1]):
        order = np.argsort(features[:, feature_index], kind="mergesort")
        ranks[order, feature_index] = (
            np.arange(len(independent), dtype=np.float64) + 0.5
        ) / len(independent)
    aggregate = ranks.mean(axis=1)
    ood_count = math.ceil(len(independent) * ood_fraction)
    ood = sorted(
        np.argsort(-aggregate, kind="mergesort")[:ood_count].astype(int).tolist()
    )
    ood_set = set(ood)
    calibration = [index for index in range(len(independent)) if index not in ood_set]
    return calibration, ood, features


def _membership_sha256(records: Sequence[ReferenceRecord], indices: Sequence[int]) -> str:
    payload = "\n".join(sorted(records[index].sequence_sha256 for index in indices)) + "\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def _classification_metrics(
    labels: np.ndarray,
    raw_probabilities: np.ndarray,
    calibrated_probabilities: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    from sklearn.metrics import (
        average_precision_score,
        balanced_accuracy_score,
        brier_score_loss,
        confusion_matrix,
        roc_auc_score,
    )

    predicted = calibrated_probabilities >= threshold
    true_negative, false_positive, false_negative, true_positive = confusion_matrix(
        labels, predicted, labels=[0, 1]
    ).ravel()
    sensitivity = true_positive / (true_positive + false_negative)
    specificity = true_negative / (true_negative + false_positive)
    return {
        "n": int(len(labels)),
        "negative_count": int(np.count_nonzero(labels == 0)),
        "positive_count": int(np.count_nonzero(labels == 1)),
        "raw_probability_auroc": float(roc_auc_score(labels, raw_probabilities)),
        "calibrated_probability_auroc": float(
            roc_auc_score(labels, calibrated_probabilities)
        ),
        "calibrated_probability_auprc": float(
            average_precision_score(labels, calibrated_probabilities)
        ),
        "calibrated_probability_brier": float(
            brier_score_loss(labels, calibrated_probabilities)
        ),
        "threshold_sensitivity": float(sensitivity),
        "threshold_specificity": float(specificity),
        "threshold_balanced_accuracy": float(
            balanced_accuracy_score(labels, predicted)
        ),
        "confusion_matrix": {
            "true_negative": int(true_negative),
            "false_positive": int(false_positive),
            "false_negative": int(false_negative),
            "true_positive": int(true_positive),
        },
    }


def _calibration_bins(labels: np.ndarray, probabilities: np.ndarray) -> list[dict[str, Any]]:
    order = np.argsort(probabilities, kind="mergesort")
    bins: list[dict[str, Any]] = []
    for bin_index, indices in enumerate(np.array_split(order, 10)):
        bins.append(
            {
                "bin_index": bin_index,
                "n": int(len(indices)),
                "mean_probability": float(np.mean(probabilities[indices])),
                "observed_positive_fraction": float(np.mean(labels[indices])),
            }
        )
    return bins


def qualify_hemopi2(root: Path) -> dict[str, dict[str, Any]]:
    """Run the pinned RF twice and return compact reproducible benchmark artifacts."""

    archive_path = root / "var/external-models/hemopi2/zenodo-14676712/hemopi2.zip"
    training, independent = load_archived_reference_datasets(archive_path)
    calibration, ood, features = frozen_calibration_ood_split(training, independent)
    model_root = (
        root
        / "var/external-models/hemopi2/zenodo-14676712/rf-only-extracted-v1/Model"
    )
    sequences = [record.sequence for record in independent]
    first = run_v27_predictions(
        sequences,
        model_root,
        model_root / "Data",
        evidence_scope="independent_calibration_and_ood_benchmark",
    )
    second = run_v27_predictions(
        sequences,
        model_root,
        model_root / "Data",
        evidence_scope="independent_calibration_and_ood_benchmark",
    )
    first_hash = sha256_json(first)
    if first_hash != sha256_json(second):
        raise ValueError("HemoPI2 probability semantics are not reproducible")

    labels = np.asarray([record.label for record in independent], dtype=np.int64)
    raw = np.asarray(
        [float(record["hemopi2_classification_score"]) for record in first]
    )
    clipped = np.clip(raw, 1e-6, 1.0 - 1e-6)
    logits = np.log(clipped / (1.0 - clipped)).reshape(-1, 1)
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import confusion_matrix

    calibrator = LogisticRegression(C=1e6, solver="lbfgs", random_state=0).fit(
        logits[calibration], labels[calibration]
    )
    calibrated = calibrator.predict_proba(logits)[:, 1]
    operating_points: list[tuple[float, float, float]] = []
    for threshold in sorted(set(calibrated[calibration])):
        true_negative, false_positive, false_negative, true_positive = confusion_matrix(
            labels[calibration], calibrated[calibration] >= threshold, labels=[0, 1]
        ).ravel()
        sensitivity = true_positive / (true_positive + false_negative)
        specificity = true_negative / (true_negative + false_positive)
        if sensitivity >= 0.90:
            operating_points.append((specificity, sensitivity, float(threshold)))
    if not operating_points:
        raise ValueError("HemoPI2 calibration has no operating point at 90% sensitivity")
    _specificity, _sensitivity, threshold = max(
        operating_points, key=lambda item: (item[0], item[1], item[2])
    )

    split_artifact = {
        "schema_version": "ampgent.hemopi2-reference-split.1",
        "candidate_data_used": False,
        "training_source": CROSS_VALIDATION_MEMBER,
        "training_source_sha256": CROSS_VALIDATION_SHA256,
        "training_count": len(training),
        "independent_source": INDEPENDENT_MEMBER,
        "independent_source_sha256": INDEPENDENT_SHA256,
        "independent_count": len(independent),
        "exact_train_independent_overlap_count": 0,
        "split_rule": {
            "label_blind": True,
            "ood_fraction": 0.25,
            "features": [
                "one_minus_maximum_training_3mer_jaccard",
                "minimum_training_amino_acid_composition_l1_over_two",
                "training_length_empirical_cdf_two_sided_tailness",
            ],
            "aggregation": "arithmetic_mean_of_equal_rank_percentiles",
            "selection": "highest_aggregate_rank_with_stable_index_tie_break",
        },
        "calibration_count": len(calibration),
        "calibration_member_set_sha256": _membership_sha256(
            independent, calibration
        ),
        "ood_count": len(ood),
        "ood_member_set_sha256": _membership_sha256(independent, ood),
    }
    calibration_artifact = {
        "schema_version": "ampgent.hemopi2-calibration-threshold.1",
        "candidate_data_used": False,
        "positive_class_semantics": "hemolytic_peptide_label_1",
        "raw_score_semantics": "random_forest_positive_class_probability",
        "prediction_reproduction_count": 2,
        "prediction_payload_sha256": first_hash,
        "calibrator": {
            "type": "platt_logistic_on_raw_probability_logit",
            "scikit_learn_version": "1.3.1",
            "coefficient": float(calibrator.coef_[0, 0]),
            "intercept": float(calibrator.intercept_[0]),
        },
        "threshold_policy": {
            "fit_partition": "calibration_only",
            "minimum_sensitivity": 0.90,
            "selection": "maximum_specificity_then_sensitivity_then_threshold",
            "calibrated_probability_threshold": threshold,
            "candidate_hard_gate_allowed": False,
        },
        "calibration_metrics": _classification_metrics(
            labels[calibration], raw[calibration], calibrated[calibration], threshold
        ),
        "calibration_curve_quantile_bins": _calibration_bins(
            labels[calibration], calibrated[calibration]
        ),
    }
    ood_metrics = _classification_metrics(
        labels[ood], raw[ood], calibrated[ood], threshold
    )
    ood_artifact = {
        "schema_version": "ampgent.hemopi2-ood-report.1",
        "candidate_data_used": False,
        "partition": "held_out_high_domain_distance_quartile",
        "metrics": ood_metrics,
        "domain_feature_quantiles_all_independent": {
            name: [
                float(value)
                for value in np.quantile(
                    features[:, index], [0, 0.25, 0.5, 0.75, 1]
                )
            ]
            for index, name in enumerate(
                ["sequence_novelty", "composition_distance", "length_tailness"]
            )
        },
        "enterprise_gate_assessment": {
            "status": "insufficient_for_solo_hard_gate",
            "reason_codes": [
                "ood_sensitivity_below_0_90",
                "single_model_cannot_supply_independent_hemolysis_panel",
            ],
            "candidate_hard_gate_allowed": False,
        },
    }
    return {
        "split": split_artifact,
        "calibration": calibration_artifact,
        "ood": ood_artifact,
    }


def artifact_hashes(artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {name: sha256_json(payload) for name, payload in artifacts.items()}
