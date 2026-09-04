import csv
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
BATCH = ROOT / "reports/gyrA_pepflow_generation5_20260904"


def test_registered_empty_cells_exclude_generation4_parent_cells() -> None:
    preflight = json.loads(
        (BATCH / "preflight_registration.json").read_text(encoding="utf-8")
    )
    with (BATCH / "parents.csv").open(encoding="utf-8-sig", newline="") as handle:
        parents = list(csv.DictReader(handle))
    with (BATCH / "proposals.csv").open(encoding="utf-8-sig", newline="") as handle:
        proposals = list(csv.DictReader(handle))
    occupied = {row["qd_cell"] for row in parents}
    registered = set(preflight["registered_target_empty_cells"])
    assert registered.isdisjoint(occupied)
    assert set(preflight["parent_occupied_cells"]) == occupied
    assert {row["actual_cell_preflight"] for row in proposals}.isdisjoint(occupied)


def test_generation5_protects_core_and_uses_distinct_pepflow_residues() -> None:
    receipt = json.loads(
        (BATCH / "generation_receipt.json").read_text(encoding="utf-8")
    )
    with (BATCH / "proposals.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 12
    assert receipt["generation"] == 5
    assert receipt["operator_id"] == "gyrA-pepflow-followup-1aa-c-terminal-empty-cell-v1"
    assert {row["donor_fragment"] for row in rows} >= {"G", "S", "V"}
    assert all(int(row["edit_position_zero_based"]) >= 11 for row in rows)
    assert all("WW" in row["parent_sequence"][:7] for row in rows)
    assert all(
        row["actual_cell_preflight"] in set(receipt["registered_empty_cells"])
        for row in rows
    )


def test_generation5_coarse_receipt_preserves_hybrid_provenance() -> None:
    receipt = json.loads(
        (BATCH / "coarse5_prepared/coarse5_prepared_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["source"] == "PepGLAD-ancestry_x_PepFlow-donor"
    assert receipt["evidence_arm"] == receipt["source"]
    assert receipt["parent_source"] == "generation4_hybrid_QD_elite"
    assert receipt["immediate_parent_source"] == "generation4_hybrid_QD_elite"
    assert receipt["ancestral_parent_source"] == "PepGLAD"
    assert receipt["donor_source"] == "PepFlow"

    close = json.loads((BATCH / "close_receipt.json").read_text(encoding="utf-8"))
    assert close["source"] == "PepGLAD-ancestry_x_PepFlow-donor"
    assert close["evidence_arm"] == close["source"]
    assert close["parent_source"] == "generation4_hybrid_QD_elite"
    assert close["immediate_parent_source"] == "generation4_hybrid_QD_elite"
    assert close["ancestral_parent_source"] == "PepGLAD"
    assert close["donor_source"] == "PepFlow"

    with (BATCH / "coarse5_prepared/coarse5_prepared_queue.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        queue = list(csv.DictReader(handle))
    assert all(row["source"] == close["source"] for row in queue)
    assert all(row["parent_source"] == close["parent_source"] for row in queue)
    assert all(row["donor_source"] == close["donor_source"] for row in queue)
