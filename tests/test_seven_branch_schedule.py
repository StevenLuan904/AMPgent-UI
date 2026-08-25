from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.seven_branch_design import SevenBranchDesignContract
from pepagent.seven_branch_reservation_cli import (
    _target_metadata,
    build_seven_branch_reservation_specs,
    build_seven_branch_top_up_reservation_specs,
)
from pepagent.seven_branch_schedule import (
    build_initial_seven_branch_schedule,
    build_top_up_seven_branch_schedule,
    derive_initial_seven_branch_run_ids,
    derive_top_up_seven_branch_run_ids,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _inputs() -> tuple[SevenBranchDesignContract, dict, str]:
    contract = SevenBranchDesignContract.model_validate_json(
        (REPO_ROOT / "config/workflows/ampgent_seven_branch_design_v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_path = REPO_ROOT / "config/targets/ampgent_six_target_sequence_manifest_20260824.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return contract, manifest, sha256_file(manifest_path)


def _template() -> dict:
    return {
        "knowledge_context_pack_sha256": "a" * 64,
        "refinement_provider": {
            "activity_name": "refine_v38_sequences_with_knowledge",
            "task_queue": "pepagent-refinement-provider-v38",
            "provider_task_id": "019fad3e-76b8-7e32-8455-d2e9b31d33e5",
            "release_revision": "provider-release-v1",
            "runtime_manifest_sha256": "b" * 64,
        },
        "task_queues": {
            "workflow_and_control": "pepagent-control-v38",
            "generator": "pepagent-generator-v38",
            "sequence_metrics": "pepagent-cpu-metrics-v38",
            "target_sequence": "pepagent-gpu-target-sequence-v39",
        },
        "generation_concurrency": 3,
        "metric_concurrency": 5,
    }


def _preflight(template: dict, contract: SevenBranchDesignContract) -> dict:
    return {
        "schema_version": "ampgent.seven-branch-submission-preflight.1",
        "status": "ready_to_submit_unique_run",
        "execution_authorized": True,
        "failed_gates": [],
        "formal_submission_key": "f" * 64,
        "request_template_sha256": sha256_json(template),
        "design_contract_sha256": contract.sha256(),
    }


def test_initial_schedule_freezes_seven_exact_once_child_runs() -> None:
    contract, manifest, manifest_sha = _inputs()
    template = _template()
    preflight = _preflight(template, contract)
    controller, children = derive_initial_seven_branch_run_ids(preflight)
    repeated_controller, repeated_children = derive_initial_seven_branch_run_ids(preflight)
    assert (controller, children) == (repeated_controller, repeated_children)
    schedule = build_initial_seven_branch_schedule(
        request_template=template,
        submission_preflight=preflight,
        design_contract=contract,
        target_manifest=manifest,
        target_manifest_sha256=manifest_sha,
        controller_run_id=controller,
        child_run_ids=children,
    )
    assert len(schedule.rounds) == 7
    assert (
        sum(
            item.request["execution_contract"]["expected_raw_occurrences"]
            for item in schedule.rounds
        )
        == 6600
    )
    assert len(schedule.target_runtime_by_key) == 6
    assert schedule.rounds[-1].request["seven_branch_round"]["branch_kind"] == ("target_agnostic")
    controller_spec, child_specs = build_seven_branch_reservation_specs(schedule)
    assert controller_spec["run_kind"] == "seven_branch_peptide_design_control"
    assert controller_spec["delivery_quota"] == 1900
    assert len(child_specs) == 7
    assert sum(item["expected_raw_occurrences"] for item in child_specs) == 6600
    assert len({item["formal_submission_key"] for item in child_specs}) == 7
    assert contract.model_dump(mode="json")["required_sequence_metrics"] == sorted(
        contract.required_sequence_metrics
    )
    assert schedule.sha256() == schedule.model_validate(schedule.model_dump(mode="json")).sha256()


def test_target_metadata_accepts_frozen_manifest_field_names() -> None:
    _, manifest, _ = _inputs()
    metadata = [
        _target_metadata(
            item,
            manifest_schema_version=manifest["schema_version"],
        )
        for item in manifest["targets"]
    ]

    assert len(metadata) == 6
    assert metadata[0]["source_kind"] == "public_canonical_supplement"
    assert all(isinstance(item["is_partial"], bool) for item in metadata)


def test_initial_schedule_rejects_template_or_manifest_drift() -> None:
    contract, manifest, manifest_sha = _inputs()
    template = _template()
    preflight = _preflight(template, contract)
    controller, children = derive_initial_seven_branch_run_ids(preflight)
    drifted_template = dict(template)
    drifted_template["run_id"] = "forbidden"
    with pytest.raises(ValueError, match="run-time identity"):
        build_initial_seven_branch_schedule(
            request_template=drifted_template,
            submission_preflight=preflight,
            design_contract=contract,
            target_manifest=manifest,
            target_manifest_sha256=manifest_sha,
            controller_run_id=controller,
            child_run_ids=children,
        )
    with pytest.raises(ValueError, match="manifest file identity"):
        build_initial_seven_branch_schedule(
            request_template=template,
            submission_preflight=preflight,
            design_contract=contract,
            target_manifest=manifest,
            target_manifest_sha256="0" * 64,
            controller_run_id=controller,
            child_run_ids=children,
        )


def test_top_up_schedule_derives_new_ids_and_empirical_budget() -> None:
    contract, manifest, manifest_sha = _inputs()
    template = _template()
    preflight = _preflight(template, contract)
    parent = UUID(int=700)
    evidence = {
        "acea": {
            "source_run_ids": [str(UUID(int=701))],
            "progress": {
                "branch_key": "acea",
                "raw_count": 600,
                "valid_unique_count": 515,
                "fully_scored_count": 515,
                "target_sequence_scored_count": 515,
                "qualified_count": 48,
                "delivered_count": 48,
                "family_count": 48,
            },
            "next_round_ordinal": 1,
            "snapshot_sha256": "e" * 64,
        }
    }
    controller, child_ids = derive_top_up_seven_branch_run_ids(
        parent_controller_run_id=parent,
        epoch_ordinal=1,
        branch_evidence_sha256_by_key={"acea": "e" * 64},
    )
    schedule = build_top_up_seven_branch_schedule(
        request_template=template,
        submission_preflight=preflight,
        design_contract=contract,
        target_manifest=manifest,
        target_manifest_sha256=manifest_sha,
        parent_controller_run_id=parent,
        controller_run_id=controller,
        epoch_ordinal=1,
        branch_evidence=evidence,
        child_run_ids_by_key=child_ids,
    )
    assert len(schedule.branches) == 1
    assert schedule.branches[0].top_up_plan.recommended_raw_budget == 2100
    assert (
        schedule.branches[0].frozen_round.request["execution_contract"]["expected_raw_occurrences"]
        == 2100
    )
    repeated = derive_top_up_seven_branch_run_ids(
        parent_controller_run_id=parent,
        epoch_ordinal=1,
        branch_evidence_sha256_by_key={"acea": "e" * 64},
    )
    assert repeated == (controller, child_ids)
    controller_spec, child_specs = build_seven_branch_top_up_reservation_specs(schedule)
    assert controller_spec["run_kind"] == ("seven_branch_peptide_design_top_up_control")
    assert controller_spec["parent_controller_run_id"] == str(parent)
    assert len(child_specs) == 1
    assert child_specs[0]["expected_raw_occurrences"] == 2100
    assert child_specs[0]["prior_source_run_ids"] == [str(UUID(int=701))]


def test_top_up_schedule_rejects_stale_preflight() -> None:
    contract, manifest, manifest_sha = _inputs()
    template = _template()
    preflight = _preflight(template, contract)
    parent = UUID(int=710)
    evidence = {
        "acea": {
            "source_run_ids": [str(UUID(int=711))],
            "progress": {
                "branch_key": "acea",
                "raw_count": 600,
                "valid_unique_count": 515,
                "fully_scored_count": 515,
                "target_sequence_scored_count": 515,
                "qualified_count": 48,
                "delivered_count": 48,
                "family_count": 48,
            },
            "next_round_ordinal": 1,
            "snapshot_sha256": "d" * 64,
        }
    }
    controller, child_ids = derive_top_up_seven_branch_run_ids(
        parent_controller_run_id=parent,
        epoch_ordinal=1,
        branch_evidence_sha256_by_key={"acea": "d" * 64},
    )
    stale = dict(preflight)
    stale["request_template_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="passed submission preflight"):
        build_top_up_seven_branch_schedule(
            request_template=template,
            submission_preflight=stale,
            design_contract=contract,
            target_manifest=manifest,
            target_manifest_sha256=manifest_sha,
            parent_controller_run_id=parent,
            controller_run_id=controller,
            epoch_ordinal=1,
            branch_evidence=evidence,
            child_run_ids_by_key=child_ids,
        )


def test_quality_top_up_schedule_continues_after_row_quota_is_complete() -> None:
    contract, manifest, manifest_sha = _inputs()
    template = _template()
    preflight = _preflight(template, contract)
    parent = UUID(int=720)
    evidence = {
        "acea": {
            "source_run_ids": [str(UUID(int=721))],
            "progress": {
                "branch_key": "acea",
                "raw_count": 600,
                "valid_unique_count": 515,
                "fully_scored_count": 515,
                "target_sequence_scored_count": 515,
                "qualified_count": 150,
                "delivered_count": 150,
                "family_count": 150,
            },
            "quality_progress": {
                "schema_version": "ampgent.seven-branch-quality-progress.1",
                "branch_key": "acea",
                "quality_quota": 150,
                "quality_qualified_count": 55,
                "archive_counts": {
                    "activity_consensus": 7,
                    "amp_read_endpoint": 15,
                    "llamp_endpoint": 15,
                    "macrel_endpoint": 16,
                    "activity_safety_balance": 4,
                    "stability_degradation": 35,
                    "novel_family": 150,
                    "model_disagreement": 4,
                },
                "underfilled_archives": [],
            },
            "next_round_ordinal": 2,
            "snapshot_sha256": "c" * 64,
        }
    }
    controller, child_ids = derive_top_up_seven_branch_run_ids(
        parent_controller_run_id=parent,
        epoch_ordinal=2,
        branch_evidence_sha256_by_key={"acea": "c" * 64},
    )
    schedule = build_top_up_seven_branch_schedule(
        request_template=template,
        submission_preflight=preflight,
        design_contract=contract,
        target_manifest=manifest,
        target_manifest_sha256=manifest_sha,
        parent_controller_run_id=parent,
        controller_run_id=controller,
        epoch_ordinal=2,
        branch_evidence=evidence,
        child_run_ids_by_key=child_ids,
    )
    epoch_branch = schedule.branches[0]
    assert schedule.schema_version == "ampgent.seven_branch_top_up_schedule.v2"
    assert epoch_branch.top_up_plan.action == "freeze_quality_successor_round"
    assert epoch_branch.top_up_plan.remaining_quality_count == 95
    assert epoch_branch.top_up_plan.recommended_raw_budget == 1800
    assert epoch_branch.frozen_round.request["quality_continuation"][
        "preserve_overlapping_archives"
    ] is True
    assert schedule.sha256() == schedule.model_validate(
        schedule.model_dump(mode="json")
    ).sha256()
