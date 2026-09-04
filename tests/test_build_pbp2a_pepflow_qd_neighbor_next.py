import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from build_gyra_pepflow_qd_neighbor_next import _donors, _local_history  # noqa: E402
from build_pbp2a_pepflow_qd_neighbor_next import (  # noqa: E402
    generate_two_aa_fallback,
    load_authoritative_parents,
)

REPORT = ROOT / "reports" / "pbp2a_pepflow_same_domain_20260904"


def test_replacement_queue_resolves_five_authoritative_parent_uuids() -> None:
    parents = load_authoritative_parents(
        REPORT / "candidate_scores_calibrated.csv",
        REPORT / "qd_candidates.csv",
        REPORT / "pg_materialization_verification.json",
        REPORT / "prepared_structure_receipt.json",
    )
    assert len(parents) == 5
    assert {row["candidate_id"] for row in parents} == {
        "1a84123a-9758-419b-b9ba-6b629cd5069f",
        "b6937ce0-9637-441c-8b94-6d1fbc5041e7",
        "eb1bc382-b789-433e-8484-4827ad1debf5",
        "e1e21247-1a58-4252-b062-664fd685467b",
        "0b859377-8750-44d6-bccc-791d94a6453e",
    }
    assert {row["run_id"] for row in parents} == {
        "aaf5d3fd-dc43-5677-933d-88db62ba131d"
    }


def test_parent_loader_fails_closed_on_uuid_drift(tmp_path: Path) -> None:
    prepared = json.loads(
        (REPORT / "prepared_structure_receipt.json").read_text(encoding="utf-8")
    )
    prepared["candidate_ids"][0] = "00000000-0000-4000-8000-000000000000"
    prepared_path = tmp_path / "prepared.json"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    with pytest.raises(ValueError, match="authoritative parent UUID/hash"):
        load_authoritative_parents(
            REPORT / "candidate_scores_calibrated.csv",
            REPORT / "qd_candidates.csv",
            REPORT / "pg_materialization_verification.json",
            prepared_path,
        )


def test_fallback_batch_receipts_remain_provisional_and_fail_closed() -> None:
    output = ROOT / "reports" / "pbp2a_pepflow_qd_neighbor_next_20260904"
    generation = json.loads(
        (output / "generation_receipt.json").read_text(encoding="utf-8")
    )
    score = json.loads((output / "score_all" / "receipt.json").read_text(encoding="utf-8"))
    calibration = json.loads(
        (output / "calibration_receipt.json").read_text(encoding="utf-8")
    )
    challenger = json.loads(
        (output / "challenger" / "receipt.json").read_text(encoding="utf-8")
    )
    qd = json.loads((output / "provisional_qd_receipt.json").read_text(encoding="utf-8"))
    close = json.loads((output / "close_receipt.json").read_text(encoding="utf-8"))

    assert generation["generation_strategy"] == (
        "two_aa_nonadjacent_pepflow_micrograft_fallback"
    )
    assert generation["operator_id"].endswith("-2aa-v1")
    assert generation["proposal_count"] == 2
    assert score["formal_12_complete_count"] == score["display_eligible_count"] == 2
    assert calibration["support_ge_2_count"] == 0
    assert challenger["reviewed_candidate_count"] == 2
    assert challenger["challenger_no_conflict_count"] == 2
    assert qd["fixed_cell_count"] == 2160
    assert qd["provisional_new_cell_count"] == qd["provisional_replacement_count"] == 0
    assert close["persistence"]["historical_pg_gate"] == "pending"
    assert close["persistence"]["pool_a_admitted"] is False
    assert close["persistence"]["postgresql_reads"] == 0
    assert close["persistence"]["postgresql_writes"] == 0


def test_two_aa_fallback_rejects_prior_parent_position_edit() -> None:
    parents = load_authoritative_parents(
        REPORT / "candidate_scores_calibrated.csv",
        REPORT / "qd_candidates.csv",
        REPORT / "pg_materialization_verification.json",
        REPORT / "prepared_structure_receipt.json",
    )
    donors = _donors(
        ROOT / "reports" / "pbp2a_pepflow_same_domain_20260904" / "proposals.csv"
    )
    archive_payload = json.loads(
        (
            ROOT
            / "reports"
            / "autoresearch_lineage_round131_pbp2a_qd_elite_prior_v1_20260902"
            / "quality_diversity_before.json"
        ).read_text(encoding="utf-8")
    )
    archive = archive_payload["branches"]["pbp2a"]
    historical_hashes, prior_edits, _ = _local_history(REPORT)
    baseline = generate_two_aa_fallback(
        parents,
        donors,
        archive,
        historical_hashes,
        prior_edits,
        limit=1,
        generation=133,
        seed=20260904,
    )
    assert baseline
    row = baseline[0]
    positions = [int(value) for value in row["edit_positions_zero_based"].split(";")]
    residues = row["to_residues"].split(";")
    parent_sequence = row["parent_sequence"]
    blocked_edits = prior_edits | {
        (parent_sequence, positions[0], residues[0]),
        (parent_sequence, positions[1], residues[1]),
    }
    blocked = generate_two_aa_fallback(
        parents,
        donors,
        archive,
        historical_hashes,
        blocked_edits,
        limit=1,
        generation=133,
        seed=20260904,
    )
    assert not blocked or blocked[0]["sequence"] != row["sequence"]
