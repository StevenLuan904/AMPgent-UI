from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_bytes


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MigrationContract(StrictContractModel):
    path: Literal["migrations/versions/0010_harness_evolution_lineage.py"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    from_revision: Literal["0009_candidate_occurrences"]
    to_revision: Literal["0010_harness_evolution_lineage"]
    deploy_to_shared_PostgreSQL_only_after_authorization: Literal[True]
    transactional_upgrade_and_schema_introspection_required: Literal[True]
    downgrade_is_not_part_of_acceptance: Literal[True]


class AuthorizationContract(StrictContractModel):
    execution_authorized: Literal[False]
    submitted: Literal[False]
    run_id: None
    explicit_user_phrase_required: Literal["授权 v36a 合成数据库闭环验收"]
    authorization_cannot_be_inferred_from_roadmap_work: Literal[True]


class SyntheticDataBoundary(StrictContractModel):
    synthetic_only: Literal[True]
    historical_candidate_or_evaluation_reads_forbidden: Literal[True]
    candidate_generation_forbidden: Literal[True]
    candidate_count: Literal[0]
    evaluation_count: Literal[0]
    real_harness_activation_forbidden: Literal[True]
    formal_challenger_or_promotion_forbidden: Literal[True]
    frozen_runs_may_be_mutated: Literal[False]


class AggregateExpectedCounts(StrictContractModel):
    harness_release_count: Literal[6]
    lineage_edge_count: Literal[4]
    trial_count: Literal[6]
    assignment_count: Literal[12]
    outcome_count: Literal[60]
    experiment_run_count: Literal[14]
    outcome_tool_call_count: Literal[12]
    agent_decision_count: Literal[2]
    candidate_count: Literal[0]
    evaluation_count: Literal[0]


class DatabaseEvidenceContract(StrictContractModel):
    PostgreSQL_is_authoritative: Literal[True]
    object_store_is_content_addressed: Literal[True]
    persist_release_trial_assignment_outcome_and_decision_rows: Literal[True]
    persist_every_artifact_and_existing_graph_edge: Literal[True]
    database_object_store_only_replay_required: Literal[True]
    replay_both_scenarios_independently: Literal[True]
    failed_attempts_and_retries_are_persisted: Literal[True]
    CSV_JSON_and_Markdown_are_exports_only: Literal[True]
    required_artifact_roles: list[str]

    @model_validator(mode="after")
    def validate_artifact_roles(self) -> DatabaseEvidenceContract:
        if self.required_artifact_roles != [
            "allowed_evidence_slice",
            "forbidden_holdout_manifest",
            "endpoint_contract",
            "source_and_environment_footprints",
            "history_partition_manifest",
            "assignment_manifest",
            "blinding_manifest",
            "budget_contract",
            "terminal_decision",
            "scenario_replay_bundle",
            "aggregate_acceptance_receipt",
        ]:
            raise ValueError("v36a required artifact roles drifted")
        return self


class AcceptanceVerdicts(StrictContractModel):
    allowed: list[str]
    success_does_not_authorize_real_harness_evolution: Literal[True]
    success_does_not_prove_harness_improvement: Literal[True]

    @model_validator(mode="after")
    def validate_allowed_verdicts(self) -> AcceptanceVerdicts:
        if self.allowed != [
            "synthetic_database_closure_accepted",
            "synthetic_database_closure_rejected",
        ]:
            raise ValueError("v36a allowed verdict set drifted")
        return self


class SyntheticScenarioContract(StrictContractModel):
    scenario_id: str
    scope_id: str
    terminal_decision: Literal[
        "promote_for_declared_scope",
        "rollback_to_registered_ancestor",
    ]
    release_count: Literal[3]
    lineage_edge_count: Literal[2]
    trial_phases: list[str]
    assignment_count: Literal[6]
    outcome_count: Literal[30]
    experiment_run_count: Literal[7]
    outcome_tool_call_count: Literal[6]
    agent_decision_count: Literal[1]

    @model_validator(mode="after")
    def validate_scenario(self) -> SyntheticScenarioContract:
        if self.trial_phases != [
            "counterfactual_replay",
            "shadow",
            "prospective_equal_budget",
        ]:
            raise ValueError("v36a requires the exact three-gate synthetic trial chain")
        return self


class V36AAcceptanceContract(StrictContractModel):
    benchmark_id: Literal["amp_harness_synthetic_acceptance_v36a"]
    version: Literal["v36a.0.1-preregistered-synthetic-database-acceptance"]
    execution_status: Literal["preregistered_not_authorized"]
    implementation_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    parent_contract_path: str
    parent_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration: MigrationContract
    authorization: AuthorizationContract
    data_boundary: SyntheticDataBoundary
    scenarios: list[SyntheticScenarioContract]
    endpoint_families: list[str]
    aggregate_expected_counts: AggregateExpectedCounts
    database_evidence_contract: DatabaseEvidenceContract
    acceptance_verdicts: AcceptanceVerdicts

    @model_validator(mode="after")
    def validate_acceptance_contract(self) -> V36AAcceptanceContract:
        if [scenario.scenario_id for scenario in self.scenarios] != [
            "synthetic_scope_promotion",
            "synthetic_ancestor_rollback",
        ]:
            raise ValueError("v36a requires promotion and rollback acceptance scenarios")
        if len({scenario.scope_id for scenario in self.scenarios}) != len(self.scenarios):
            raise ValueError("v36a synthetic scenarios must use isolated scopes")
        if [scenario.terminal_decision for scenario in self.scenarios] != [
            "promote_for_declared_scope",
            "rollback_to_registered_ancestor",
        ]:
            raise ValueError("v36a terminal decision coverage drifted")
        required_endpoint_families = [
            "discovery_quality",
            "error_control",
            "stability",
            "efficiency",
            "evidence_quality",
        ]
        if self.endpoint_families != required_endpoint_families:
            raise ValueError("v36a endpoint-family coverage drifted")
        return self


def load_v36a_acceptance_contract(path: Path) -> V36AAcceptanceContract:
    contract = V36AAcceptanceContract.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    parent_path = (path.parent / contract.parent_contract_path).resolve()
    if sha256_bytes(parent_path.read_bytes()) != contract.parent_contract_sha256:
        raise ValueError("v36a parent harness contract checksum mismatch")
    migration_path = (path.parents[2] / contract.migration.path).resolve()
    if sha256_bytes(migration_path.read_bytes()) != contract.migration.sha256:
        raise ValueError("v36a migration checksum mismatch")
    return contract
