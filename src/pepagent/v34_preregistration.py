from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

from pepagent.provenance.hashing import sha256_json
from pepagent.v34_external_adapters import (
    DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT,
    DEFAULT_PEPSHOT_ADAPTER_CONTRACT,
)


class FactorialArm(BaseModel):
    name: Literal["baseline", "cards_only", "pepshot_only", "cards_and_pepshot"]
    verified_knowledge_context: Literal["off", "on"]
    PepShot_review: Literal["off", "on"]


class Endpoint(BaseModel):
    name: str
    direction: Literal["maximize", "minimize"]
    family: str | None = None


class FormalRun(BaseModel):
    execution_authorized: bool
    submitted: bool
    run_id: str | None
    workflow_id: str | None
    implementation_revision: str | None


class V34Preregistration(BaseModel):
    benchmark_id: Literal["amp_knowledge_pepshot_ablation_v34"]
    version: str
    execution_status: Literal["preregistered_draft_not_authorized"]
    scientific_question: dict[str, Any]
    parent_cohort: dict[str, Any]
    knowledge_provider: dict[str, Any]
    pepshot_provider: dict[str, Any]
    factorial_design: dict[str, Any]
    budget_contract: dict[str, Any]
    intervention_contract: dict[str, Any]
    independent_evaluation: dict[str, Any]
    analysis_contract: dict[str, Any]
    database_evidence_contract: dict[str, Any]
    scientific_boundaries: dict[str, Any]
    formal_run: FormalRun

    @model_validator(mode="after")
    def validate_contract(self) -> V34Preregistration:
        arms = [FactorialArm.model_validate(item) for item in self.factorial_design["arms"]]
        observed = {
            (arm.name, arm.verified_knowledge_context, arm.PepShot_review) for arm in arms
        }
        expected = {
            ("baseline", "off", "off"),
            ("cards_only", "on", "off"),
            ("pepshot_only", "off", "on"),
            ("cards_and_pepshot", "on", "on"),
        }
        if observed != expected or len(arms) != 4:
            raise ValueError("v34 requires the exact knowledge by PepShot 2x2 design")
        if self.factorial_design.get("repeated_block") != "every_parent_runs_all_four_arms":
            raise ValueError("every v34 parent must run all four arms")
        if not self.factorial_design.get("cross_arm_memory_forbidden"):
            raise ValueError("cross-arm memory would contaminate the factorial comparison")
        if not self.factorial_design.get("assignment_reveal_after_locked_adjudication"):
            raise ValueError("arm identities must remain blinded until adjudication is locked")

        cohort = self.parent_cohort
        if cohort.get("selection_rule") != "all_24_v32_portfolio_members_in_database_replay_order":
            raise ValueError("v34 parent selection drifted")
        if cohort.get("expected_parent_count") != 24:
            raise ValueError("v34 must use all 24 frozen v32 portfolio parents")
        if cohort.get("identity_status") != "frozen_from_database_replay":
            raise ValueError("v34 parent identities must freeze from database replay")
        members = cohort.get("members", [])
        if len(members) != 24:
            raise ValueError("v34 parent manifest must contain exactly 24 members")
        if [item.get("order") for item in members] != list(range(1, 25)):
            raise ValueError("v34 parent manifest order drifted")
        candidate_ids = [item.get("candidate_id") for item in members]
        sequence_hashes = [item.get("sequence_sha256") for item in members]
        if len(set(candidate_ids)) != 24 or len(set(sequence_hashes)) != 24:
            raise ValueError("v34 parent identities and sequences must be unique")
        if not all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in sequence_hashes
        ):
            raise ValueError("v34 parent sequence SHA is invalid")
        if cohort.get("member_manifest_sha256") != sha256_json(members):
            raise ValueError("v34 parent manifest SHA mismatch")
        if not cohort.get("v32_backwrite_forbidden"):
            raise ValueError("v34 cannot backwrite v32")

        knowledge = self.knowledge_provider
        if knowledge.get("system_id") != "amp-system-kb":
            raise ValueError("v34 knowledge provider identity drifted")
        if knowledge.get("context_pack_schema_sha256") != (
            DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.context_schema_sha256
        ) or knowledge.get("active_policy_sha256") != (
            DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.active_policy_sha256
        ):
            raise ValueError("v34 knowledge provider contract hash drifted")
        if set(knowledge.get("required_pack_fields", [])) != set(
            DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.required_pack_fields
        ):
            raise ValueError("v34 knowledge context fields drifted")
        admission = knowledge.get("admission", {})
        required_knowledge_guards = (
            "D4_excluded",
            "missing_matches_return_explicit_empty_pack",
            "broken_database_policy_schema_or_evidence_reference_fails_closed",
        )
        if not all(admission.get(key) is True for key in required_knowledge_guards):
            raise ValueError("v34 knowledge admission guards are incomplete")
        if not knowledge.get("online_condensation_model_forbidden"):
            raise ValueError("online knowledge condensation is forbidden in v34")

        pepshot = self.pepshot_provider
        expected_pepshot_hashes = (
            DEFAULT_PEPSHOT_ADAPTER_CONTRACT.contract_sha256,
            DEFAULT_PEPSHOT_ADAPTER_CONTRACT.request_schema_sha256,
            DEFAULT_PEPSHOT_ADAPTER_CONTRACT.review_schema_sha256,
        )
        observed_pepshot_hashes = (
            pepshot.get("contract_sha256"),
            pepshot.get("agent_request_schema_sha256"),
            pepshot.get("review_schema_sha256"),
        )
        if observed_pepshot_hashes != expected_pepshot_hashes:
            raise ValueError("v34 PepShot provider contract hash drifted")
        if pepshot.get("maximum_priority_labeled_views") != (
            DEFAULT_PEPSHOT_ADAPTER_CONTRACT.maximum_priority_labeled_views
        ):
            raise ValueError("v34 PepShot priority-view budget drifted")
        if pepshot.get("required_route") != (
            "verify_then_read_all_requested_images_then_validate_review"
        ):
            raise ValueError("v34 must use the verified PepShot review route")
        if "claim_binding_or_affinity" not in pepshot.get("forbidden_decision_effects", []):
            raise ValueError("PepShot cannot become binding or affinity evidence")

        budget = self.budget_contract
        required_budget_guards = (
            "revision_replaces_not_adds_to_budget",
            "fixed_full_budget_required",
            "adaptive_early_stopping_forbidden",
            "equal_tool_independent_compute_budget_across_arms",
            "tool_specific_cost_reported_separately",
        )
        if not all(budget.get(key) is True for key in required_budget_guards):
            raise ValueError("v34 equal-budget contract is incomplete")
        if budget.get("missing_policy") != "retain_shortfall_no_refill":
            raise ValueError("v34 must not refill arm shortfalls")

        primary = [
            Endpoint.model_validate(item)
            for item in self.independent_evaluation["primary_endpoints"]
        ]
        expected_primary = {
            "confirmed_novel_nondominated_yield_per_parent",
            "structural_conflict_interception_recall",
            "invalid_or_unsupported_edit_rate",
        }
        if {item.name for item in primary} != expected_primary:
            raise ValueError("v34 independent primary endpoints drifted")
        evaluation = self.independent_evaluation
        if not evaluation.get("evaluation_lane_blinded_to_arm_labels"):
            raise ValueError("v34 evaluation must be blinded")
        if not evaluation.get("intervention_outputs_cannot_be_sole_validation_endpoint"):
            raise ValueError("tool outputs cannot validate themselves")
        if not evaluation.get("weighted_total_score_forbidden"):
            raise ValueError("weighted totals are forbidden")

        expected_contrasts = {
            "knowledge_main_effect",
            "PepShot_main_effect",
            "knowledge_by_PepShot_interaction",
            "cards_only_vs_baseline",
            "pepshot_only_vs_baseline",
            "cards_and_pepshot_vs_baseline",
        }
        if set(self.analysis_contract.get("required_contrasts", [])) != expected_contrasts:
            raise ValueError("v34 factorial contrasts are incomplete")
        promotion = self.analysis_contract.get("promotion_rule", {})
        if promotion.get("current_margin_status") != "frozen_before_execution":
            raise ValueError("v34 practical margins must be frozen before execution")
        margins = promotion.get("endpoint_margins", {})
        if set(margins) != expected_primary:
            raise ValueError("v34 practical margins must cover every primary endpoint")
        for endpoint_name, margin in margins.items():
            improvement = margin.get("improvement")
            degradation = margin.get("maximum_allowed_degradation")
            if not isinstance(improvement, int | float) or improvement <= 0:
                raise ValueError(f"invalid improvement margin for {endpoint_name}")
            if not isinstance(degradation, int | float) or degradation < 0:
                raise ValueError(f"invalid degradation margin for {endpoint_name}")

        evidence = self.database_evidence_contract
        required_evidence = (
            "PostgreSQL_is_authoritative",
            "object_store_is_content_addressed",
            "persist_factorial_assignment_and_opaque_labels",
            "persist_parent_and_all_proposal_occurrences",
            "persist_context_query_pack_trace_cards_passages_and_policy",
            "persist_PepShot_request_bundle_audit_images_review_and_validation",
            "persist_prompts_responses_adoptions_rejections_and_revisions",
            "persist_all_ToolCalls_dependencies_Evaluations_and_AgentDecisions",
            "persist_blinded_adjudication_before_assignment_reveal",
            "persist_cost_and_failure_events",
            "database_object_store_only_replay_required",
            "CSV_and_Markdown_are_exports_only",
        )
        if not all(evidence.get(key) is True for key in required_evidence):
            raise ValueError("v34 database evidence contract is incomplete")

        boundaries = self.scientific_boundaries
        if boundaries.get("PepMLM_used") or boundaries.get("AMPlify_used"):
            raise ValueError("PepMLM and AMPlify are forbidden in v34")
        if boundaries.get("weighted_total_used") is not False:
            raise ValueError("v34 cannot use a weighted total")
        required_boundaries = (
            "predictions_are_not_experiments",
            "no_experimental_activity_claim",
            "no_experimental_safety_claim",
            "no_AceA_binding_or_affinity_claim",
            "PepShot_is_structural_review_not_binding_evidence",
            "knowledge_cards_are_attributed_advice_not_ground_truth",
            "v22_through_v33_backwrite_forbidden",
        )
        if not all(boundaries.get(key) is True for key in required_boundaries):
            raise ValueError("v34 scientific boundaries are incomplete")

        if self.formal_run.execution_authorized or self.formal_run.submitted:
            raise ValueError("v34 draft is not authorized for execution")
        if self.formal_run.run_id is not None or self.formal_run.workflow_id is not None:
            raise ValueError("unsubmitted v34 draft cannot carry run/workflow identities")
        revision = self.formal_run.implementation_revision
        if revision is not None and (
            len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            raise ValueError("v34 implementation revision must be an exact git SHA")
        return self


def load_v34_preregistration(path: Path) -> V34Preregistration:
    return V34Preregistration.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
