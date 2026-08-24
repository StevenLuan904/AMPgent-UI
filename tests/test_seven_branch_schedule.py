from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.seven_branch_design import SevenBranchDesignContract
from pepagent.seven_branch_reservation_cli import build_seven_branch_reservation_specs
from pepagent.seven_branch_schedule import (
    build_initial_seven_branch_schedule,
    derive_initial_seven_branch_run_ids,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _inputs() -> tuple[SevenBranchDesignContract, dict, str]:
    contract = SevenBranchDesignContract.model_validate_json(
        (REPO_ROOT / "config/workflows/ampgent_seven_branch_design_v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_path = (
        REPO_ROOT
        / "config/targets/ampgent_six_target_sequence_manifest_20260824.json"
    )
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
    repeated_controller, repeated_children = derive_initial_seven_branch_run_ids(
        preflight
    )
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
    assert sum(
        item.request["execution_contract"]["expected_raw_occurrences"]
        for item in schedule.rounds
    ) == 6600
    assert len(schedule.target_runtime_by_key) == 6
    assert schedule.rounds[-1].request["seven_branch_round"]["branch_kind"] == (
        "target_agnostic"
    )
    controller_spec, child_specs = build_seven_branch_reservation_specs(schedule)
    assert controller_spec["run_kind"] == "seven_branch_peptide_design_control"
    assert controller_spec["delivery_quota"] == 1900
    assert len(child_specs) == 7
    assert sum(item["expected_raw_occurrences"] for item in child_specs) == 6600
    assert len({item["formal_submission_key"] for item in child_specs}) == 7
    assert contract.model_dump(mode="json")["required_sequence_metrics"] == sorted(
        contract.required_sequence_metrics
    )
    assert schedule.sha256() == schedule.model_validate(
        schedule.model_dump(mode="json")
    ).sha256()


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
