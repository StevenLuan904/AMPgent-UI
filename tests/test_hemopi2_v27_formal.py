from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import pytest

from pepagent.hemopi2_v27_formal import main as formal_main
from pepagent.hemopi2_v27_formal import validate_formal_output
from pepagent.hemopi2_v27_formal_worker import (
    INPUT_ROW_COUNT,
    OUTPUT_COLUMNS,
    canonical_formal_csv,
    load_frozen_cohort,
)


def _input_payload(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=("candidate_id", "sequence", "sequence_sha256"),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def test_frozen_cohort_loader_rejects_digest_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "cohort.csv"
    path.write_bytes(b"not,the,formal,cohort\n")
    with pytest.raises(ValueError, match="SHA-256"):
        load_frozen_cohort(path, "0" * 64)


def test_frozen_cohort_loader_validates_all_rows_without_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {
            "candidate_id": f"candidate-{index}",
            "sequence": f"ACD{'A' * index}",
            "sequence_sha256": hashlib.sha256(f"ACD{'A' * index}".encode()).hexdigest(),
        }
        for index in range(2)
    ]
    payload = _input_payload(rows)
    path = tmp_path / "cohort.csv"
    path.write_bytes(payload)
    monkeypatch.setattr("pepagent.hemopi2_v27_formal_worker.INPUT_ROW_COUNT", 2)
    assert load_frozen_cohort(path, hashlib.sha256(payload).hexdigest()) == rows


def test_canonical_formal_csv_preserves_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    sequence = "ACDE"
    sequence_sha = hashlib.sha256(sequence.encode()).hexdigest()
    cohort = [
        {"candidate_id": "candidate-1", "sequence": sequence, "sequence_sha256": sequence_sha}
    ]
    predictions = [
        {
            "sequence": sequence,
            "sequence_sha256": sequence_sha,
            "hemopi2_classification_score": 0.42,
            "hemopi2_classification_label": 0,
            "hemopi2_hc50_um": 90.237,
            "validator_version": "HemoPI2-Zenodo-14676712-rf-only-v27",
            "evidence_scope": "frozen_full_cohort_soft_safety_validation",
        }
    ]
    monkeypatch.setattr("pepagent.hemopi2_v27_formal_worker.INPUT_ROW_COUNT", 1)
    payload = canonical_formal_csv(cohort, predictions)
    assert payload.startswith((",".join(OUTPUT_COLUMNS) + "\n").encode())
    assert b"candidate-1,ACDE," in payload
    assert b",90.237," in payload


def test_formal_output_validation_rejects_wrong_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pepagent.hemopi2_v27_formal.INPUT_ROW_COUNT", 1)
    with pytest.raises(ValueError, match="row count"):
        validate_formal_output((",".join(OUTPUT_COLUMNS) + "\n").encode())


def test_ready_status_prevents_formal_execution_before_authorization() -> None:
    with pytest.raises(RuntimeError, match="not authorized"):
        formal_main()


def test_formal_runner_is_single_attempt_and_uses_exclusive_files() -> None:
    root = Path(__file__).parents[1]
    source = (root / "src/pepagent/hemopi2_v27_formal.py").read_text(encoding="utf-8")
    assert source.count("subprocess.run(") == 1
    assert '.open("xb")' in source
    assert "shell=False" in source
    assert "amp_generator_v25_candidate_metrics" not in source
    assert INPUT_ROW_COUNT == 300
