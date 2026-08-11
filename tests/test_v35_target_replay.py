from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_bytes
from pepagent.v35_multitarget import (
    SCHEMA_VERSION,
    TargetAuditItem,
    TargetQualificationSnapshot,
    build_selection_witness,
    canonical_json_bytes,
    deterministic_panel_selection,
    verify_target_qualification_snapshot,
)


def _artifact(payloads: dict[str, bytes], payload: bytes | dict) -> str:
    raw = canonical_json_bytes(payload) if isinstance(payload, dict) else payload
    digest = sha256_bytes(raw)
    payloads[digest] = raw
    return digest


def _target(
    order: int,
    payloads: dict[str, bytes],
    *,
    qualified: bool,
    vector: list[float] | None,
) -> dict:
    key = f"target-{order:02d}"
    sequence_sha = _artifact(payloads, f"SEQUENCE{order}".encode())
    row = {
        "shortlist_order": order,
        "target_key": key,
        "organism_and_strain": f"Bacterium strain {order}",
        "sequence_accession": f"P{order:05d}",
        "sequence_entry_version": "1",
        "sequence_admission_basis": "UniProtKB_reviewed",
        "sequence_sha256": sequence_sha,
        "sequence_artifact_sha256": sequence_sha,
        "source_manifest_sha256": _artifact(payloads, {"target": key, "source": "fixture"}),
        "feature_evidence_sha256": _artifact(payloads, {"target": key, "features": []}),
        "structure_source_type": (
            "experimental_exact_target" if qualified else "predicted_hypothesis_only"
        ),
        "coordinate_sha256": None,
        "structure_validation_sha256": None,
        "sequence_structure_mapping_sha256": None,
        "primary_pocket_grade": "A" if qualified else "C",
        "primary_pocket_definition_sha256": None,
        "wrong_pocket_definition_sha256": None,
        "audit_status": "qualified_primary" if qualified else "rejected",
        "rejection_reasons": [] if qualified else ["pocket_evidence_below_primary_grade"],
        "diversity_vector": vector,
    }
    if qualified:
        row.update(
            {
                "coordinate_sha256": _artifact(payloads, f"COORDINATES{order}".encode()),
                "structure_validation_sha256": _artifact(
                    payloads, {"target": key, "local_validation": "acceptable"}
                ),
                "sequence_structure_mapping_sha256": _artifact(
                    payloads, {"target": key, "mapping": "exact"}
                ),
                "primary_pocket_definition_sha256": _artifact(
                    payloads, {"target": key, "pocket": "native"}
                ),
                "wrong_pocket_definition_sha256": _artifact(
                    payloads, {"target": key, "pocket": "wrong"}
                ),
            }
        )
    return row


def _fixture() -> tuple[dict, dict[str, bytes]]:
    payloads: dict[str, bytes] = {}
    vectors = [
        [1.0, 0.0],
        [0.0, 1.0],
        [-1.0, 0.0],
        [0.0, -1.0],
        [0.7, 0.7],
        [-0.7, -0.7],
    ]
    shortlist = [
        _target(index, payloads, qualified=True, vector=vector)
        for index, vector in enumerate(vectors, start=1)
    ]
    shortlist.extend(
        [
            _target(7, payloads, qualified=False, vector=None),
            _target(8, payloads, qualified=False, vector=None),
        ]
    )
    provisional = {
        "schema_version": SCHEMA_VERSION,
        "audit_scope_id": "v35-synthetic-target-ledger",
        "target_names_selected_before_audit": False,
        "peptide_or_structure_outcomes_used_for_selection": False,
        "target_agnostic_amp_lane_retained": True,
        "acea_anchor_vector": [0.0, 0.0],
        "requested_new_target_count": 3,
        "shortlist": shortlist,
        "selected_target_keys": [],
        "selection_method": "hard_gate_then_anchor_aware_maximin_v1",
        "selection_witness_sha256": "0" * 64,
    }
    snapshot = TargetQualificationSnapshot.model_validate(
        {
            **provisional,
            "selected_target_keys": deterministic_panel_selection(
                [TargetAuditItem.model_validate(row) for row in shortlist],
                acea_anchor_vector=[0.0, 0.0],
                panel_size=3,
            ),
        }
    )
    witness = build_selection_witness(snapshot)
    witness_sha = _artifact(payloads, witness)
    payload = snapshot.model_dump(mode="json")
    payload["selection_witness_sha256"] = witness_sha
    return payload, payloads


def test_v35_target_qualification_replays_complete_failure_denominator() -> None:
    snapshot, payloads = _fixture()
    receipt = verify_target_qualification_snapshot(snapshot, payloads)
    assert receipt["exact_replay"] is True
    assert receipt["shortlist_count"] == 8
    assert receipt["qualified_count"] == 6
    assert receipt["rejected_count"] == 2
    assert receipt["selected_target_keys"] == ["target-01", "target-02", "target-03"]
    assert receipt["candidate_count"] == 0
    assert receipt["generalization_evaluated"] is False


def test_v35_target_qualification_rejects_cherry_picked_or_underpowered_ledger() -> None:
    snapshot, payloads = _fixture()
    snapshot["shortlist"].pop()
    with pytest.raises(ValueError, match="at least 8 items"):
        verify_target_qualification_snapshot(snapshot, payloads)

    snapshot, payloads = _fixture()
    snapshot["selected_target_keys"] = list(reversed(snapshot["selected_target_keys"]))
    with pytest.raises(ValueError, match="differs from deterministic"):
        verify_target_qualification_snapshot(snapshot, payloads)


def test_v35_target_qualification_rejects_forbidden_outcome_or_weak_primary_pocket() -> None:
    snapshot, payloads = _fixture()
    snapshot["shortlist"][0]["rosetta_score"] = -100.0
    with pytest.raises(ValueError, match="forbidden peptide/tool outcome"):
        verify_target_qualification_snapshot(snapshot, payloads)

    snapshot, payloads = _fixture()
    snapshot["shortlist"][0]["primary_pocket_grade"] = "C"
    with pytest.raises(ValueError, match="grade A or B"):
        verify_target_qualification_snapshot(snapshot, payloads)


def test_v35_target_qualification_rejects_artifact_or_witness_drift() -> None:
    snapshot, payloads = _fixture()
    sequence_sha = snapshot["shortlist"][0]["sequence_artifact_sha256"]
    payloads[sequence_sha] += b"drift"
    with pytest.raises(ValueError, match="sequence artifact missing or corrupt"):
        verify_target_qualification_snapshot(snapshot, payloads)

    snapshot, payloads = _fixture()
    witness_sha = snapshot["selection_witness_sha256"]
    witness = json.loads(payloads.pop(witness_sha))
    witness["selected_target_keys"] = list(reversed(witness["selected_target_keys"]))
    drifted = canonical_json_bytes(witness)
    drifted_sha = sha256_bytes(drifted)
    payloads[drifted_sha] = drifted
    snapshot["selection_witness_sha256"] = drifted_sha
    with pytest.raises(ValueError, match="witness differs from replay"):
        verify_target_qualification_snapshot(snapshot, payloads)


def test_v35_target_snapshot_is_strict_and_source_file_has_no_target_names() -> None:
    snapshot, _ = _fixture()
    drifted = copy.deepcopy(snapshot)
    drifted["unexpected"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        TargetQualificationSnapshot.model_validate(drifted)

    framework = (
        Path(__file__).parents[1]
        / "config"
        / "benchmarks"
        / "amp_multitarget_qualification_v35.yaml"
    ).read_text(encoding="utf-8")
    assert "target_names_selected: false" in framework
    assert "target_selection_authorized: false" in framework
