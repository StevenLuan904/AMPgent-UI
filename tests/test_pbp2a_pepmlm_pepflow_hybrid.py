from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pbp2a_pepmlm_pepflow_hybrid_20260904"
SOURCE = ROOT / "analysis" / "build_pbp2a_pepmlm_pepflow_hybrid.py"
SPEC = importlib.util.spec_from_file_location("pbp2a_pepmlm_pepflow_hybrid", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
OPERATOR_ID = MODULE.OPERATOR_ID
archive_cells = MODULE.archive_cells
candidate_cell = MODULE.candidate_cell
generate = MODULE.generate
select_donors = MODULE.select_donors


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_five_no_conflict_parents_and_empty_cells_are_disjoint() -> None:
    parents = _read(REPORT / "parents_no_conflict.csv")
    archive = ROOT / "reports" / "pbp2a_pepmlm_qd_gap_v1_20260904" / "qd_receipt.json"
    empty, _, _ = archive_cells(archive)
    occupied = {candidate_cell(row["sequence"], archive) for row in parents}
    assert len(parents) == 5
    assert len({row["candidate_id"] for row in parents}) == 5
    registered_empty = empty - occupied
    assert registered_empty.isdisjoint(occupied)


def test_generated_batch_has_new_operator_and_unique_empty_cells() -> None:
    parents = _read(REPORT / "parents_no_conflict.csv")
    donors = select_donors(
        _read(ROOT / "reports" / "pbp2a_pepflow_same_domain_20260904" / "proposals.csv")
    )
    archive = ROOT / "reports" / "pbp2a_pepmlm_qd_gap_v1_20260904" / "qd_receipt.json"
    proposals, registered_empty = generate(
        parents,
        donors,
        set(),
        set(),
        archive,
        generation=105,
        seed=20260904,
        limit=12,
    )
    assert proposals
    assert len({row["sequence_sha256"] for row in proposals}) == len(proposals)
    assert len({row["actual_cell_preflight"] for row in proposals}) == len(proposals)
    assert all(row["actual_cell_preflight"] in registered_empty for row in proposals)
    assert all(row["operator_id"] == OPERATOR_ID for row in proposals)
    assert all(
        row["source_arm"] == "PepMLM-target-conditioned-ancestry_x_PepFlow-donor"
        for row in proposals
    )
    assert all(row["parent_source"] == "PepMLM-target-conditioned" for row in proposals)
    assert all(row["donor_source"] == "PepFlow" for row in proposals)


def test_generation_contract_is_explicit() -> None:
    assert OPERATOR_ID == "pbp2a-pepmlm-ancestry-pepflow-donor-qd-gap-v1"
    assert (
        json.loads('{"parent_source":"PepMLM-target-conditioned"}')["parent_source"]
        == "PepMLM-target-conditioned"
    )


def test_materialized_cohort_and_task_keys_are_exact() -> None:
    root = REPORT.parent / "pbp2a_pepmlm_pepflow_hybrid_20260904_run1"
    materialization = json.loads(
        (root / "materialization_receipt.json").read_text(encoding="utf-8")
    )
    verification = json.loads((root / "pg_verification.json").read_text(encoding="utf-8"))
    queue = _read(root / "coarse5_prepared" / "coarse5_prepared_queue.csv")
    assert materialization["materialized_or_reused_in_run_count"] == 3
    assert materialization["inserted_evaluation_count"] == 51
    assert verification["per_candidate_evidence_count_17"] is True
    assert len(queue) == 3
    assert all(
        row["task_key"] == f"rosetta-coarse5:pbp2a:{row['run_id']}:{row['candidate_id']}"
        for row in queue
    )
    assert all(row["nstruct"] == "5" and row["dispatch_allowed"] == "false" for row in queue)
