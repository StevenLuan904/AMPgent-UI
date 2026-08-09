from __future__ import annotations

import csv
import hashlib
import io
import sys
from pathlib import Path

from pepagent.hemopi2_v27_worker import network_disabled, require_preimport_environment

INPUT_SHA256 = "fac36b6dbbf4c7525ab7982f054c3c3b02632e0760b938b137d719f1a22a7b12"
INPUT_ROW_COUNT = 300
OUTPUT_COLUMNS = (
    "candidate_id",
    "sequence",
    "sequence_sha256",
    "hemopi2_classification_score",
    "hemopi2_classification_label",
    "hemopi2_hc50_um",
    "validator_version",
    "evidence_scope",
)


def load_frozen_cohort(path: Path, expected_sha256: str) -> list[dict[str, str]]:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("formal v27 input SHA-256 mismatch")
    with io.StringIO(payload.decode("utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"candidate_id", "sequence", "sequence_sha256"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("formal v27 input is missing required columns")
        rows = list(reader)
    if len(rows) != INPUT_ROW_COUNT:
        raise ValueError("formal v27 input row count mismatch")
    candidate_ids: list[str] = []
    sequence_hashes: list[str] = []
    for index, row in enumerate(rows):
        candidate_id = row["candidate_id"].strip()
        sequence = row["sequence"].strip().upper()
        sequence_sha = row["sequence_sha256"].strip().lower()
        if not candidate_id or not sequence:
            raise ValueError(f"formal v27 row {index} has an empty identity field")
        if hashlib.sha256(sequence.encode()).hexdigest() != sequence_sha:
            raise ValueError(f"formal v27 row {index} sequence SHA-256 mismatch")
        candidate_ids.append(candidate_id)
        sequence_hashes.append(sequence_sha)
        row["candidate_id"] = candidate_id
        row["sequence"] = sequence
        row["sequence_sha256"] = sequence_sha
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("formal v27 candidate IDs are not unique")
    if len(set(sequence_hashes)) != len(sequence_hashes):
        raise ValueError("formal v27 sequences are not unique")
    return rows


def canonical_formal_csv(
    cohort: list[dict[str, str]], predictions: list[dict[str, object]]
) -> bytes:
    if len(cohort) != INPUT_ROW_COUNT or len(predictions) != INPUT_ROW_COUNT:
        raise ValueError("formal v27 input/output count mismatch")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for index, (input_row, prediction) in enumerate(zip(cohort, predictions, strict=True)):
        if prediction["sequence"] != input_row["sequence"]:
            raise ValueError(f"formal v27 row {index} prediction order mismatch")
        if prediction["sequence_sha256"] != input_row["sequence_sha256"]:
            raise ValueError(f"formal v27 row {index} prediction SHA mismatch")
        writer.writerow(
            {
                "candidate_id": input_row["candidate_id"],
                "sequence": input_row["sequence"],
                "sequence_sha256": input_row["sequence_sha256"],
                "hemopi2_classification_score": repr(
                    prediction["hemopi2_classification_score"]
                ),
                "hemopi2_classification_label": prediction[
                    "hemopi2_classification_label"
                ],
                "hemopi2_hc50_um": f"{prediction['hemopi2_hc50_um']:.3f}",
                "validator_version": prediction["validator_version"],
                "evidence_scope": prediction["evidence_scope"],
            }
        )
    return stream.getvalue().encode("utf-8")


def _formal_is_authorized(manifest_path: Path) -> bool:
    marker = "\nexecution_status: formal_authorized\n"
    return marker in f"\n{manifest_path.read_text(encoding='utf-8')}"


def main() -> int:
    require_preimport_environment()
    root = Path(__file__).resolve().parents[2]
    manifest = root / "config/benchmarks/amp_designer_safety_validation_v27.yaml"
    if not _formal_is_authorized(manifest):
        raise RuntimeError("v27 formal run is not authorized by the current status")
    cohort = load_frozen_cohort(
        root / "reports/amp_generator_v25_candidate_metrics_20260809.csv",
        INPUT_SHA256,
    )
    extracted = root / "var/external-models/hemopi2/zenodo-14676712/rf-only-extracted-v1"
    with network_disabled():
        from pepagent.hemopi2_v27_inference import run_v27_predictions

        predictions = run_v27_predictions(
            [row["sequence"] for row in cohort],
            extracted / "Model",
            extracted / "Model/Data",
            evidence_scope="frozen_full_cohort_soft_safety_validation",
        )
    sys.stdout.buffer.write(canonical_formal_csv(cohort, predictions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
