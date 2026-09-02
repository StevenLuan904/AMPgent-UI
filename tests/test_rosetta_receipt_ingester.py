from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

SOURCE_PATH = (
    Path(__file__).parents[1] / "deploy" / "remote" / "run_rosetta_receipt_ingester.py"
)
SPEC = importlib.util.spec_from_file_location("run_rosetta_receipt_ingester", SOURCE_PATH)
assert SPEC is not None and SPEC.loader is not None
INGESTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INGESTER)


def _receipt(tmp_path: Path, *, primary: float = -4.5) -> Path:
    candidate = tmp_path / "candidates" / "acea" / "sequence"
    result_path = candidate / "results" / "rosetta_result.json"
    result_path.parent.mkdir(parents=True)
    result = {
        "nstruct": 20,
        "decoys": [
            {"reweighted_sc": float(index), "dG_separated": -float(index)}
            for index in range(20)
        ],
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")
    receipt_path = candidate / "completion_receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": (
                    "ampgent.autoresearch-rosetta-candidate-completion.1"
                ),
                "status": "succeeded",
                "candidate_id": str(uuid.uuid4()),
                "sequence_sha256": "a" * 64,
                "target_key": "acea",
                "nstruct": 20,
                "primary_dG_separated_reu": primary,
                "minimum_dG_separated_reu": -19.0,
                "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return receipt_path


def test_validate_receipt_recomputes_top10_median_and_minimum(tmp_path: Path) -> None:
    validated = INGESTER.validate_receipt(_receipt(tmp_path))

    assert validated["nstruct"] == 20
    assert validated["primary"] == -4.5
    assert validated["minimum"] == -19.0


def test_validate_receipt_rejects_primary_aggregation_drift(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="primary dG aggregation mismatch"):
        INGESTER.validate_receipt(_receipt(tmp_path, primary=-5.0))
