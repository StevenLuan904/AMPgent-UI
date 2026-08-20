from __future__ import annotations

import json
from pathlib import Path

import yaml

from pepagent.enterprise_evidence_scheduler import plan_enterprise_evidence_actions
from pepagent.enterprise_model_registry import audit_model_assay_registry


def _audit(*domains: str) -> dict:
    return {
        "formal_science_run_authorized": not domains,
        "gaps": [f"{domain}:independent_models=0,required=1" for domain in domains],
    }


def _task(task_id: str, domain: str, *, group: int = 0, gpu: int = 0) -> dict:
    return {
        "task_id": task_id,
        "status": "ready",
        "priority_tier": group,
        "evidence_domains": [domain],
        "prerequisites": [],
        "estimated_cpu_minutes": 10,
        "estimated_gpu_minutes": gpu,
        "acceptance_artifacts": ["result_sha256"],
        "stop_conditions": ["stop_on_identity_drift"],
    }


def test_scheduler_is_gap_driven_and_deterministic() -> None:
    backlog = {
        "schema_version": "ampgent.enterprise-evidence-backlog.1",
        "tasks": [
            _task("later", "toxicity", group=1),
            _task("first", "toxicity", group=0),
            _task("irrelevant", "hemolysis", group=0),
        ],
    }
    plan = plan_enterprise_evidence_actions(
        model_registry_audit=_audit("toxicity"), backlog=backlog
    )
    assert plan.selected_task_ids == ("first", "later")
    assert plan.deferred_reasons["irrelevant"] == "does_not_cover_current_gap"


def test_scheduler_honors_dependencies_and_zero_gpu_budget() -> None:
    dependent = _task("calibrate", "hemolysis")
    dependent["prerequisites"] = ["license"]
    backlog = {
        "schema_version": "ampgent.enterprise-evidence-backlog.1",
        "tasks": [dependent, _task("gpu_task", "hemolysis", gpu=1)],
    }
    plan = plan_enterprise_evidence_actions(
        model_registry_audit=_audit("hemolysis"), backlog=backlog
    )
    assert plan.selected_task_ids == ()
    assert plan.deferred_reasons["calibrate"] == "waiting_for:license"
    assert plan.deferred_reasons["gpu_task"] == "gpu_budget"


def test_scheduler_keeps_in_progress_work_in_the_action_plan() -> None:
    active = _task("active", "novelty", group=1)
    active["status"] = "in_progress"
    backlog = {
        "schema_version": "ampgent.enterprise-evidence-backlog.1",
        "tasks": [active, _task("ready", "novelty", group=1)],
    }
    plan = plan_enterprise_evidence_actions(
        model_registry_audit=_audit("novelty"), backlog=backlog, maximum_actions=1
    )
    assert plan.selected_task_ids == ("active",)


def test_current_backlog_selects_three_high_information_cpu_tasks() -> None:
    root = Path(__file__).parents[1]
    backlog = yaml.safe_load(
        (root / "config/enterprise/ampgent_evidence_acquisition_backlog_v39.yaml").read_text(
            encoding="utf-8"
        )
    )
    registry = yaml.safe_load(
        (root / "config/enterprise/ampgent_model_assay_registry_v39.yaml").read_text(
            encoding="utf-8"
        )
    )
    contract = yaml.safe_load(
        (root / "config/enterprise/ampgent_core_pipeline_v39_audit.yaml").read_text(
            encoding="utf-8"
        )
    )
    audit = audit_model_assay_registry(
        registry=registry, enterprise_contract=contract
    ).to_dict()
    plan = plan_enterprise_evidence_actions(model_registry_audit=audit, backlog=backlog)
    assert plan.selected_task_ids == (
        "cytotoxicity_and_commensal_reference_panel_freeze",
        "pathogen_conditioned_activity_reference_freeze",
        "toxicity_second_family_qualification",
    )
    assert plan.estimated_gpu_minutes == 0
    assert plan.formal_run_submission_allowed is False
    pinned = json.loads(
        (root / "config/enterprise/ampgent_evidence_action_plan_v39.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(plan.selected_task_ids) == pinned["selected_task_ids"]
    assert plan.backlog_sha256 == pinned["backlog_sha256"]
    assert plan.estimated_cpu_minutes == pinned["estimated_cpu_minutes"]
    assert plan.estimated_gpu_minutes == pinned["estimated_gpu_minutes"]


def test_scheduler_authorizes_no_work_only_after_registry_ready() -> None:
    backlog = {
        "schema_version": "ampgent.enterprise-evidence-backlog.1",
        "tasks": [_task("task", "toxicity")],
    }
    plan = plan_enterprise_evidence_actions(model_registry_audit=_audit(), backlog=backlog)
    assert plan.selected_task_ids == ()
    assert plan.formal_run_submission_allowed is True
