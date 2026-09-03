def test_v7_operator_exists():
    from pathlib import Path

    assert (Path(__file__).parents[1] / "analysis" / "qd_new_family_block_graft_v7.py").exists()


def test_queue_export_replaces_proposal_id_with_authoritative_uuid(tmp_path, monkeypatch):
    import argparse
    import csv
    import hashlib
    import importlib.util
    import json
    from pathlib import Path

    module_path = Path(__file__).parents[1] / "analysis" / "export_qd_gap_structure_queue.py"
    spec = importlib.util.spec_from_file_location("queue_exporter", module_path)
    exporter = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(exporter)

    sequence = "ACDEFG"
    sequence_sha = hashlib.sha256(sequence.encode()).hexdigest()
    scores = tmp_path / "scores.csv"
    with scores.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "candidate_id", "sequence", "sequence_sha256", "branch_key",
                "excellent_sequence_stage_calibrated",
                "activity_model_support_count_calibrated", "quality",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "candidate_id": "proposal-03e1920e95919d33fdd9",
            "sequence": sequence,
            "sequence_sha256": sequence_sha,
            "branch_key": "pbp2a",
            "excellent_sequence_stage_calibrated": "true",
            "activity_model_support_count_calibrated": "2",
            "quality": "0.9",
        })
    qd = tmp_path / "qd.json"
    qd.write_text(json.dumps({"contributions": [{
        "candidate_id": "03e1920e95919d33fdd9",
        "contribution": "empty_cell",
        "cell_id": "q0",
    }]}), encoding="utf-8")
    challenger = tmp_path / "challenger.csv"
    with challenger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["sequence_sha256", "challenger_conflict_status"],
        )
        writer.writeheader()
        writer.writerow({
            "sequence_sha256": sequence_sha,
            "challenger_conflict_status": "no_conflict",
        })
    async def fake_authoritative_ids(database_url, run_id, hashes):
        return {sequence_sha: "11111111-1111-1111-1111-111111111111"}

    monkeypatch.setattr(exporter, "_authoritative_ids", fake_authoritative_ids)
    output = tmp_path / "queue.csv"
    exporter.export(argparse.Namespace(
        scores=scores, qd=qd, challenger=challenger,
        run_id="fb6eb9ee-31c4-5bf8-83d7-404cd15648d6",
        database_url="postgresql+asyncpg://unused", output=output,
    ))
    row = next(csv.DictReader(output.open(encoding="utf-8")))
    assert row["candidate_id"] == "11111111-1111-1111-1111-111111111111"
    assert row["source_proposal_id"] == "proposal-03e1920e95919d33fdd9"
