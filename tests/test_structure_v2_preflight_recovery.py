from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

import pytest

from pepagent.autoresearch_structure_cohort import TARGET_KEYS
from pepagent.provenance.hashing import sha256_text
from pepagent.structure_v2_binding import PREFLIGHT_RECOVERY_REASON
from pepagent.structure_v2_preflight_recovery import (
    _recovery_branch,
    _recovery_candidate_metadata,
    preflight_recovery_reservation_key,
)

PREDECESSOR_KEY = "a" * 64
RECOVERY_KEY = "b" * 64


def _fixture() -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
    runs = []
    candidates = []
    for target_index, target_key in enumerate(TARGET_KEYS):
        run_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"old-run-{target_key}")
        workflow_spec = {"target_key": target_key, "target": {"sequence": "ACDE"}}
        runs.append(
            SimpleNamespace(
                id=run_id,
                target_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"target-{target_key}"),
                temporal_workflow_id=f"pepagent-structure-v2-{target_key}-old",
                temporal_run_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"temporal-{target_key}")),
                spec_json={
                    "target_key": target_key,
                    "workflow_spec": workflow_spec,
                    "structure_v2_reservation_key": PREDECESSOR_KEY,
                },
            )
        )
        for rank in range(1, 51):
            sequence = f"K{target_index:01X}{rank:02X}R"
            candidates.append(
                SimpleNamespace(
                    id=uuid.uuid5(run_id, f"candidate-{rank}"),
                    run_id=run_id,
                    sequence=sequence,
                    sequence_sha256=sha256_text(sequence),
                    generation=0,
                    proposal_rank=rank,
                    metadata_json={
                        "family_key_80_80": f"family-{target_key}-{rank:02d}",
                        "source_candidate_id": f"source-{target_key}-{rank:02d}",
                        "source_result_sha256": sha256_text(
                            f"source-result-{target_key}-{rank:02d}"
                        ),
                        "structure_v2_eligibility": {
                            "cohort_sha256": PREDECESSOR_KEY,
                        },
                    },
                )
            )
    return runs, candidates


def test_recovery_reservation_identity_is_deterministic_and_cohort_bound() -> None:
    runs, candidates = _fixture()

    first = preflight_recovery_reservation_key(PREDECESSOR_KEY, runs, candidates)
    second = preflight_recovery_reservation_key(
        PREDECESSOR_KEY,
        list(reversed(runs)),
        list(reversed(candidates)),
    )

    assert first == second
    changed = copy.deepcopy(candidates)
    changed[0].sequence_sha256 = "f" * 64
    assert preflight_recovery_reservation_key(PREDECESSOR_KEY, runs, changed) != first


def test_recovery_branch_has_new_deterministic_ids_and_explicit_lineage() -> None:
    runs, candidates = _fixture()
    predecessor = runs[0]
    rows = [row for row in candidates if row.run_id == predecessor.id]
    import_call = SimpleNamespace(id=uuid.uuid4())

    branch = _recovery_branch(
        recovery_key=RECOVERY_KEY,
        predecessor=predecessor,
        predecessor_candidates=rows,
        predecessor_import_call=import_call,
    )
    repeated = _recovery_branch(
        recovery_key=RECOVERY_KEY,
        predecessor=predecessor,
        predecessor_candidates=list(reversed(rows)),
        predecessor_import_call=import_call,
    )

    assert branch.run_id == repeated.run_id
    assert branch.workflow_id == repeated.workflow_id
    assert branch.run_id != predecessor.id
    assert branch.workflow_id != predecessor.temporal_workflow_id
    assert branch.run_spec["preflight_recovery"] == {
        "schema_version": "ampgent.structure-v2-preflight-recovery.1",
        "predecessor_reservation_key": PREDECESSOR_KEY,
        "predecessor_run_id": str(predecessor.id),
        "predecessor_workflow_id": predecessor.temporal_workflow_id,
        "predecessor_temporal_run_id": predecessor.temporal_run_id,
        "reason": PREFLIGHT_RECOVERY_REASON,
        "scientific_output_reused": False,
    }


def test_reissued_candidate_preserves_science_identity_but_not_row_identity() -> None:
    runs, candidates = _fixture()
    predecessor = runs[0]
    source = candidates[0]

    metadata = _recovery_candidate_metadata(
        recovery_key=RECOVERY_KEY,
        predecessor_run=predecessor,
        predecessor_candidate=source,
        rank=1,
    )

    assert metadata["family_key_80_80"] == source.metadata_json["family_key_80_80"]
    assert metadata["source_candidate_id"] == source.metadata_json["source_candidate_id"]
    assert metadata["structure_v2_eligibility"]["cohort_sha256"] == RECOVERY_KEY
    assert metadata["preflight_recovery"] == {
        "predecessor_run_id": str(predecessor.id),
        "predecessor_candidate_id": str(source.id),
        "reason": PREFLIGHT_RECOVERY_REASON,
        "scientific_output_reused": False,
    }
    assert source.metadata_json["structure_v2_eligibility"]["cohort_sha256"] == (
        PREDECESSOR_KEY
    )


def test_recovery_identity_rejects_partial_predecessor_cohort() -> None:
    runs, candidates = _fixture()

    with pytest.raises(ValueError, match="predecessor cohort differs"):
        preflight_recovery_reservation_key(PREDECESSOR_KEY, runs, candidates[:-1])
