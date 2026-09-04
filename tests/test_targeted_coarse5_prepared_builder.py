from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analysis.build_angpt1_pepglad_coarse5_prepared as builder


def test_builder_task_key_binds_target_run_and_authoritative_uuid(
    tmp_path: Path, monkeypatch
) -> None:
    run_id = "2b8f5096-5e59-5e27-a53d-692adadebd82"
    specs = [
        (
            "729332fc-9768-480d-810d-2a430da4b967",
            "81fa825512d7d4c04a2859ba2f1f8954cfa258d19a0a67fd3d5bb45806dea3fe",
            "EPRASGEGGTTTHPYYGT",
            "q2-h0-m1-l2",
            "empty_cell",
        ),
        (
            "4d514008-7799-434e-a554-836d3e163526",
            "94beb5f7957a05c3f822d4e11df4d981c699e136329dd88d451553f0f4033d65",
            "IPRASGEGGTTTHPYYGT",
            "q3-h0-m2-l2",
            "incumbent_replacement",
        ),
        (
            "f9a0f1a2-a8b7-4bcc-aef4-62b9bf747f03",
            "dc96039278cd5a49aefc47e56b5776f7a8b1f93d24dfab95029d8cac917cc1a0",
            "KPRASGEGGTTTHPYYGT",
            "q3-h0-m1-l2",
            "incumbent_replacement",
        ),
    ]
    score_csv = tmp_path / "scores.csv"
    with score_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sequence", "sequence_sha256"])
        writer.writeheader()
        for _candidate_id, digest, sequence, _cell, _contribution in specs:
            writer.writerow({"sequence": sequence, "sequence_sha256": digest})
    qd_json = tmp_path / "qd.json"
    qd_json.write_text(
        json.dumps(
            {
                "contributions": [
                    {
                        "candidate_id": digest,
                        "cell_id": cell,
                        "contribution": contribution,
                    }
                    for _candidate_id, digest, _sequence, cell, contribution in specs
                ]
            }
        ),
        encoding="utf-8",
    )
    materialization_json = tmp_path / "materialization.json"
    materialization_json.write_text(
        json.dumps(
            {
                "operational_run_id": run_id,
                "inserted_evaluation_count": 51,
                "tool_call_id": "3c7f9b24-614c-4ef9-a209-6387f7378e04",
                "global_exact_replay_skip_count": 0,
                "historical_runs_modified": False,
            }
        ),
        encoding="utf-8",
    )

    async def fake_pg_candidates(_run_id: str, _hashes: list[str]) -> dict:
        return {
            digest: SimpleNamespace(
                id=candidate_id,
                sequence=sequence,
                sequence_sha256=digest,
            )
            for candidate_id, digest, sequence, _cell, _contribution in specs
        }

    monkeypatch.setattr(builder, "_pg_candidates", fake_pg_candidates)
    builder.build(
        score_csv,
        qd_json,
        materialization_json,
        tmp_path / "out",
        target_key="fgf2",
        source="PepGLAD",
        operator_id="fgf2-pepglad-source-expansion-1aa-v1",
    )
    rows = list(
        csv.DictReader(
            (tmp_path / "out/coarse5_prepared_queue.csv").open(
                encoding="utf-8-sig"
            )
        )
    )
    assert len(rows) == 3
    assert len({row["task_key"] for row in rows}) == 3
    expected_ids = {candidate_id for candidate_id, *_rest in specs}
    for row in rows:
        assert row["task_key"] == (
            f"rosetta-coarse5:fgf2:{run_id}:{row['candidate_id']}"
        )
        assert row["candidate_id"] in expected_ids
        assert row["target_key"] == "fgf2"
        assert row["run_id"] == run_id
        assert int(row["nstruct"]) == 5
        assert float(row["median_dg_gate"]) == -30
        assert row["dispatch_allowed"] == "false"
        assert not row["task_key"].startswith(
            "rosetta-coarse5:" + row["candidate_id"]
        )
    receipt = json.loads(
        (tmp_path / "out/coarse5_prepared_receipt.json").read_text()
    )
    assert receipt["authoritative_candidate_ids"] == [
        row["candidate_id"] for row in rows
    ]
    assert receipt["pg_readback"] == {
        "candidate_count": 3,
        "evaluation_count": 51,
        "evaluation_count_per_candidate": 17,
        "tool_call_id": "3c7f9b24-614c-4ef9-a209-6387f7378e04",
        "global_exact_replay_skip_count": 0,
        "identity_drift_count": 0,
    }
    assert receipt["median_dg_gate"] == -30
