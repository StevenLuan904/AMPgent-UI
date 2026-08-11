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
    provider_governance: dict[str, Any]
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
        expected_knowledge_release = {
            "release_revision": DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.release_revision,
            "latest_sha256": DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.latest_sha256,
            "release_manifest_sha256": (
                DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.release_manifest_sha256
            ),
            "runtime_manifest_sha256": (
                DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.runtime_manifest_sha256
            ),
            "policy_selection_receipt_sha256": (
                DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.policy_selection_receipt_sha256
            ),
            "policy_roles_sha256": (
                DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.policy_roles_sha256
            ),
            "policy_record_content_sha256": (
                DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.policy_record_content_sha256
            ),
            "policy_specification_sha256": (
                DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT.policy_specification_sha256
            ),
        }
        if any(knowledge.get(key) != value for key, value in expected_knowledge_release.items()):
            raise ValueError("v34 knowledge provider release drifted")
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
        expected_pepshot_release = {
            "normalized_source_revision": DEFAULT_PEPSHOT_ADAPTER_CONTRACT.source_revision,
            "release_id": DEFAULT_PEPSHOT_ADAPTER_CONTRACT.release_id,
            "latest_sha256": DEFAULT_PEPSHOT_ADAPTER_CONTRACT.latest_sha256,
            "release_manifest_sha256": (
                DEFAULT_PEPSHOT_ADAPTER_CONTRACT.release_manifest_sha256
            ),
            "runtime_manifest_sha256": (
                DEFAULT_PEPSHOT_ADAPTER_CONTRACT.runtime_manifest_sha256
            ),
            "fixed_fixture_bundle_id": (
                DEFAULT_PEPSHOT_ADAPTER_CONTRACT.fixture_bundle_id
            ),
        }
        if any(pepshot.get(key) != value for key, value in expected_pepshot_release.items()):
            raise ValueError("v34 PepShot provider release drifted")
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

        governance = self.provider_governance
        expected_owners = {
            "knowledge": {
                "task_id": "019fad3e-76b8-7e32-8455-d2e9b31d33e5",
                "frozen_release_identity": knowledge["release_revision"],
            },
            "pepshot": {
                "task_id": "019fb910-f2dd-7be1-a7e6-bfe381512c25",
                "frozen_release_identity": pepshot["release_id"],
            },
        }
        if governance.get("provider_owner_tasks") != expected_owners:
            raise ValueError("v34 provider ownership or frozen release drifted")
        expected_triggers = {
            "contract_violation",
            "runtime_or_renderer_failure",
            "evidence_incompleteness",
            "schema_or_semantic_inadequacy",
            "scientific_review_inadequacy",
        }
        if set(governance.get("trigger_categories", [])) != expected_triggers:
            raise ValueError("v34 provider escalation trigger set drifted")
        consumer_policy = governance.get("consumer_policy", {})
        required_consumer_guards = (
            "provider_owned_fix_required",
            "AMPgent_compatibility_adaptation_forbidden",
            "active_formal_run_release_hot_swap_forbidden",
            "rejected_release_cannot_be_reaccepted_without_new_immutable_release",
            "replacement_requires_read_only_acceptance",
        )
        if not all(consumer_policy.get(key) is True for key in required_consumer_guards):
            raise ValueError("v34 provider consumer policy is incomplete")
        expected_request_fields = {
            "request_id",
            "provider",
            "owner_task_id",
            "rejecting_run_id",
            "change_request_run_id",
            "rejected_release_identity",
            "trigger_category",
            "reproducible_input_artifact_sha256",
            "violated_contract_artifact_sha256",
            "acceptance_criteria_artifact_sha256",
            "external_request_receipt_artifact_sha256",
            "lifecycle_state",
            "consumer_adaptation_performed",
        }
        if set(governance.get("change_request_required_fields", [])) != (
            expected_request_fields
        ):
            raise ValueError("v34 provider change-request field contract drifted")
        expected_replacement_fields = {
            "replacement_release_identity",
            "replacement_release_manifest_sha256",
            "read_only_acceptance_receipt_artifact_sha256",
        }
        if set(governance.get("replacement_required_fields", [])) != (
            expected_replacement_fields
        ):
            raise ValueError("v34 provider replacement field contract drifted")
        if governance.get("lifecycle_states") != [
            "change_request_sent",
            "replacement_release_received",
            "read_only_reaccepted",
        ]:
            raise ValueError("v34 provider change-request lifecycle drifted")
        if governance.get("database_parentage") != (
            "provider_change_request_child_run_of_rejecting_run"
        ):
            raise ValueError("v34 provider change request must be a linked child run")
        if governance.get("no_change_path_requires_explicit_empty_ledger") is not True:
            raise ValueError("v34 provider no-change path must remain explicit")

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
            "persist_provider_ownership_release_freeze_and_change_request_ledger",
            "persist_provider_rejection_request_replacement_and_reacceptance_receipts",
            "provider_change_requests_are_child_runs_linked_to_rejecting_run",
            "active_formal_run_provider_release_hot_swap_forbidden",
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
