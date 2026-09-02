from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from analysis.audit_remote_pool_a import audit


def _write_fixture(root: Path, *, nstruct: int) -> None:
    sequence_sha = "a" * 64
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    with (inputs / "candidates.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "candidate_id",
                "sequence_sha256",
                "target_key",
                "display_eligible",
                "formal_12_complete",
                "formal_metric_count",
                "guruprasad_instability_index",
                "toxinpred3_label",
                "macrel_hemolysis_label",
                "activity_model_support_count_calibrated",
                "historical_exact_replay",
                "challenger_conflict_status",
                "family_key_80_80",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "00000000-0000-0000-0000-000000000001",
                "sequence_sha256": sequence_sha,
                "target_key": "pbp2a",
                "display_eligible": "true",
                "formal_12_complete": "true",
                "formal_metric_count": "12",
                "guruprasad_instability_index": "50",
                "toxinpred3_label": "Non-Toxin",
                "macrel_hemolysis_label": "low",
                "activity_model_support_count_calibrated": "2",
                "historical_exact_replay": "false",
                "challenger_conflict_status": "no_conflict",
                "family_key_80_80": "family-1",
            }
        )
    candidate = root / "candidate-1"
    results = candidate / "results"
    results.mkdir(parents=True)
    decoys = [
        {"reweighted_sc": float(index), "dG_separated": -40.0 + index / 10}
        for index in range(nstruct)
    ]
    result_text = json.dumps({"decoys": decoys})
    (results / "rosetta_result.json").write_text(result_text, encoding="utf-8")
    top = decoys[: (5 if nstruct == 5 else 10)]
    primary = sorted(item["dG_separated"] for item in top)[len(top) // 2]
    if len(top) % 2 == 0:
        primary = (top[len(top) // 2 - 1]["dG_separated"] + top[len(top) // 2]["dG_separated"]) / 2
    receipt = {
        "schema_version": "ampgent.autoresearch-rosetta-candidate-completion.1",
        "status": "succeeded",
        "nstruct": nstruct,
        "candidate_id": "00000000-0000-0000-0000-000000000001",
        "sequence_sha256": sequence_sha,
        "target_key": "pbp2a",
        "result_sha256": hashlib.sha256(result_text.encode()).hexdigest(),
        "primary_dG_separated_reu": primary,
    }
    (candidate / "completion_receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )


def test_pool_a_audit_accepts_frozen_20_decoy_protocol(tmp_path: Path) -> None:
    _write_fixture(tmp_path, nstruct=20)

    result = audit([tmp_path])

    assert result["valid_receipt_count"] == 1
    assert result["invalid_receipt_count"] == 0
    assert result["rows"][0]["nstruct"] == 20
    assert result["summary"]["pbp2a"]["pool_a_top50_filled"] == 1


def test_pool_a_audit_accepts_five_decoy_protocol(tmp_path: Path) -> None:
    _write_fixture(tmp_path, nstruct=5)

    result = audit([tmp_path])

    assert result["valid_receipt_count"] == 1
    assert result["rows"][0]["nstruct"] == 5


def test_pool_a_audit_rejects_unfrozen_decoy_count(tmp_path: Path) -> None:
    _write_fixture(tmp_path, nstruct=19)

    result = audit([tmp_path])

    assert result["valid_receipt_count"] == 0
    assert result["invalid_receipt_count"] == 1
