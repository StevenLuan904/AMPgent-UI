from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator

from pepagent.autoresearch_structure_cohort import (
    FrozenModel,
    StructureEscalationCohort,
    load_frozen_structure_escalation_cohort,
)
from pepagent.autoresearch_wetlab_gold import ChallengerReviewEvidence
from pepagent.hemopi2_v27_worker import REQUIRED_ENVIRONMENT
from pepagent.provenance.hashing import sha256_file, sha256_json

HEMOPI2_CLASSIFIER_SHA256 = "63973510af1b883c505e0c475297b4f9edf07b5c7bcd91546ffcb4fdec62dac5"
HEMOPI2_REGRESSOR_SHA256 = "72b8afc63ed7803955aae970ab93627bb710fab17db027bf6110a0237abb6955"


class HemoPI2ChallengerPrediction(FrozenModel):
    schema_version: Literal["ampgent.hemopi2-challenger-prediction.1"] = (
        "ampgent.hemopi2-challenger-prediction.1"
    )
    target_key: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_classification_score: float = Field(ge=0.0, le=1.0)
    raw_classification_label: Literal[0, 1]
    calibrated_hemolysis_probability: float = Field(ge=0.0, le=1.0)
    calibration_risk_threshold: float = Field(gt=0.0, lt=1.0)
    calibration_threshold_exceeded: bool
    reported_hc50_um: float = Field(ge=0.0)
    reported_hc50_below_100_um: bool
    macrel_hemolysis_label: Literal["low"] = "low"
    conflict_status: Literal["no_conflict", "cross_model_disagreement_retained"]
    candidate_hard_gate_applied: Literal[False] = False
    candidate_level_ood_available: Literal[False] = False

    @model_validator(mode="after")
    def validate_prediction(self) -> HemoPI2ChallengerPrediction:
        values = (
            self.raw_classification_score,
            self.calibrated_hemolysis_probability,
            self.reported_hc50_um,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("HemoPI2 challenger prediction contains non-finite values")
        expected_conflict = (
            "cross_model_disagreement_retained"
            if (
                self.calibration_threshold_exceeded
                or self.raw_classification_label == 1
                or self.reported_hc50_below_100_um
            )
            else "no_conflict"
        )
        if self.conflict_status != expected_conflict:
            raise ValueError("HemoPI2 challenger conflict classification drifted")
        return self


class ChallengerRuntimeEvidence(FrozenModel):
    runtime_python_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    classifier_sha256: Literal[HEMOPI2_CLASSIFIER_SHA256]
    regressor_sha256: Literal[HEMOPI2_REGRESSOR_SHA256]
    calibration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ood_witness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineage_witness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deterministic_environment: dict[str, str]
    network_disabled: Literal[True] = True

    @model_validator(mode="after")
    def validate_environment(self) -> ChallengerRuntimeEvidence:
        if self.deterministic_environment != REQUIRED_ENVIRONMENT:
            raise ValueError("HemoPI2 deterministic environment drifted")
        return self


class ChallengerReviewBundle(FrozenModel):
    schema_version: Literal["ampgent.autoresearch-challenger-review-bundle.1"] = (
        "ampgent.autoresearch-challenger-review-bundle.1"
    )
    structure_cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_cohort_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime: ChallengerRuntimeEvidence
    predictions: tuple[HemoPI2ChallengerPrediction, ...]
    reviews: tuple[ChallengerReviewEvidence, ...]
    missing_verified_runtimes: tuple[Literal["apex", "peptiverse"], ...] = (
        "apex",
        "peptiverse",
    )
    candidate_hard_gate_applied: Literal[False] = False
    no_weighted_total_score: Literal[True] = True

    @model_validator(mode="after")
    def validate_bundle(self) -> ChallengerReviewBundle:
        if len(self.predictions) != len(self.reviews) or not self.predictions:
            raise ValueError("challenger prediction/review coverage drifted")
        prediction_ids = [item.sequence_sha256 for item in self.predictions]
        review_ids = [item.sequence_sha256 for item in self.reviews]
        if prediction_ids != review_ids or len(set(prediction_ids)) != len(prediction_ids):
            raise ValueError("challenger prediction/review identity drifted")
        if any(item.review_receipt_sha256 != self.review_identity_sha256 for item in self.reviews):
            raise ValueError("challenger review identity drifted")
        return self

    @computed_field(return_type=str)
    @property
    def bundle_sha256(self) -> str:
        return sha256_json(
            self.model_dump(
                mode="json",
                exclude={"bundle_sha256"},
                exclude_computed_fields=True,
            )
        )


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _calibrated_probability(raw_score: float, coefficient: float, intercept: float) -> float:
    clipped = min(max(raw_score, 1e-12), 1.0 - 1e-12)
    logit = math.log(clipped / (1.0 - clipped))
    calibrated_logit = coefficient * logit + intercept
    if calibrated_logit >= 0:
        return 1.0 / (1.0 + math.exp(-calibrated_logit))
    exp_value = math.exp(calibrated_logit)
    return exp_value / (1.0 + exp_value)


def _worker_input(cohort: StructureEscalationCohort) -> dict[str, object]:
    return {
        "schema_version": "ampgent.autoresearch-challenger-worker-input.1",
        "structure_cohort_sha256": cohort.cohort_sha256,
        "candidates": [
            {
                "target_key": target.target_key,
                "candidate_id": item.candidate_id,
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
            }
            for target in cohort.target_cohorts
            for item in target.selected
        ],
    }


def run_challenger_review(
    *,
    structure_cohort_dir: Path,
    structure_cohort_receipt_sha256: str,
    runtime_python: Path,
    worker_path: Path,
    inference_path: Path,
    model_root: Path,
    calibration_path: Path,
    calibration_sha256: str,
    ood_witness_path: Path,
    ood_witness_sha256: str,
    lineage_witness_path: Path,
    lineage_witness_sha256: str,
    source_root: Path,
    scratch_root: Path,
) -> tuple[ChallengerReviewBundle, bytes]:
    cohort, _ = load_frozen_structure_escalation_cohort(
        structure_cohort_dir,
        receipt_sha256=structure_cohort_receipt_sha256,
    )
    identities = {
        "runtime_python": sha256_file(runtime_python),
        "worker": sha256_file(worker_path),
        "inference": sha256_file(inference_path),
        "classifier": sha256_file(model_root / "hemopi2_ml_clf.sav"),
        "regressor": sha256_file(model_root / "HemoPI2_reg.sav"),
        "calibration": sha256_file(calibration_path),
        "ood": sha256_file(ood_witness_path),
        "lineage": sha256_file(lineage_witness_path),
    }
    expected = {
        "classifier": HEMOPI2_CLASSIFIER_SHA256,
        "regressor": HEMOPI2_REGRESSOR_SHA256,
        "calibration": calibration_sha256,
        "ood": ood_witness_sha256,
        "lineage": lineage_witness_sha256,
    }
    if any(identities[key] != value for key, value in expected.items()):
        raise ValueError("HemoPI2 challenger runtime evidence drifted")
    calibration = json.loads(calibration_path.read_text(encoding="utf-8-sig"))
    if calibration.get("schema_version") != "ampgent.hemopi2-calibration-threshold.1":
        raise ValueError("HemoPI2 calibration schema drifted")
    if calibration.get("threshold_policy", {}).get("candidate_hard_gate_allowed") is not False:
        raise ValueError("HemoPI2 calibration unexpectedly permits a candidate hard gate")
    coefficient = float(calibration["calibrator"]["coefficient"])
    intercept = float(calibration["calibrator"]["intercept"])
    threshold = float(calibration["threshold_policy"]["calibrated_probability_threshold"])

    input_payload = _canonical_json_bytes(_worker_input(cohort))
    input_sha256 = hashlib.sha256(input_payload).hexdigest()
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hemopi2-challenger-", dir=scratch_root) as raw_dir:
        input_path = Path(raw_dir) / "input.json"
        input_path.write_bytes(input_payload)
        environment = os.environ.copy()
        environment.update(REQUIRED_ENVIRONMENT)
        environment["PYTHONPATH"] = str((source_root / "src").resolve())
        completed = subprocess.run(
            [
                str(runtime_python.resolve()),
                str(worker_path.resolve()),
                "--input",
                str(input_path),
                "--input-sha256",
                input_sha256,
                "--model-root",
                str(model_root.resolve()),
            ],
            cwd=source_root.resolve(),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            shell=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"HemoPI2 challenger worker failed ({completed.returncode}): "
            f"{completed.stderr.decode(errors='replace')}"
        )
    if completed.stderr:
        raise RuntimeError(
            "HemoPI2 challenger worker emitted stderr: "
            f"{completed.stderr.decode(errors='replace')}"
        )
    worker_output = completed.stdout
    raw_output = json.loads(worker_output.decode("utf-8"))
    records = raw_output.get("records") if isinstance(raw_output, dict) else None
    expected_candidates = _worker_input(cohort)["candidates"]
    if not isinstance(records, list) or len(records) != len(expected_candidates):
        raise ValueError("HemoPI2 challenger worker coverage drifted")

    predictions: list[HemoPI2ChallengerPrediction] = []
    review_identity_sha256 = sha256_json(
        {
            "structure_cohort_sha256": cohort.cohort_sha256,
            "structure_cohort_receipt_sha256": structure_cohort_receipt_sha256,
            "worker_input_sha256": input_sha256,
            "worker_output_sha256": hashlib.sha256(worker_output).hexdigest(),
            "runtime_identities": identities,
        }
    )
    reviews: list[ChallengerReviewEvidence] = []
    limitations = tuple(
        sorted(
            {
                "apex_missing_verified_runtime",
                "challenger_predictions_are_not_wetlab_measurements",
                "hemopi2_candidate_hard_gate_forbidden",
                "hemopi2_candidate_level_ood_unavailable",
                "hemopi2_same_evidence_family_as_macrel",
                "peptiverse_missing_verified_runtime",
            }
        )
    )
    for index, (expected_row, record) in enumerate(zip(expected_candidates, records, strict=True)):
        if not isinstance(record, dict) or any(
            record.get(key) != expected_row[key]
            for key in ("target_key", "candidate_id", "sequence", "sequence_sha256")
        ):
            raise ValueError(f"HemoPI2 challenger worker row {index} identity drifted")
        raw_score = float(record["hemopi2_classification_score"])
        raw_label = int(record["hemopi2_classification_label"])
        hc50 = float(record["hemopi2_hc50_um"])
        calibrated = _calibrated_probability(raw_score, coefficient, intercept)
        risk = calibrated >= threshold
        hc50_risk = hc50 < 100.0
        conflict = (
            "cross_model_disagreement_retained"
            if risk or raw_label == 1 or hc50_risk
            else "no_conflict"
        )
        prediction = HemoPI2ChallengerPrediction(
            target_key=str(record["target_key"]),
            candidate_id=str(record["candidate_id"]),
            sequence_sha256=str(record["sequence_sha256"]),
            raw_classification_score=raw_score,
            raw_classification_label=raw_label,
            calibrated_hemolysis_probability=calibrated,
            calibration_risk_threshold=threshold,
            calibration_threshold_exceeded=risk,
            reported_hc50_um=hc50,
            reported_hc50_below_100_um=hc50_risk,
            conflict_status=conflict,
        )
        predictions.append(prediction)
        reviews.append(
            ChallengerReviewEvidence(
                target_key=prediction.target_key,
                sequence_sha256=prediction.sequence_sha256,
                review_receipt_sha256=review_identity_sha256,
                verified_models=("hemopi2_v27",),
                missing_verified_runtimes=("apex", "peptiverse"),
                conflict_status=conflict,
                unresolved_severe_conflict=False,
                limitations=limitations,
            )
        )
    runtime = ChallengerRuntimeEvidence(
        runtime_python_sha256=identities["runtime_python"],
        worker_sha256=identities["worker"],
        inference_sha256=identities["inference"],
        classifier_sha256=identities["classifier"],
        regressor_sha256=identities["regressor"],
        calibration_sha256=identities["calibration"],
        ood_witness_sha256=identities["ood"],
        lineage_witness_sha256=identities["lineage"],
        deterministic_environment=REQUIRED_ENVIRONMENT,
    )
    return (
        ChallengerReviewBundle(
            structure_cohort_sha256=cohort.cohort_sha256,
            structure_cohort_receipt_sha256=structure_cohort_receipt_sha256,
            worker_input_sha256=input_sha256,
            worker_output_sha256=hashlib.sha256(worker_output).hexdigest(),
            review_identity_sha256=review_identity_sha256,
            runtime=runtime,
            predictions=tuple(predictions),
            reviews=tuple(reviews),
        ),
        worker_output,
    )


def challenger_csv_rows(bundle: ChallengerReviewBundle) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in bundle.predictions]


def write_challenger_bundle(
    *,
    bundle: ChallengerReviewBundle,
    worker_output: bytes,
    output_root: Path,
) -> dict[str, object]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / bundle.bundle_sha256
    if final_dir.exists():
        raise FileExistsError(f"challenger review bundle already exists: {final_dir}")
    temporary_dir = output_root / f".{bundle.bundle_sha256}.tmp-{os.getpid()}"
    if temporary_dir.exists():
        raise FileExistsError(f"challenger review temporary directory exists: {temporary_dir}")
    temporary_dir.mkdir()
    raw_path = temporary_dir / "hemopi2_worker_output.json"
    bundle_path = temporary_dir / "challenger_review_bundle.json"
    csv_path = temporary_dir / "challenger_review.csv"
    audit_path = temporary_dir / "challenger_review_audit.json"
    receipt_path = temporary_dir / "challenger_review.receipt.json"
    raw_path.write_bytes(worker_output)
    bundle_payload = bundle.model_dump(mode="json", exclude_computed_fields=True)
    bundle_payload["bundle_sha256"] = bundle.bundle_sha256
    bundle_path.write_bytes(_canonical_json_bytes(bundle_payload))
    rows = challenger_csv_rows(bundle)
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    target_counts: dict[str, dict[str, int]] = {}
    for prediction in bundle.predictions:
        counts = target_counts.setdefault(
            prediction.target_key, {"reviewed": 0, "disagreement_retained": 0}
        )
        counts["reviewed"] += 1
        counts["disagreement_retained"] += int(
            prediction.conflict_status == "cross_model_disagreement_retained"
        )
    audit = {
        "schema_version": "ampgent.autoresearch-challenger-review-audit.1",
        "bundle_sha256": bundle.bundle_sha256,
        "review_identity_sha256": bundle.review_identity_sha256,
        "reviewed_count": len(bundle.reviews),
        "candidate_hard_gate_applied": False,
        "target_counts": target_counts,
        "limitations": sorted({value for review in bundle.reviews for value in review.limitations}),
    }
    audit_path.write_bytes(_canonical_json_bytes(audit))
    files = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in (raw_path, bundle_path, csv_path, audit_path)
    }
    receipt = {
        "schema_version": "ampgent.autoresearch-challenger-review-receipt.1",
        "status": "succeeded",
        "bundle_sha256": bundle.bundle_sha256,
        "review_identity_sha256": bundle.review_identity_sha256,
        "reviewed_count": len(bundle.reviews),
        "files": files,
        "runtime": bundle.runtime.model_dump(mode="json"),
        "candidate_hard_gate_applied": False,
    }
    receipt_path.write_bytes(_canonical_json_bytes(receipt))
    receipt_sha256 = sha256_file(receipt_path)
    temporary_dir.replace(final_dir)
    return {
        "status": "succeeded",
        "output_dir": str(final_dir),
        "bundle_sha256": bundle.bundle_sha256,
        "review_identity_sha256": bundle.review_identity_sha256,
        "reviewed_count": len(bundle.reviews),
        "receipt_sha256": receipt_sha256,
    }


__all__ = [
    "ChallengerReviewBundle",
    "HemoPI2ChallengerPrediction",
    "challenger_csv_rows",
    "run_challenger_review",
    "write_challenger_bundle",
]
