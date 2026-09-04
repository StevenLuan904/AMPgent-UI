import csv
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
BATCH = ROOT / "reports/gyrA_pepflow_second_generation_20260904"


def test_second_generation_is_explicit_hybrid_and_pg_new() -> None:
    with (BATCH / "proposals.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 12
    assert {row["parent_source"] for row in rows} == {"PepGLAD"}
    assert {row["donor_source"] for row in rows} == {"PepFlow"}
    assert {row["donor_artifact_id"] for row in rows} == {
        "proposal-dcd55e4ba83c52513d92"
    }
    assert all(len(row["donor_fragment"]) == 2 for row in rows)
    assert len({row["sequence_sha256"] for row in rows}) == 12
    assert all(row["history_gate"].startswith("postgresql_exact") for row in rows)


def test_second_generation_qd_and_prepared_queue_use_authoritative_ids() -> None:
    qd = json.loads((BATCH / "quality_diversity.json").read_text(encoding="utf-8"))
    material = json.loads(
        (BATCH / "materialization_receipt.json").read_text(encoding="utf-8")
    )
    with (BATCH / "coarse5_prepared/coarse5_prepared_queue.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        queue = list(csv.DictReader(handle))
    assert len(qd["contributions"]) == 3
    assert qd["diversity_gain"] == 3
    assert material["operational_run_id"] == "8b9aba3a-ca15-54fe-bfa9-bba6c065f229"
    assert material["inserted_evaluation_count"] == 51
    assert len(queue) == 3
    assert all(row["candidate_id"].count("-") == 4 for row in queue)
    assert all(
        row["task_key"]
        == f"rosetta-coarse5:gyra:{row['run_id']}:{row['candidate_id']}"
        for row in queue
    )
    assert all(row["nstruct"] == "5" for row in queue)
    assert all(row["dispatch_allowed"] == "false" for row in queue)
