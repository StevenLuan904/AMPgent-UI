import csv
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
REPORT = ROOT / "reports" / "acea_pepflow_qd_neighbor_vnext_20260904_generation5_v2"
BACKLOG = (
    ROOT
    / "reports"
    / "targeted_rosetta_coarse5_backlog_20260904"
    / "vnext_acea_generation5_20260904"
)
PARENTS = {
    "91309ee5-0f3c-4796-8367-851f24a68fb2",
    "a0bb708e-276a-4c9d-aa48-381a4532bb5c",
}


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_generation5_is_twelve_identity_bound_one_aa_proposals() -> None:
    rows = _csv(REPORT / "proposals.csv")
    receipt = _json(REPORT / "generation_receipt.json")
    assert len(rows) == 12
    assert receipt["generation"] == 5
    assert receipt["parent_count"] == 2
    assert set(receipt["authoritative_parent_candidate_ids"]) == PARENTS
    assert receipt["parent_exact_readback_receipt"]
    assert receipt["identity_contract"] == {
        "sequence_sha256_unique": True,
        "sequence_order_preserved": True,
        "target_key": "acea",
        "output_dir_is_independent": True,
    }
    assert {row["parent_candidate_id"] for row in rows} <= PARENTS
    assert {row["proposal_mode"] for row in rows} == {"pepflow_qd_neighbor_1aa"}
    assert all(row["acceptor_start_zero_based"].isdigit() for row in rows)
    assert all(row["delta_phi_skill"] for row in rows)
    assert len({row["sequence_sha256"] for row in rows}) == 12


def test_generation5_materializes_only_one_qd_contribution_and_merges_backlog() -> None:
    close = _json(REPORT / "close_receipt.json")
    readback = _json(REPORT / "pg_exact_readback_receipt.json")
    queue = _json(REPORT / "coarse5_prepared" / "coarse5_prepared_receipt.json")
    backlog = _json(BACKLOG / "targeted_rosetta_coarse5_backlog_vnext.json")

    assert close["stage_counts"]["proposal"] == 12
    assert close["stage_counts"]["formal12"] == 12
    assert close["stage_counts"]["display"] == 12
    assert close["stage_counts"]["support_ge_2"] == 1
    assert close["source_provisional_qd"] == {
        "quality_eligible_count": 1,
        "new_cell_count": 1,
        "replacement_count": 0,
    }
    assert close["final_qd"] == {
        "status": "formal_pg_new_materialized",
        "quality_eligible_count": 1,
        "new_cell_count": 1,
        "replacement_count": 0,
        "materialized_contribution_count": 1,
        "formal_pg_new": True,
        "future_priority_only": False,
    }
    assert readback["status"] == "readback_verified"
    assert readback["candidate_count"] == 1
    assert readback["evaluation_count"] == 17
    assert readback["identity_drift"] == 0
    assert queue["candidate_count"] == queue["task_key_count"] == 1
    assert queue["nstruct"] == 5
    assert queue["dispatch_allowed"] is False
    assert backlog["summary"]["rows"] == 53
    assert backlog["summary"]["target_counts"]["acea"] == 7
    assert backlog["summary"]["unique_identity"] is True
    assert backlog["summary"]["unique_canonical_task_key"] is True
    assert backlog["dispatch_allowed"] is False
