from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from analysis.promote_source_qd_candidate import promote


def _write_csv(path, row):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def _fixtures(tmp_path):
    sequence = "AASKKSKSDKVGNLLQAA"
    digest = hashlib.sha256(sequence.encode()).hexdigest()
    score = tmp_path / "score.csv"
    _write_csv(
        score,
        {
            "sequence": sequence,
            "sequence_sha256": digest,
            "branch_key": "pbp2a",
            "candidate_id": "proposal-test",
            "formal_12_complete": "true",
            "display_eligible": "true",
            "activity_support_calibrated": "3",
            "toxinpred3_label": "Non-Toxin",
            "macrel_hemolysis_label": "low",
            "guruprasad_instability_index": "8.6",
        },
    )
    challenger = tmp_path / "challenger.csv"
    _write_csv(
        challenger,
        {
            "sequence_sha256": digest,
            "challenger_conflict_status": "no_conflict",
        },
    )
    qd = tmp_path / "qd.json"
    qd.write_text(
        json.dumps(
            {
                "sequence_sha256": digest,
                "quality_eligible": True,
                "contribution": "empty_cell",
                "actual_cell_id": "q4-h2-m1-l2",
                "new_cell": True,
                "replacement": False,
                "archive_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    return score, challenger, qd, digest


def test_promotes_only_one_identity_and_preserves_qd(tmp_path):
    score, challenger, qd, digest = _fixtures(tmp_path)
    receipt = promote(
        score_csv=score,
        challenger_csv=challenger,
        qd_receipt=qd,
        output_dir=tmp_path / "out",
        sequence_sha256=digest,
        source_stage="fixture",
    )
    assert receipt["formal12_complete"] is True
    assert receipt["qd"]["actual_cell_id"] == "q4-h2-m1-l2"
    assert len(list(csv.DictReader((tmp_path / "out" / "candidate_scores.csv").open()))) == 1


def test_rejects_display_or_challenger_failure(tmp_path):
    score, challenger, qd, digest = _fixtures(tmp_path)
    rows = list(csv.DictReader(score.open(encoding="utf-8-sig", newline="")))
    rows[0]["display_eligible"] = "false"
    _write_csv(score, rows[0])
    with pytest.raises(ValueError, match="display eligible"):
        promote(
            score_csv=score,
            challenger_csv=challenger,
            qd_receipt=qd,
            output_dir=tmp_path / "out-display",
            sequence_sha256=digest,
            source_stage="fixture",
        )
