from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FrozenCohortSpec(BaseModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=1)
    sequence_column: str = Field(min_length=1)
    sequence_sha256_column: str = Field(min_length=1)
    selection_forbidden: bool


class ValidatorArchiveSpec(BaseModel):
    record_uri: str = Field(pattern=r"^https://")
    record_id: int = Field(ge=1)
    record_doi: str = Field(min_length=1)
    record_license: str = Field(min_length=1)
    archive_name: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    upstream_digest: str = Field(pattern=r"^md5:[0-9a-f]{32}$")
    local_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    extracted_inventory_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    serialized_model_format: Literal["pickle"]
    untrusted_deserialization: bool


class TrainingOverlapSpec(BaseModel):
    dataset_name: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    upstream_digest: str = Field(pattern=r"^md5:[0-9a-f]{32}$")
    row_count: int = Field(ge=1)
    exact_sequence_overlap_count: int = Field(ge=0)


class SafetyValidationManifest(BaseModel):
    validation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    version: str = Field(min_length=1)
    execution_status: Literal["archive_pending", "ready", "completed"]
    track: Literal["frozen_cohort_external_safety_validation"]
    reference_benchmark_id: str = Field(min_length=1)
    reference_code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    reference_results_immutable: bool
    input_cohort: FrozenCohortSpec
    validator_name: str = Field(min_length=1)
    paper_uri: str = Field(pattern=r"^https://")
    paper_venue: str = Field(min_length=1)
    archive: ValidatorArchiveSpec
    training_overlap_audit: list[TrainingOverlapSpec] = Field(min_length=1)
    expected_output_columns: list[str] = Field(min_length=1)
    expected_output_rows: int = Field(ge=1)
    isolated_execution_required: bool
    network_disabled_during_inference: bool
    scientific_contract: dict[str, bool]
    fail_closed: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_contract(self) -> SafetyValidationManifest:
        if self.reference_results_immutable is not True:
            raise ValueError("external validation cannot rewrite the frozen benchmark")
        if self.input_cohort.selection_forbidden is not True:
            raise ValueError("the frozen cohort must be evaluated without selection")
        if self.expected_output_rows != self.input_cohort.row_count:
            raise ValueError("every frozen input row must have one output row")
        if len(self.expected_output_columns) != len(set(self.expected_output_columns)):
            raise ValueError("expected output columns must be unique")
        if self.archive.untrusted_deserialization is not True:
            raise ValueError("pickle validators must be treated as untrusted")
        if not self.isolated_execution_required or not self.network_disabled_during_inference:
            raise ValueError("untrusted validator execution must be isolated and offline")
        if self.execution_status in {"ready", "completed"} and (
            self.archive.local_sha256 is None
            or self.archive.extracted_inventory_sha256 is None
        ):
            raise ValueError("ready validation requires archive and inventory SHA-256")
        required_flags = {
            "frozen_full_cohort_no_filtering",
            "validator_not_used_for_generation",
            "no_existing_metric_used_for_selection",
            "no_binding_or_affinity_claim",
            "soft_prediction_not_experimental_evidence",
            "no_reference_result_rewrite",
        }
        if any(self.scientific_contract.get(flag) is not True for flag in required_flags):
            raise ValueError("scientific_contract is missing a required true flag")
        return self
