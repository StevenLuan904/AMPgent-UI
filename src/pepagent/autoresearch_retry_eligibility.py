from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_json

TERMINAL_TEMPORAL_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
)


class RetryEligibilityObservation(BaseModel):
    """Read-only facts required before freezing an AutoResearch successor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ampgent.autoresearch-retry-observation.1"] = (
        "ampgent.autoresearch-retry-observation.1"
    )
    predecessor_run_id: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    database_status: str = Field(min_length=1)
    temporal_workflow_id: str = Field(min_length=1)
    temporal_run_id: str = Field(min_length=1)
    observed_temporal_workflow_id: str = Field(min_length=1)
    observed_temporal_run_id: str = Field(min_length=1)
    temporal_status: str = Field(min_length=1)
    successor_run_ids: tuple[str, ...] = ()
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_policy: Literal["freeze_only", "submit_allowed"] = "freeze_only"
    generator_gpu_work_required: Literal[True] = True
    new_gpu_tasks_allowed: bool = False

    @model_validator(mode="after")
    def validate_successors(self) -> RetryEligibilityObservation:
        if len(set(self.successor_run_ids)) != len(self.successor_run_ids):
            raise ValueError("AutoResearch retry observation contains duplicate successors")
        return self


class RetryEligibilityDecision(BaseModel):
    """Deterministic successor eligibility; this object never submits a workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ampgent.autoresearch-retry-eligibility.1"] = (
        "ampgent.autoresearch-retry-eligibility.1"
    )
    predecessor_run_id: str
    target_key: str
    eligible_to_freeze: bool
    eligible_to_submit: bool
    successor_identity_seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_codes: tuple[str, ...]
    observation: RetryEligibilityObservation

    @property
    def eligibility_sha256(self) -> str:
        return sha256_json(
            self.model_dump(mode="json", exclude={"eligibility_sha256"})
        )


def decide_retry_eligibility(
    observation: RetryEligibilityObservation,
) -> RetryEligibilityDecision:
    """Fail closed unless PostgreSQL and the exact Temporal execution are terminal."""

    blockers: list[str] = []
    if observation.database_status.lower() != "failed":
        blockers.append("predecessor_database_status_not_failed")
    if observation.temporal_status.upper() not in TERMINAL_TEMPORAL_STATUSES:
        blockers.append("temporal_execution_not_terminal")
    if observation.observed_temporal_workflow_id != observation.temporal_workflow_id:
        blockers.append("temporal_workflow_binding_drifted")
    if observation.observed_temporal_run_id != observation.temporal_run_id:
        blockers.append("temporal_run_binding_drifted")
    if observation.successor_run_ids:
        blockers.append("existing_successor_present")

    eligible_to_freeze = not blockers
    reason_codes = list(blockers)
    if eligible_to_freeze:
        reason_codes.append("eligible_exact_once_successor_freeze")
    if observation.execution_policy == "freeze_only":
        reason_codes.append("freeze_only_policy")
    if observation.generator_gpu_work_required and not observation.new_gpu_tasks_allowed:
        reason_codes.append("new_gpu_tasks_prohibited")

    eligible_to_submit = (
        eligible_to_freeze
        and observation.execution_policy == "submit_allowed"
        and (
            not observation.generator_gpu_work_required
            or observation.new_gpu_tasks_allowed
        )
    )
    if eligible_to_submit:
        reason_codes.append("eligible_exact_once_successor_submission")

    identity_seed = sha256_json(
        {
            "schema_version": "ampgent.autoresearch-successor-identity-seed.1",
            "predecessor_run_id": observation.predecessor_run_id,
            "target_key": observation.target_key.lower(),
            "source_revision": observation.source_revision,
            "continuity_mode": "automatic_retry_from_failed_predecessor",
            "historical_outputs_reused": False,
        }
    )
    return RetryEligibilityDecision(
        predecessor_run_id=observation.predecessor_run_id,
        target_key=observation.target_key,
        eligible_to_freeze=eligible_to_freeze,
        eligible_to_submit=eligible_to_submit,
        successor_identity_seed_sha256=identity_seed,
        reason_codes=tuple(reason_codes),
        observation=observation,
    )


__all__ = [
    "RetryEligibilityDecision",
    "RetryEligibilityObservation",
    "TERMINAL_TEMPORAL_STATUSES",
    "decide_retry_eligibility",
]
