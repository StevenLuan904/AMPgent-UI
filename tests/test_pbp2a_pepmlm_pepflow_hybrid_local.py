from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pbp2a_pepmlm_pepflow_hybrid_20260904_run2"
SOURCE = ROOT / "analysis" / "build_pbp2a_pepmlm_pepflow_hybrid_local.py"
SPEC = importlib.util.spec_from_file_location("pbp2a_hybrid_local", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CLOSE_SOURCE = ROOT / "analysis" / "close_pbp2a_pepmlm_pepflow_hybrid_local.py"
CLOSE_SPEC = importlib.util.spec_from_file_location("pbp2a_hybrid_local_close", CLOSE_SOURCE)
assert CLOSE_SPEC and CLOSE_SPEC.loader
CLOSE_MODULE = importlib.util.module_from_spec(CLOSE_SPEC)
CLOSE_SPEC.loader.exec_module(CLOSE_MODULE)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_local_hybrid_receipts_are_complete_but_pg_pending() -> None:
    assert callable(CLOSE_MODULE.close)
    generation = json.loads((REPORT / "generation_receipt.json").read_text(encoding="utf-8"))
    score = json.loads((REPORT / "score_all" / "receipt.json").read_text(encoding="utf-8"))
    calibration = json.loads(
        (REPORT / "calibration_receipt.json").read_text(encoding="utf-8")
    )
    challenger = json.loads(
        (REPORT / "challenger" / "receipt.json").read_text(encoding="utf-8")
    )
    qd = json.loads((REPORT / "provisional_qd.json").read_text(encoding="utf-8"))
    close = json.loads((REPORT / "close_receipt.json").read_text(encoding="utf-8"))

    assert generation["parent_count"] == 5
    assert generation["proposal_count"] == 12
    assert generation["fixed_archive_cell_count"] == 2160
    assert score["formal_12_complete_count"] == 12
    assert score["display_eligible_count"] == 11
    assert calibration["support_ge_2_count"] == 5
    assert challenger["reviewed_candidate_count"] == 12
    assert challenger["candidate_identity_coverage_complete"] is True
    assert qd["new_cell_count"] == 4
    assert qd["replacement_count"] == 0
    assert close["qd"]["valid_candidate_count"] == 4
    assert close["algorithm_adjustment"]["triggered"] is False
    assert close["persistence"]["historical_pg_gate"] == "pending"
    assert close["persistence"]["pool_a_admitted"] is False
    assert close["persistence"]["postgresql_reads"] == 0
    assert close["persistence"]["postgresql_writes"] == 0


def test_local_hybrid_motifs_are_bounded_and_history_excludes_negative_batch() -> None:
    proposals = _read(REPORT / "proposals.csv")
    negative = _read(
        ROOT / "reports" / "pbp2a_pepflow_qd_neighbor_next_20260904" / "proposals.csv"
    )
    negative_hashes = {MODULE.sha256_text(row["sequence"]) for row in negative}
    assert len(proposals) == 12
    assert not negative_hashes.intersection(row["sequence_sha256"] for row in proposals)
    assert {int(row["edit_length"]) for row in proposals} == {2, 3}
    assert all(float(row["edit_fraction"]) <= 0.25 for row in proposals)
    assert len({row["sequence_sha256"] for row in proposals}) == 12
    assert len({row["actual_cell_preflight"] for row in proposals}) == 12
    assert all(row["historical_pg_gate"] == "pending" for row in proposals)
    assert all(row["materialization_status"] == "proposed_not_materialized" for row in proposals)


def test_generation_107_closes_fail_closed_when_frozen_activity_support_is_zero() -> None:
    report = ROOT / "reports" / "pbp2a_pepmlm_pepflow_hybrid_20260904_run3"
    generation = json.loads((report / "generation_receipt.json").read_text(encoding="utf-8"))
    score = json.loads((report / "score_all" / "receipt.json").read_text(encoding="utf-8"))
    calibration = json.loads((report / "calibration_receipt.json").read_text(encoding="utf-8"))
    challenger = json.loads((report / "challenger" / "receipt.json").read_text(encoding="utf-8"))
    qd = json.loads((report / "provisional_qd.json").read_text(encoding="utf-8"))
    close = json.loads((report / "close_receipt.json").read_text(encoding="utf-8"))
    closed_loop = json.loads((report / "closed_loop_receipt.json").read_text(encoding="utf-8"))

    assert generation["generation"] == 107
    assert generation["proposal_count"] == 10
    assert generation["operator_variant"] == "activity_preserving_fallback"
    assert generation["fixed_archive_cell_count"] == 2160
    assert score["proposal_count"] == 10
    assert score["formal_12_complete_count"] == 10
    assert score["display_eligible_count"] == 10
    assert calibration["support_ge_2_count"] == 0
    assert challenger["reviewed_candidate_count"] == 10
    assert challenger["candidate_identity_coverage_complete"] is True
    assert qd["quality_eligible_count"] == 0
    assert qd["new_cell_count"] == 0
    assert qd["replacement_count"] == 0
    assert close["qd"]["valid_candidate_count"] == 0
    assert close["identity_contract"]["candidate_count"] == 10
    assert close["identity_contract"]["sequence_order_verified"] is True
    assert close["persistence"]["candidate_identity_status"] == "proposal_only"
    assert close["algorithm_adjustment"]["reason"] == (
        "no_valid_qd_candidate_after_frozen_activity_calibration"
    )
    assert closed_loop["verify"]["pg_new_count"] == 0
    assert closed_loop["verify"]["prepared_coarse5_count"] == 0
    assert closed_loop["identity_contract"]["rows"] == 10
    assert closed_loop["identity_contract"]["sequence_overlap"] == 10
    assert closed_loop["identity_contract"]["order_match"] is True
    proposals = _read(report / "proposals.csv")
    assert len(proposals) == 10
    assert len({row["sequence_sha256"] for row in proposals}) == 10
    assert len({row["actual_cell_preflight"] for row in proposals}) == 10
    assert all(
        row["protected_key_motif_policy"]
        == "preserve_charged_scaffold;mutate_only_low_attribution_neutral_positions"
        for row in proposals
    )
