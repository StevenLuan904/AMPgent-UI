from __future__ import annotations

import json

import pytest

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.workers import v37_activities
from pepagent.workflows.v37_champion import _compact_v37_pose, _compact_v37_rosetta


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_post_rosetta_projection_drops_large_runtime_payloads() -> None:
    coordinate = {
        "path": "predictions/complex_model_0.cif",
        "sha256": "a" * 64,
        "size_bytes": 250_000,
        "uri": "s3://pepagent/sha256/coordinate",
        "media_type": "chemical/x-mmcif",
    }
    pose = {
        "candidate": {
            "id": "candidate-1",
            "sequence": "KRLV",
            "sequence_sha256": "b" * 64,
            "large_sequence_evidence": "x" * 100_000,
        },
        "input": {"seed": 20270380},
        "tool_call_id": "pose-1",
        "boltz2": {"pair_iptm": 0.71, "raw_confidence": "x" * 100_000},
        "provenance": {
            "engine_artifacts": [coordinate, {"path": "large.npz", "blob": "x" * 100_000}]
        },
        "interface_audit": {
            "sample_audits": [
                {
                    "tool_call_id": "pose-1",
                    "seed": 20270380,
                    "pocket_coverage_fraction": 0.8,
                    "cross_chain_clash_count": 0,
                }
            ],
            "large_group_payload": "x" * 100_000,
        },
        "interface_audit_tool_call_id": "audit-1",
    }
    projected = _compact_v37_pose(pose)
    assert projected["coordinate_artifact"] == coordinate
    assert projected["interface_audit_sample"]["tool_call_id"] == "pose-1"
    assert "provenance" not in projected
    assert "large_sequence_evidence" not in projected["candidate"]
    assert "large_group_payload" not in projected
    assert len(_canonical_bytes(projected)) < 2_000

    rosetta = {
        "candidate": {"large": "x" * 100_000},
        "tool_call_id": "rosetta-1",
        "provenance": {
            "parent_tool_call_id": "pose-1",
            "large_environment": "x" * 100_000,
        },
        "rosetta": {
            "decoys": [
                {
                    "decoy_id": f"d-{index}",
                    "dG_separated": -10.0,
                    "peptide_bb_rmsd": 1.0,
                    "input_sha256": "c" * 64,
                    "output_sha256": "d" * 64,
                    "score_terms_sha256": "e" * 64,
                }
                for index in range(16)
            ],
            "large_engine_output": "x" * 100_000,
        },
    }
    compact_rosetta = _compact_v37_rosetta(rosetta)
    assert len(compact_rosetta["rosetta"]["decoys"]) == 16
    assert "large_environment" not in compact_rosetta["provenance"]
    assert "large_engine_output" not in compact_rosetta["rosetta"]


@pytest.mark.asyncio
async def test_structure_summary_reference_resolves_with_exact_byte_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"poses": [{"pose_id": "p1"}], "decoys": [{"decoy_id": "d1"}]}
    raw = _canonical_bytes(payload)

    class Store:
        def get_bytes(self, uri: str) -> bytes:
            assert uri == "s3://pepagent/sha256/summary"
            return raw

    monkeypatch.setattr(v37_activities, "ContentAddressedObjectStore", Store)
    reference = {
        "schema_version": v37_activities.V37_STRUCTURE_SUMMARY_REFERENCE_SCHEMA,
        "summary_sha256": sha256_json(payload),
        "artifact": {
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "uri": "s3://pepagent/sha256/summary",
            "media_type": "application/json",
        },
    }
    assert await v37_activities._resolve_v37_structure_summary_reference(reference) == payload

    reference["artifact"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="bytes drifted"):
        await v37_activities._resolve_v37_structure_summary_reference(reference)


def test_full_frozen_post_rosetta_command_stays_below_temporal_limit() -> None:
    pose = {
        "candidate": {"id": "c", "sequence": "KRLV", "sequence_sha256": "a" * 64},
        "input": {"seed": 20270380},
        "tool_call_id": "p",
        "boltz2": {"pair_iptm": 0.7},
        "coordinate_artifact": {
            "path": "complex_model_0.cif",
            "sha256": "b" * 64,
            "size_bytes": 300_000,
            "uri": "s3://pepagent/sha256/coordinate",
            "media_type": "chemical/x-mmcif",
        },
        "interface_audit_sample": {
            "tool_call_id": "p",
            "seed": 20270380,
            "pocket_coverage_fraction": 0.8,
            "cross_chain_clash_count": 0,
        },
        "interface_audit_tool_call_id": "a",
    }
    decoy = {
        "decoy_id": "d",
        "dG_separated": -10.0,
        "peptide_bb_rmsd": 1.0,
        "input_sha256": "c" * 64,
        "output_sha256": "d" * 64,
        "score_terms_sha256": "e" * 64,
    }
    request = {
        "candidate_ids": [f"candidate-{index}" for index in range(48)],
        "structures_by_candidate": {
            f"candidate-{candidate}": [
                {
                    **pose,
                    "candidate": {**pose["candidate"], "id": f"candidate-{candidate}"},
                    "tool_call_id": f"pose-{candidate}-{seed}",
                    "interface_audit_sample": {
                        **pose["interface_audit_sample"],
                        "tool_call_id": f"pose-{candidate}-{seed}",
                    },
                }
                for seed in range(3)
            ]
            for candidate in range(48)
        },
        "rosetta_results": [
            {
                "tool_call_id": f"rosetta-{candidate}-{seed}",
                "provenance": {"parent_tool_call_id": f"pose-{candidate}-{seed}"},
                "rosetta": {
                    "decoys": [
                        {**decoy, "decoy_id": f"d-{candidate}-{seed}-{i}"}
                        for i in range(16)
                    ]
                },
            }
            for candidate in range(48)
            for seed in range(3)
        ],
    }
    assert len(_canonical_bytes(request)) < 2_097_152


def test_final_closure_command_carries_compact_inputs_not_expanded_ledger() -> None:
    def receipt(activity_id: str) -> dict[str, object]:
        return {
            "schema_version": "v37.activity-transition-receipt.1",
            "activity_id": activity_id,
            "activity_type": "frozen-activity",
            "attempt": 1,
            "task_queue": "pepagent-frozen",
            "scheduled_at": "2026-08-16T00:00:00+00:00",
            "started_at": "2026-08-16T00:00:00+00:00",
            "finished_at": "2026-08-16T00:00:01+00:00",
            "schedule_to_start_seconds": 0.0,
        }

    candidate_ids = [f"candidate-{index:04d}" for index in range(900)]
    shortlisted = candidate_ids[:48]
    request = {
        "pipeline_occurrences": [
            {"proposal_ordinal": index, "occurrence_id": candidate_id}
            for index, candidate_id in enumerate(candidate_ids, start=1)
        ],
        "pipeline_transition_receipts": {
            "shortlisted_ids": shortlisted,
            "proposal": {
                candidate_id: receipt(f"proposal-{index}")
                for index, candidate_id in enumerate(candidate_ids)
            },
            "evaluation": [receipt(f"metric-{index}") for index in range(5)],
            "boltz": {
                candidate_id: [receipt(f"boltz-{candidate_id}-{seed}") for seed in range(3)]
                for candidate_id in shortlisted
            },
            "rosetta": {
                candidate_id: [
                    receipt(f"rosetta-{candidate_id}-{seed}") for seed in range(3)
                ]
                for candidate_id in shortlisted
            },
        },
        "structure_summary": {
            "schema_version": v37_activities.V37_STRUCTURE_SUMMARY_REFERENCE_SCHEMA,
            "summary_sha256": "a" * 64,
            "artifact": {
                "sha256": "b" * 64,
                "size_bytes": 1_000_000,
                "uri": "s3://pepagent/sha256/summary",
                "media_type": "application/json",
            },
        },
    }
    assert len(_canonical_bytes(request)) < 2_097_152
    assert "pipeline_queue_transition_ledger" not in request
