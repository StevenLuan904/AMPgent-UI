from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from pepagent.autoresearch_structure_cohort import (
    TARGET_KEYS,
    freeze_structure_escalation_cohort,
)
from pepagent.autoresearch_structure_submit import build_structure_formal_plan
from pepagent.provenance.hashing import sha256_file, sha256_text

ALPHABET = "ACDEFGHIKLMNPQRSTVWY"


def _sequence(index: int) -> str:
    digits: list[str] = []
    value = index
    for _ in range(3):
        digits.append(ALPHABET[value % len(ALPHABET)])
        value //= len(ALPHABET)
    return "".join(digits) + "KRWLAKIRKL"


def _cohort(tmp_path: Path):
    strict = tmp_path / "strict.csv"
    fieldnames = [
        "activity_model_support_count",
        "candidate_id",
        "display_eligible",
        "family_key_80_80",
        "formal_metric_count",
        "formal_metrics_complete",
        "generator_id",
        "guruprasad_instability_index",
        "guruprasad_instability_ood",
        "macrel_hemolysis_label",
        "maximum_hydrophobic_run",
        "pareto_depth_within_expansion_target",
        "safety_labels_pass",
        "sequence",
        "sequence_sha256",
        "source_result_sha256",
        "target_key",
        "toxinpred3_label",
        "valid_sequence",
    ]
    with strict.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        ordinal = 0
        for target_key in TARGET_KEYS:
            for rank in range(50):
                sequence = _sequence(ordinal)
                writer.writerow(
                    {
                        "activity_model_support_count": 2 + rank % 2,
                        "candidate_id": f"{target_key}-candidate-{rank:03d}",
                        "display_eligible": "True",
                        "family_key_80_80": f"family-{target_key}-{rank:03d}",
                        "formal_metric_count": 12,
                        "formal_metrics_complete": "True",
                        "generator_id": "fixture",
                        "guruprasad_instability_index": 10 + rank / 10,
                        "guruprasad_instability_ood": "False",
                        "macrel_hemolysis_label": "low",
                        "maximum_hydrophobic_run": 2,
                        "pareto_depth_within_expansion_target": 1 + rank % 3,
                        "safety_labels_pass": "True",
                        "sequence": sequence,
                        "sequence_sha256": sha256_text(sequence),
                        "source_result_sha256": sha256_text(
                            f"source-{target_key}-{rank}"
                        ),
                        "target_key": target_key,
                        "toxinpred3_label": "Non-Toxin",
                        "valid_sequence": "True",
                    }
                )
                ordinal += 1
    strict_sha = sha256_file(strict)
    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-scoreall-bundle.v2",
                "status": "succeeded",
                "run_id": "fixture-score",
                "storage_uri": "fixture://score",
                "global_strict_library": {
                    "sha256": strict_sha,
                    "bytes": strict.stat().st_size,
                },
                "runtime": {"registry_sha256": sha256_text("registry")},
            }
        ),
        encoding="utf-8",
    )
    manifest = Path("config/targets/ampgent_six_target_sequence_manifest_20260824.json")
    catalog = Path("config/pockets/mvp_v2_pocket_catalog.yaml")
    return freeze_structure_escalation_cohort(
        strict_library_path=strict,
        strict_library_sha256=strict_sha,
        bundle_receipt_path=bundle,
        bundle_receipt_sha256=sha256_file(bundle),
        target_manifest_path=manifest,
        target_manifest_sha256=sha256_file(manifest),
        pocket_catalog_path=catalog,
        pocket_catalog_sha256=sha256_file(catalog),
        per_target_count=50,
    )


def test_builds_deterministic_six_target_structure_submission(tmp_path: Path) -> None:
    cohort = _cohort(tmp_path)
    manifest_path = Path(
        "config/targets/ampgent_six_target_sequence_manifest_20260824.json"
    )
    catalog_path = Path("config/pockets/mvp_v2_pocket_catalog.yaml")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    plan = build_structure_formal_plan(
        cohort=cohort,
        cohort_path=tmp_path / "cohort.json",
        target_manifest_payload=manifest,
        pocket_catalog_payload=catalog,
    )
    repeated = build_structure_formal_plan(
        cohort=cohort,
        cohort_path=tmp_path / "cohort.json",
        target_manifest_payload=manifest,
        pocket_catalog_payload=catalog,
    )

    assert len(plan.branches) == 6
    assert plan.plan_identity == repeated.plan_identity
    assert [item.run_id for item in plan.branches] == [
        item.run_id for item in repeated.branches
    ]
    assert len({item.run_id for item in plan.branches}) == 6
    assert all(len(item.candidates) == 50 for item in plan.branches)
    assert all(item.workflow_spec["rosetta_nstruct"] == 200 for item in plan.branches)
    assert all(item.workflow_spec["boltz_seeds_per_candidate"] == 3 for item in plan.branches)
    assert all(item.workflow_spec["bulk_evaluation_concurrency"] == 1 for item in plan.branches)

    by_target = {item.target_key: item for item in plan.branches}
    for target_key in ("acea", "gyra", "pbp2a"):
        branch = by_target[target_key]
        assert branch.workflow_spec["structure_protocol"] == "legacy_ensemble_gate"
        assert branch.workflow_spec["boltz_force_pocket"] is True
        assert branch.workflow_spec["target"]["pocket_residues"]
        assert branch.workflow_spec["rosetta_interpretation"] == (
            "same-protocol relative target-conditioned ranking only"
        )
    for target_key in ("vegfa", "fgf2", "angpt1"):
        branch = by_target[target_key]
        assert branch.workflow_spec["structure_protocol"] == "diagnostic_fast"
        assert branch.workflow_spec["boltz_force_pocket"] is False
        assert branch.workflow_spec["target"]["pocket_residues"] == []
        assert branch.workflow_spec["rosetta_interpretation"] == (
            "exploratory low-confidence structure diagnostic only"
        )
        assert "target_interface_mapping_unqualified" in branch.workflow_spec[
            "target_structure_limitations"
        ]
    assert all(
        item.workflow_spec["binding_or_affinity_claim_allowed"] is False
        for item in plan.branches
    )
