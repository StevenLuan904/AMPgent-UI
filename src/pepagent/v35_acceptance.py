from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_bytes


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParentContract(StrictContractModel):
    path: Literal["amp_multitarget_qualification_v35.yaml"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MigrationContract(StrictContractModel):
    path: Literal["migrations/versions/0011_target_qualification_lineage.py"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    from_revision: Literal["0010_harness_evolution_lineage"]
    to_revision: Literal["0011_target_qualification_lineage"]
    deploy_to_shared_PostgreSQL_only_after_authorization: Literal[True]
    transactional_upgrade_and_schema_introspection_required: Literal[True]
    downgrade_is_not_part_of_acceptance: Literal[True]


class AuthorizationContract(StrictContractModel):
    execution_authorized: Literal[False]
    submitted: Literal[False]
    run_id: None
    explicit_user_phrase_required: Literal[
        "授权 v35a 靶点资格合成数据库闭环验收"
    ]
    authorization_cannot_be_inferred_from_roadmap_work: Literal[True]


class DataBoundary(StrictContractModel):
    synthetic_only: Literal[True]
    synthetic_scope_id: Literal["v35a-synthetic-target-qualification-closure"]
    real_target_names_or_accessions_forbidden: Literal[True]
    external_target_web_or_database_audit_forbidden: Literal[True]
    historical_candidate_or_evaluation_reads_forbidden: Literal[True]
    candidate_generation_forbidden: Literal[True]
    candidate_count: Literal[0]
    evaluation_count: Literal[0]
    real_target_selection_forbidden: Literal[True]
    panel_execution_forbidden: Literal[True]
    frozen_runs_may_be_mutated: Literal[False]


class SyntheticScenario(StrictContractModel):
    shortlist_count: Literal[8]
    qualified_primary_count: Literal[6]
    rejected_count: Literal[2]
    requested_panel_size: Literal[3]
    primary_pocket_grade_distribution: dict[str, int]
    rejected_pocket_grade_distribution: dict[str, int]
    target_audit_run_count: Literal[8]
    selection_run_count: Literal[1]
    audit_tool_call_count: Literal[8]
    selection_tool_call_count: Literal[1]
    negative_probe_tool_call_count: Literal[7]
    audit_agent_decision_count: Literal[8]
    selection_agent_decision_count: Literal[1]
    selection_depends_on_all_audit_calls: Literal[True]
    exact_selection_method: Literal["hard_gate_then_anchor_aware_maximin_v1"]
    target_names_selected_before_audit: Literal[False]
    peptide_or_structure_outcomes_used_for_selection: Literal[False]
    target_agnostic_amp_lane_retained: Literal[True]

    @model_validator(mode="after")
    def validate_scenario(self) -> SyntheticScenario:
        if self.qualified_primary_count + self.rejected_count != self.shortlist_count:
            raise ValueError("v35a synthetic shortlist denominator drifted")
        if self.primary_pocket_grade_distribution != {"A": 3, "B": 3}:
            raise ValueError("v35a qualified pocket-grade distribution drifted")
        if self.rejected_pocket_grade_distribution != {"C": 1, "D": 1}:
            raise ValueError("v35a rejected pocket-grade distribution drifted")
        return self


class ExpectedTypedCounts(StrictContractModel):
    synthetic_target_count: Literal[9]
    target_pocket_count: Literal[12]
    experiment_run_count: Literal[9]
    tool_call_count: Literal[16]
    tool_call_dependency_count: Literal[8]
    agent_decision_count: Literal[9]
    agent_decision_tool_call_edge_count: Literal[9]
    target_qualification_audit_count: Literal[8]
    target_panel_selection_witness_count: Literal[1]
    target_panel_selection_member_count: Literal[3]
    candidate_count: Literal[0]
    evaluation_count: Literal[0]
    artifact_count: Literal[109]
    evidence_artifact_edge_count: Literal[86]
    lifecycle_event_count: Literal[43]


class DatabaseEvidenceContract(StrictContractModel):
    PostgreSQL_is_authoritative: Literal[True]
    object_store_is_content_addressed: Literal[True]
    use_retry_safe_repository_primitives: Literal[True]
    persist_all_success_and_failed_probe_ToolCalls: Literal[True]
    persist_all_AgentDecision_ToolCall_edges: Literal[True]
    persist_all_target_audit_artifact_foreign_keys: Literal[True]
    persist_selection_dependency_fan_in: Literal[True]
    persist_anchor_witness_snapshot_replay_and_acceptance_artifacts: Literal[True]
    database_object_store_only_replay_required: Literal[True]
    replay_must_reconstruct_complete_failure_denominator: Literal[True]
    replay_must_reconstruct_exact_selected_order: Literal[True]
    replay_must_reject_every_negative_probe: Literal[True]
    CSV_JSON_and_Markdown_are_exports_only: Literal[True]
    required_artifact_roles: list[str]

    @model_validator(mode="after")
    def validate_artifact_roles(self) -> DatabaseEvidenceContract:
        if self.required_artifact_roles != [
            "target_sequence",
            "target_source_manifest",
            "target_feature_evidence",
            "target_structure_coordinates",
            "target_structure_validation",
            "target_sequence_structure_mapping",
            "target_primary_pocket_definition",
            "target_wrong_pocket_definition",
            "ToolCall_input",
            "ToolCall_output_or_error",
            "AgentDecision_prompt",
            "AgentDecision_response",
            "AceA_anchor",
            "target_panel_selection_witness",
            "target_qualification_snapshot",
            "database_only_replay_bundle",
            "aggregate_acceptance_receipt",
        ]:
            raise ValueError("v35a required artifact roles drifted")
        return self


class AcceptanceVerdicts(StrictContractModel):
    allowed: list[str]
    success_does_not_authorize_real_target_audit: Literal[True]
    success_does_not_authorize_real_target_selection: Literal[True]
    success_does_not_authorize_candidate_generation: Literal[True]
    success_does_not_prove_multitarget_generalization: Literal[True]

    @model_validator(mode="after")
    def validate_verdicts(self) -> AcceptanceVerdicts:
        if self.allowed != [
            "synthetic_target_qualification_database_closure_accepted",
            "synthetic_target_qualification_database_closure_rejected",
        ]:
            raise ValueError("v35a allowed verdict set drifted")
        return self


class V35AAcceptanceContract(StrictContractModel):
    benchmark_id: Literal["amp_target_qualification_synthetic_acceptance_v35a"]
    version: Literal["v35a.0.1-preregistered-synthetic-database-acceptance"]
    execution_status: Literal["preregistered_not_authorized"]
    implementation_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    parent_contract: ParentContract
    migration: MigrationContract
    authorization: AuthorizationContract
    data_boundary: DataBoundary
    synthetic_scenario: SyntheticScenario
    negative_probes: list[str]
    expected_typed_counts: ExpectedTypedCounts
    database_evidence_contract: DatabaseEvidenceContract
    acceptance_verdicts: AcceptanceVerdicts

    @model_validator(mode="after")
    def validate_acceptance_contract(self) -> V35AAcceptanceContract:
        if self.negative_probes != [
            "append_audit_after_panel_freeze",
            "cross_target_or_cross_run_evidence",
            "detached_AgentDecision_ToolCall",
            "retry_payload_drift",
            "corrupt_object_bytes_or_artifact_metadata",
            "forbidden_peptide_or_structure_outcome_selection_key",
            "incomplete_shortlist_failure_denominator",
        ]:
            raise ValueError("v35a negative-probe set drifted")
        if self.synthetic_scenario.negative_probe_tool_call_count != len(
            self.negative_probes
        ):
            raise ValueError("v35a negative-probe ToolCall count drifted")
        scenario = self.synthetic_scenario
        counts = self.expected_typed_counts
        successful_run_count = (
            scenario.target_audit_run_count + scenario.selection_run_count
        )
        successful_tool_call_count = (
            scenario.audit_tool_call_count + scenario.selection_tool_call_count
        )
        successful_decision_count = (
            scenario.audit_agent_decision_count
            + scenario.selection_agent_decision_count
        )
        if counts.experiment_run_count != successful_run_count:
            raise ValueError("v35a ExperimentRun count arithmetic drifted")
        if counts.tool_call_count != (
            successful_tool_call_count + scenario.negative_probe_tool_call_count
        ):
            raise ValueError("v35a ToolCall count arithmetic drifted")
        if counts.agent_decision_count != successful_decision_count:
            raise ValueError("v35a AgentDecision count arithmetic drifted")
        if counts.agent_decision_tool_call_edge_count != successful_decision_count:
            raise ValueError("v35a AgentDecision-ToolCall edge count drifted")
        if counts.tool_call_dependency_count != scenario.shortlist_count:
            raise ValueError("v35a selection dependency fan-in count drifted")
        if counts.target_qualification_audit_count != scenario.shortlist_count:
            raise ValueError("v35a qualification audit denominator drifted")
        if counts.target_panel_selection_member_count != scenario.requested_panel_size:
            raise ValueError("v35a selected panel-member count drifted")
        if counts.candidate_count != self.data_boundary.candidate_count:
            raise ValueError("v35a Candidate count boundary drifted")
        if counts.evaluation_count != self.data_boundary.evaluation_count:
            raise ValueError("v35a Evaluation count boundary drifted")
        return self


def load_v35a_acceptance_contract(path: Path) -> V35AAcceptanceContract:
    contract = V35AAcceptanceContract.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    parent_path = (path.parent / contract.parent_contract.path).resolve()
    if sha256_bytes(parent_path.read_bytes()) != contract.parent_contract.sha256:
        raise ValueError("v35a parent target qualification contract checksum mismatch")
    migration_path = (path.parents[2] / contract.migration.path).resolve()
    if sha256_bytes(migration_path.read_bytes()) != contract.migration.sha256:
        raise ValueError("v35a migration checksum mismatch")
    return contract
