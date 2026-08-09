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
    static_inventory_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    static_entry_count: int | None = Field(default=None, ge=1)
    static_file_count: int | None = Field(default=None, ge=1)
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


class ExtractedArtifactSpec(BaseModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FeatureContractSpec(BaseModel):
    feature_count: int = Field(ge=1)
    ordered_feature_names_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_feature: str = Field(min_length=1)
    last_feature: str = Field(min_length=1)


class NarrowAdapterContract(BaseModel):
    adapter_id: str = Field(min_length=1)
    classification_backend: Literal["random_forest_model_1"]
    regression_backend: Literal["random_forest_hc50"]
    allowed_model_paths: list[str] = Field(min_length=2, max_length=2)
    pickle_global_allowlist: list[str] = Field(min_length=1)
    extraction_allowlist: list[ExtractedArtifactSpec] = Field(min_length=1)
    sklearn_version: Literal["1.3.1"]
    classification_feature_contract: FeatureContractSpec
    regression_feature_contract: FeatureContractSpec
    upstream_cli_execution_forbidden: bool
    shell_execution_forbidden: bool
    merci_disabled: bool
    esm_disabled: bool
    protein_scan_disabled: bool
    design_and_mutation_disabled: bool
    network_access_forbidden: bool
    archive_metadata_root_ignored_for_execution: Literal["__MACOSX/"]
    extraction_destination: str = Field(min_length=1)
    extracted_file_count: int = Field(ge=1)

    @model_validator(mode="after")
    def require_narrow_surface(self) -> NarrowAdapterContract:
        required_true = (
            self.upstream_cli_execution_forbidden,
            self.shell_execution_forbidden,
            self.merci_disabled,
            self.esm_disabled,
            self.protein_scan_disabled,
            self.design_and_mutation_disabled,
            self.network_access_forbidden,
        )
        if not all(required_true):
            raise ValueError("HemoPI2 adapter must keep every excluded surface disabled")
        if set(self.allowed_model_paths) != {
            "hemopi2/Model/hemopi2_ml_clf.sav",
            "hemopi2/Model/HemoPI2_reg.sav",
        }:
            raise ValueError("HemoPI2 adapter model allowlist must be exact")
        expected_pickle_globals = {
            "numpy.core.multiarray._reconstruct",
            "numpy.core.multiarray.scalar",
            "numpy.dtype",
            "numpy.ndarray",
            "sklearn.ensemble._forest.RandomForestClassifier",
            "sklearn.ensemble._forest.RandomForestRegressor",
            "sklearn.tree._classes.DecisionTreeClassifier",
            "sklearn.tree._classes.DecisionTreeRegressor",
            "sklearn.tree._tree.Tree",
        }
        if set(self.pickle_global_allowlist) != expected_pickle_globals:
            raise ValueError("HemoPI2 pickle global allowlist must be exact")
        extracted_paths = [item.path for item in self.extraction_allowlist]
        if len(extracted_paths) != len(set(extracted_paths)):
            raise ValueError("HemoPI2 extraction allowlist paths must be unique")
        if not set(self.allowed_model_paths).issubset(extracted_paths):
            raise ValueError("every allowed model must be in the extraction allowlist")
        forbidden_extraction_markers = (
            "__macosx",
            "pytorch_model",
            "hemopi2_classification.py",
            "hemopi2_regression.py",
            "merci/",
        )
        if any(
            marker in path.lower()
            for path in extracted_paths
            for marker in forbidden_extraction_markers
        ):
            raise ValueError("forbidden HemoPI2 surface entered extraction allowlist")
        if self.extracted_file_count != len(extracted_paths):
            raise ValueError("extracted file count must match extraction allowlist")
        if self.classification_feature_contract.feature_count != 1190:
            raise ValueError("classification feature count must remain 1190")
        if self.regression_feature_contract.feature_count != 1167:
            raise ValueError("regression feature count must remain 1167")
        return self


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
    adapter_contract: NarrowAdapterContract
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
        if self.archive.local_sha256 is not None and (
            self.archive.static_inventory_sha256 is None
            or self.archive.static_entry_count is None
            or self.archive.static_file_count is None
        ):
            raise ValueError("downloaded archive requires a frozen static inventory")
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
