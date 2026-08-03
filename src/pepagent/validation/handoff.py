from __future__ import annotations

from pathlib import Path
from typing import Any

from pepagent.domain.schemas import OptionalMetricSpec
from pepagent.model_workers.sequence_metrics_cli import evaluate
from pepagent.provenance.hashing import sha256_file
from pepagent.structures.pdb import atom_chain_sequence


def validate_handoff_metric_control(
    suite: dict[str, Any],
    source_path: Path,
    work_root: Path,
    registry_path: Path | None,
) -> dict[str, Any]:
    """Run opt-in metrics on a hash-locked public protein-peptide complex."""
    case = suite["case"]
    actual_sha256 = sha256_file(source_path)
    if actual_sha256 != case["source_sha256"]:
        raise ValueError(
            f"{case['pdb_id']} source hash mismatch: "
            f"{actual_sha256} != {case['source_sha256']}"
        )
    observed_sequence = atom_chain_sequence(source_path, [case["peptide_chain"]])
    modeled_sequence = case.get("modeled_peptide_sequence", case["peptide_sequence"])
    if observed_sequence != modeled_sequence:
        raise ValueError(
            f"{case['pdb_id']} peptide sequence mismatch: {observed_sequence}"
        )

    candidate = {
        "id": f"pdb-{case['pdb_id'].lower()}-auth-chain-{case['peptide_chain'].lower()}",
        "sequence": case["peptide_sequence"],
    }
    results: dict[str, dict[str, Any]] = {}
    for raw_plugin in suite["metrics"]:
        plugin = OptionalMetricSpec.model_validate(raw_plugin).model_dump(mode="json")
        if not plugin["enabled"]:
            continue
        results[plugin["name"]] = evaluate(
            {
                "run_id": suite["suite_id"],
                "generation": 0,
                "stage": "final",
                "plugin": plugin,
                "candidates": [candidate],
            },
            work_root / plugin["name"],
            registry_path,
        )

    descriptor_result = results["physicochemical_developability"]
    observed_descriptors = {
        item["metric_name"]: item["numeric_value"]
        for item in descriptor_result["records"][0]["observations"]
    }
    expected_descriptors = {
        key: value
        for key, value in suite["descriptor_reference"].items()
        if key
        in {
            "molecular_weight_da",
            "net_charge_ph7_4",
            "isoelectric_point",
            "hydrophobic_ratio_modlamp",
            "hydrophobic_moment_eisenberg",
        }
    }
    descriptor_deltas = {
        key: observed_descriptors[key] - float(expected)
        for key, expected in expected_descriptors.items()
    }
    descriptor_reproduced = all(abs(delta) <= 1e-12 for delta in descriptor_deltas.values())
    statuses = {name: result["status"] for name, result in results.items()}
    return {
        "suite_id": suite["suite_id"],
        "pdb_id": case["pdb_id"],
        "source_sha256": actual_sha256,
        "peptide_chain": case["peptide_chain"],
        "peptide_sequence": case["peptide_sequence"],
        "modeled_peptide_sequence": observed_sequence,
        "experimental_property_reference": case["property_primary_source"],
        "metric_statuses": statuses,
        "descriptor_reproduced": descriptor_reproduced,
        "descriptor_deltas": descriptor_deltas,
        "overall_status": (
            "complete" if all(status == "complete" for status in statuses.values()) else "partial"
        ),
        "results": results,
        "interpretation": {
            "plumbing_control_only": True,
            "single_case_calibration": False,
            "unavailable_metrics_leave_candidate_eligible": True,
        },
    }
