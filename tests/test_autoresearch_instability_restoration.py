from __future__ import annotations

import copy

import pytest

from pepagent.autoresearch_instability_restoration import (
    RESTORATION_POLICY,
    RESTORATION_SCHEMA,
    canonical_manifest_bytes,
    successor_restored_candidate_ids,
    validate_restoration_manifest,
)
from pepagent.provenance.hashing import sha256_bytes


def _manifest() -> dict[str, object]:
    evaluation_ids = {
        name: f"00000000-0000-0000-0000-{index:012d}"
        for index, name in enumerate(
            (
                "amp_read_log10_mic_um",
                "guruprasad_instability_index",
                "hydrophobic_moment_eisenberg",
                "hydrophobic_ratio_modlamp",
                "llamp_log10_mic_um",
                "macrel_amp_probability",
                "macrel_hemolysis_label",
                "macrel_hemolysis_probability",
                "maximum_hydrophobic_run",
                "net_charge_ph7_4",
                "toxinpred3_hybrid_score",
                "toxinpred3_label",
            ),
            start=1,
        )
    }
    return {
        "schema_version": RESTORATION_SCHEMA,
        "policy": RESTORATION_POLICY,
        "snapshot_cutoff": "2026-08-29T04:30:00Z",
        "formal_metric_names": list(evaluation_ids),
        "decision_fields": {
            "primary": ["display_hard_gate_pass", "instability_score_qualified"],
            "guruprasad_instability_ood": "descriptive_audit_only",
            "ood_qualified": "deprecated_audit_alias_not_used",
        },
        "archive_membership_rewrite_count": 0,
        "summary": {"candidate_count": 1},
        "target_summary": {},
        "release_candidate_counts": {},
        "run_summary": [],
        "restored_candidates": [
            {
                "run_id": "10000000-0000-0000-0000-000000000001",
                "candidate_id": "20000000-0000-0000-0000-000000000001",
                "target_key": "pbp2a",
                "sequence_sha256": "a" * 64,
                "display_hard_gate_pass": True,
                "instability_score_qualified": True,
                "guruprasad_instability_index": 12.0,
                "guruprasad_instability_ood_audit": True,
                "evaluation_ids": evaluation_ids,
            }
        ],
    }


def test_manifest_uses_score_only_rule_and_is_canonical() -> None:
    manifest = _manifest()
    validate_restoration_manifest(manifest)
    payload = canonical_manifest_bytes(manifest)

    assert sha256_bytes(payload) == sha256_bytes(canonical_manifest_bytes(manifest))
    assert successor_restored_candidate_ids(manifest, target_key="PBP2A") == (
        "20000000-0000-0000-0000-000000000001",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("instability_score_qualified", False, "not score-qualified"),
        ("guruprasad_instability_index", 50.0, "fails the score-only"),
        ("guruprasad_instability_ood_audit", False, "not excluded by the old OOD"),
    ],
)
def test_manifest_fails_closed_on_nonrestorable_rows(
    field: str, value: object, message: str
) -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["restored_candidates"][0][field] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=message):
        validate_restoration_manifest(manifest)


def test_manifest_forbids_historical_archive_rewrite() -> None:
    manifest = _manifest()
    manifest["archive_membership_rewrite_count"] = 1

    with pytest.raises(ValueError, match="must not rewrite"):
        validate_restoration_manifest(manifest)
