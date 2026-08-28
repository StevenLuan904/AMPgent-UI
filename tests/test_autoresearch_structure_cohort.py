from __future__ import annotations

import csv
import json
from pathlib import Path

from pepagent.autoresearch_structure_cohort import (
    TARGET_KEYS,
    freeze_structure_escalation_cohort,
)
from pepagent.provenance.hashing import sha256_file, sha256_text

ALPHABET = "ACDEFGHIKLMNPQRSTVWY"


def _sequence(index: int) -> str:
    digits = []
    value = index
    for _ in range(3):
        digits.append(ALPHABET[value % len(ALPHABET)])
        value //= len(ALPHABET)
    return "".join(digits) + "KRWLAKIRKL"


def _write_strict_library(path: Path) -> None:
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
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        ordinal = 0
        for target in TARGET_KEYS:
            for within_target in range(50):
                sequence = _sequence(ordinal)
                writer.writerow(
                    {
                        "activity_model_support_count": 2 + within_target % 2,
                        "candidate_id": f"{target}-candidate-{within_target:03d}",
                        "display_eligible": "True",
                        "family_key_80_80": f"seqfam80_{target}_{within_target:03d}",
                        "formal_metric_count": 12,
                        "formal_metrics_complete": "True",
                        "generator_id": "fixture-generator",
                        "guruprasad_instability_index": 10 + within_target / 10,
                        "guruprasad_instability_ood": "False",
                        "macrel_hemolysis_label": "low",
                        "maximum_hydrophobic_run": 2,
                        "pareto_depth_within_expansion_target": 1 + within_target % 3,
                        "safety_labels_pass": "True",
                        "sequence": sequence,
                        "sequence_sha256": sha256_text(sequence),
                        "source_result_sha256": sha256_text(f"source-{target}-{within_target}"),
                        "target_key": target,
                        "toxinpred3_label": "Non-Toxin",
                        "valid_sequence": "True",
                    }
                )
                ordinal += 1


def test_freezes_six_target_family_diverse_structure_cohort(tmp_path: Path) -> None:
    strict_path = tmp_path / "strict.csv"
    _write_strict_library(strict_path)
    strict_sha256 = sha256_file(strict_path)
    bundle_path = tmp_path / "bundle.receipt.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-scoreall-bundle.v2",
                "status": "succeeded",
                "run_id": "fixture-score-all",
                "storage_uri": "fixture://score-all/",
                "global_strict_library": {
                    "path": "library/strict.csv",
                    "sha256": strict_sha256,
                    "bytes": strict_path.stat().st_size,
                },
                "runtime": {"registry_sha256": sha256_text("registry")},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    target_manifest_path = Path("config/targets/ampgent_six_target_sequence_manifest_20260824.json")
    pocket_catalog_path = Path("config/pockets/mvp_v2_pocket_catalog.yaml")
    cohort = freeze_structure_escalation_cohort(
        strict_library_path=strict_path,
        strict_library_sha256=strict_sha256,
        bundle_receipt_path=bundle_path,
        bundle_receipt_sha256=sha256_file(bundle_path),
        target_manifest_path=target_manifest_path,
        target_manifest_sha256=sha256_file(target_manifest_path),
        pocket_catalog_path=pocket_catalog_path,
        pocket_catalog_sha256=sha256_file(pocket_catalog_path),
        per_target_count=50,
    )

    assert cohort.selected_count == 300
    assert (
        len({item.sequence_sha256 for target in cohort.target_cohorts for item in target.selected})
        == 300
    )
    assert all(
        len({item.family_key_80_80 for item in target.selected}) == 50
        for target in cohort.target_cohorts
    )
    modes = {
        target.target_key: target.qualification.structure_evidence_mode
        for target in cohort.target_cohorts
    }
    assert modes == {
        "acea": "admitted_target_conditioned_relative_ranking",
        "gyra": "admitted_target_conditioned_relative_ranking",
        "pbp2a": "admitted_target_conditioned_relative_ranking",
        "vegfa": "exploratory_low_confidence_relative_ranking",
        "fgf2": "exploratory_low_confidence_relative_ranking",
        "angpt1": "exploratory_low_confidence_relative_ranking",
    }
    exploratory = [
        target
        for target in cohort.target_cohorts
        if target.qualification.structure_evidence_mode.startswith("exploratory")
    ]
    assert all(
        "target_interface_mapping_unqualified" in target.qualification.limitations
        for target in exploratory
    )
    assert cohort.no_binding_or_affinity_claim is True
    assert len(cohort.cohort_sha256) == 64
