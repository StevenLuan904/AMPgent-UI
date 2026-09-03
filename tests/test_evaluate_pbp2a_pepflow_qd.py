from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.evaluate_pbp2a_pepflow_qd import _archive
from analysis.prepare_pbp2a_pepflow_structure_keys import main as prepare_main


def test_archive_loader_accepts_multibranch_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "archive.json"
    path.write_text(
        json.dumps(
            {
                "branches": {
                    "pbp2a": {
                        "elites": [
                            {
                                "candidate_id": "a",
                                "sequence": "ACDE",
                                "quality": 0.5,
                                "behavior": {
                                    "charge_density": 0.0,
                                    "hydrophobicity": 0.1,
                                    "hydrophobic_moment": 0.2,
                                    "length": 4,
                                },
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert _archive(path)[0].candidate_id == "a"


def test_prepared_key_uses_authoritative_uuid(tmp_path: Path, monkeypatch) -> None:
    qd = tmp_path / "qd.csv"
    qd.write_text(
        "sequence_sha256,candidate_id,contribution\n" + "a" * 64 + ",proposal-a,empty_cell\n",
        encoding="utf-8",
    )
    verification = tmp_path / "pg.json"
    verification.write_text(
        json.dumps(
            {
                "run_id": "11111111-1111-4111-8111-111111111111",
                "candidate_evidence": [{"sequence_sha256": "a" * 64, "candidate_id": "u"}],
            }
        ),
        encoding="utf-8",
    )
    score = tmp_path / "score.csv"
    score.write_text("sequence_sha256,sequence\n" + "a" * 64 + ",ACDE\n", encoding="utf-8")
    output_csv, output_json = tmp_path / "out.csv", tmp_path / "out.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare",
            "--qd-csv",
            str(qd),
            "--pg-verification",
            str(verification),
            "--score-csv",
            str(score),
            "--output-csv",
            str(output_csv),
            "--output-json",
            str(output_json),
        ],
    )
    prepare_main()
    assert "proposal-a" not in output_csv.read_text(encoding="utf-8-sig")
    assert ":u," in output_csv.read_text(encoding="utf-8-sig")
