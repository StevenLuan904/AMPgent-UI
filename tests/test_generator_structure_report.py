from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
import yaml

from pepagent.generator_structure_report import (
    REQUIRED_METRICS,
    build_candidate_rows,
    build_summary_rows,
    select_v31b_confirmation_cohort,
)


def _fixture() -> tuple[list[dict[str, str]], dict[str, object]]:
    cohort = []
    calls = []
    evaluations = []
    tools = {
        "boltz2_pair_iptm": "boltz2",
        "boltz2_pair_iptm_median": "coordinate-interface-audit",
        "pocket_contact_count": "coordinate-interface-audit",
        "pocket_coverage_fraction": "coordinate-interface-audit",
        "off_pocket_contact_fraction": "coordinate-interface-audit",
        "interface_clash_count": "coordinate-interface-audit",
        "interface_min_distance_angstrom": "coordinate-interface-audit",
        "rosetta_dg_separated_reu": "pyrosetta-flexpepdock-interface-analyzer",
        "rosetta_dg_minimum_reu": "pyrosetta-flexpepdock-interface-analyzer",
        "rosetta_peptide_bb_rmsd_angstrom": "pyrosetta-flexpepdock-interface-analyzer",
        "rosetta_interface_score": "pyrosetta-flexpepdock-interface-analyzer",
        "rosetta_reweighted_score": "pyrosetta-flexpepdock-interface-analyzer",
        "rosetta_interface_hbonds": "pyrosetta-flexpepdock-interface-analyzer",
        "rosetta_buried_surface_area": "pyrosetta-flexpepdock-interface-analyzer",
    }
    for index in range(90):
        digest = f"{index:064x}"
        cohort.append(
            {
                "screening_rank": str(index + 1),
                "generator_id": ("hydramp", "ampgan_v2", "amp_designer")[index // 30],
                "generator_seed": str(index // 10),
                "within_seed_diversity_rank": str(index % 10 + 1),
                "source_id": "source",
                "source_candidate_id": f"source-{index}",
                "source_selected_rank": str(index + 1),
                "sequence": "A" * 10 + "C" + str(index),
                "sequence_sha256": digest,
            }
        )
        call_ids = {}
        for tool in set(tools.values()):
            call_id = f"{tool}-{index}"
            call_ids[tool] = call_id
            calls.append({"id": call_id, "tool_name": tool, "status": "succeeded"})
        for metric_index, metric in enumerate(REQUIRED_METRICS):
            value = float(index + metric_index)
            if metric == "interface_clash_count":
                value = 0.0
            elif metric == "pocket_contact_count":
                value = 10.0
            evaluations.append(
                {
                    "candidate_id": f"candidate-{index}",
                    "candidate_sequence_sha256": digest,
                    "tool_call_id": call_ids[tools[metric]],
                    "metric_name": metric,
                    "numeric_value": value,
                    "status": "succeeded",
                }
            )
    return cohort, {"tool_calls": calls, "evaluations": evaluations}


def test_build_candidate_rows_and_summaries() -> None:
    cohort, evidence = _fixture()
    rows = build_candidate_rows(cohort, evidence)
    assert len(rows) == 90
    assert rows[0]["candidate_id"] == "candidate-0"
    seed_rows = build_summary_rows(rows, ("generator_id", "generator_seed"))
    assert len(seed_rows) == 9
    assert all(row["candidate_count"] == 10 for row in seed_rows)
    generator_rows = build_summary_rows(rows, ("generator_id",))
    assert len(generator_rows) == 3
    assert all(row["candidate_count"] == 30 for row in generator_rows)


def test_candidate_export_rejects_nonfinite_value() -> None:
    cohort, evidence = _fixture()
    evidence["evaluations"][0]["numeric_value"] = math.nan  # type: ignore[index]
    with pytest.raises(ValueError, match="non-finite"):
        build_candidate_rows(cohort, evidence)


def test_v31b_preregistration_preserves_confirmation_boundaries() -> None:
    path = (
        Path(__file__).parents[1]
        / "config"
        / "benchmarks"
        / "amp_generator_target_structure_v31b.yaml"
    )
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["execution_status"] == "cohort_frozen_execution_pending"
    assert payload["execution"]["execution_authorized"] is False
    assert payload["confirmation_cohort"]["expected_total"] == 18
    assert payload["confirmation_protocol"]["expected_structures_per_candidate"] == 3
    assert payload["confirmation_protocol"]["expected_rosetta_decoys_per_candidate"] == 48
    assert payload["analysis"]["generator_comparison"]["weighted_total_score_forbidden"]
    assert payload["claim_boundary"]["no_binding_claim"]
    assert "PepMLM_delta_nll" in payload["selection"]["forbidden_selection_inputs"]
    assert payload["frozen_selection"]["selected_count"] == 18
    for kind in ("cohort", "audit"):
        relative = payload["frozen_selection"][f"{kind}_path"]
        artifact = (path.parent / relative).resolve()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == payload[
            "frozen_selection"
        ][f"{kind}_sha256"]


def test_v31b_selection_is_balanced_unique_and_deterministic() -> None:
    cohort, evidence = _fixture()
    rows = build_candidate_rows(cohort, evidence)
    first, audit = select_v31b_confirmation_cohort(rows)
    second, _ = select_v31b_confirmation_cohort(list(reversed(rows)))
    assert first == second
    assert len(first) == 18
    assert len({row["sequence_sha256"] for row in first}) == 18
    assert [row["generator_id"] for row in first].count("hydramp") == 6
    assert [row["generator_id"] for row in first].count("ampgan_v2") == 6
    assert [row["generator_id"] for row in first].count("amp_designer") == 6
    assert audit["global_sequence_uniqueness"] is True
