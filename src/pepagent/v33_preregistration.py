from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from pepagent.provenance.hashing import sha256_bytes, sha256_json


class ChargeDose(BaseModel):
    edit_count: int = Field(ge=1, le=2)
    expected_formal_charge_delta: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def validate_dose(self) -> ChargeDose:
        if self.edit_count != self.expected_formal_charge_delta:
            raise ValueError("neutral-to-cationic edits must add one formal charge each")
        return self


class Arm(BaseModel):
    name: str
    kind: Literal["baseline", "charge_intervention", "edit_control"]
    target_interval: str | None
    paired_to: str | None


class LiteratureCitation(BaseModel):
    pmid: str = Field(pattern=r"^[0-9]+$")
    pmcid: str | None = Field(default=None, pattern=r"^PMC[0-9]+$")
    doi: str | None = None
    source_uri: str


class LiteratureSourceRecord(BaseModel):
    retrieval_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passage_locator: str = Field(min_length=3)
    verification_status: Literal["primary_record_verified"]


class LiteratureEvidenceItem(BaseModel):
    evidence_id: str = Field(min_length=3)
    citation: LiteratureCitation
    source_record: LiteratureSourceRecord
    evidence_grade: Literal[
        "primary_experimental_matched_analog_series",
        "primary_experimental_position_specific_analog_series",
        "primary_experimental_fixed_composition_pattern_series",
        "primary_experimental_position_specific_substitution_series",
        "primary_experimental_matched_identity_length_series",
        "primary_experimental_matched_identity_scaffold_pair",
        "primary_experimental_matched_identity_pair",
        "primary_experimental_boundary_counterexample",
        "mechanistic_molecular_dynamics_and_free_energy_simulation",
    ]
    applicability_distance: str = Field(min_length=3)
    study_type: str = Field(min_length=3)
    scaffold: str = Field(min_length=3)
    intervention: str = Field(min_length=3)
    supports: list[str] = Field(min_length=1)
    does_not_support: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_record_identity(self) -> LiteratureEvidenceItem:
        expected_fragment = f"id={self.citation.pmid}&retmode=xml"
        if expected_fragment not in self.source_record.retrieval_uri:
            raise ValueError("v33 source record does not match its PMID")
        if not self.citation.source_uri.startswith(
            f"https://pubmed.ncbi.nlm.nih.gov/{self.citation.pmid}/"
        ):
            raise ValueError("v33 evidence must identify its primary PubMed record")
        return self


class GeneratorContract(BaseModel):
    development_seeds: list[int] = Field(min_length=3)
    confirmation_seeds: list[int] = Field(min_length=2)
    raw_proposal_budget_per_seed: int
    valid_stream_limit_per_seed: int
    valid_stream_checkpoints: list[int]
    missing_policy: str

    @model_validator(mode="after")
    def validate_budget(self) -> GeneratorContract:
        seeds = self.development_seeds + self.confirmation_seeds
        if len(seeds) != len(set(seeds)):
            raise ValueError("development and confirmation seeds must be globally unique")
        if self.valid_stream_checkpoints != sorted(self.valid_stream_checkpoints):
            raise ValueError("valid stream checkpoints must be strictly ordered")
        if len(self.valid_stream_checkpoints) != len(set(self.valid_stream_checkpoints)):
            raise ValueError("valid stream checkpoints must be unique")
        if self.valid_stream_checkpoints[-1] != self.valid_stream_limit_per_seed:
            raise ValueError("final checkpoint must equal the valid stream limit")
        if self.valid_stream_limit_per_seed > self.raw_proposal_budget_per_seed:
            raise ValueError("valid stream limit cannot exceed raw proposal budget")
        if self.missing_policy != "retain_shortfall_no_refill":
            raise ValueError("v33 must not refill a short generator stream")
        return self


class SearchSaturationGate(BaseModel):
    maximum_new_epsilon_cells_per_50_candidates: int = Field(ge=0)
    maximum_epsilon_cell_turnover_fraction: float = Field(ge=0, le=1)
    must_hold_for_every_pareto_family: Literal[True]
    must_hold_in_all_development_seeds: Literal[True]
    must_hold_in_all_confirmation_seeds: Literal[True]


class CrossSeedAttainmentGate(BaseModel):
    checkpoint: int = Field(gt=0)
    consensus_support: Literal["strict_majority_within_seed_cohort"]
    development_consensus_must_be_attained_by: Literal["every_confirmation_seed"]
    confirmation_consensus_must_be_attained_by: Literal["every_development_seed"]
    symmetric_recurrence_required_for_saturation: Literal[True]
    empty_consensus_is_failure_not_success: Literal[True]


class CostDiagnostic(BaseModel):
    unit: Literal["cumulative_tool_wall_seconds_within_frozen_worker_release"]
    required_at_every_assessment_checkpoint: Literal[True]
    cost_efficiency_is_diagnostic_not_saturation_gate: Literal[True]


class ModelDependenceDiagnostic(BaseModel):
    checkpoint: int = Field(gt=0)
    required_soft_model_metrics_by_family: dict[str, list[str]]
    report_every_seed_and_metric: Literal[True]
    fragility_warning_jaccard_below: float = Field(ge=0, le=1)
    warning_is_orthogonal_to_search_saturation: Literal[True]
    forbidden_inference: Literal["stable_search_does_not_validate_the_soft_models"]


class SearchSufficiency(BaseModel):
    philosophy: Literal["conjunctive_evidence_governance_not_single_indicator_convergence"]
    claim_scope: Literal[
        "empirical_stability_within_frozen_generator_metrics_seeds_and_budget_not_global_optimality"
    ]
    methods_evidence_manifest_path: str
    methods_evidence_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    methods_evidence_manifest_canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixed_full_budget_required: bool
    adaptive_early_stopping_forbidden: bool
    analyze_development_and_confirmation_seeds_separately: Literal[True]
    checkpoint_unit: Literal["reachable_counterfactual_parent"]
    archive_method_version: Literal["v33-search-sufficiency-v2"]
    checkpoint_metrics: list[str]
    saturation_assessment_checkpoints: list[int]
    saturation_gate: SearchSaturationGate
    cross_seed_attainment_gate: CrossSeedAttainmentGate
    cost_diagnostic: CostDiagnostic
    model_dependence_diagnostic: ModelDependenceDiagnostic
    allowed_verdicts: list[str]
    forbidden_verdicts: list[str]
    weighted_total_score_forbidden: bool
    single_hypervolume_completion_claim_forbidden: bool
    front_size_alone_completion_claim_forbidden: bool
    candidate_identity_turnover_alone_completion_claim_forbidden: bool

    @model_validator(mode="after")
    def validate_sufficiency_contract(self) -> SearchSufficiency:
        if self.saturation_assessment_checkpoints != [150, 200]:
            raise ValueError("v33 saturation assessment checkpoints drifted")
        if self.cross_seed_attainment_gate.checkpoint != self.saturation_assessment_checkpoints[-1]:
            raise ValueError("cross-seed attainment must use the final frozen checkpoint")
        required_metrics = {
            "new_nondominated_candidate_rate",
            "archive_turnover_fraction",
            "epsilon_cell_turnover_fraction",
            "new_family_local_epsilon_cells_per_candidate",
            "cross_seed_epsilon_cell_attainment",
            "cost_per_new_epsilon_cell",
            "leave_one_soft_model_out_selection_jaccard",
        }
        if set(self.checkpoint_metrics) != required_metrics:
            raise ValueError("v33 search sufficiency metric set drifted")
        expected_models = {
            "membrane": [],
            "activity_mic": [
                "macrel_amp_probability",
                "llamp_log10_mic_um",
                "amp_read_log10_mic_um",
            ],
            "risk_control": [
                "toxinpred3_hybrid_score",
                "macrel_hemolysis_probability",
            ],
        }
        observed_models = (
            self.model_dependence_diagnostic.required_soft_model_metrics_by_family
        )
        if observed_models != expected_models:
            raise ValueError("v33 leave-one-soft-model-out contract drifted")
        return self


class FormalRun(BaseModel):
    execution_authorized: bool
    submitted: bool
    run_id: str | None
    workflow_id: str | None
    implementation_revision: str | None


class V33Preregistration(BaseModel):
    benchmark_id: Literal["amp_charge_search_sufficiency_v33"]
    version: str
    execution_status: Literal["preregistered_draft_not_authorized"]
    parent_evidence: dict[str, Any]
    generator: GeneratorContract
    charge_definition: dict[str, Any]
    literature_evidence_basis: dict[str, Any]
    edit_contract: dict[str, Any]
    arms: list[Arm]
    required_metric_names: list[str]
    pareto_families: dict[str, list[dict[str, Any]]]
    search_sufficiency: SearchSufficiency
    database_evidence_contract: dict[str, Any]
    scientific_boundaries: dict[str, Any]
    formal_run: FormalRun

    @model_validator(mode="after")
    def validate_scientific_contract(self) -> V33Preregistration:
        doses = {
            name: ChargeDose.model_validate(value)
            for name, value in self.charge_definition["intervention_doses"].items()
        }
        if set(doses) != {"one_positive_residue", "two_positive_residues"}:
            raise ValueError("v33 requires one- and two-residue cationic interventions")
        if [doses[name].edit_count for name in sorted(doses)] != [1, 2]:
            raise ValueError("v33 cationic intervention doses drifted")
        if self.charge_definition["introduced_residue_identities"] != ["K", "R"]:
            raise ValueError("v33 must compare K and R at matched positions")
        if self.parent_evidence.get("permitted_use") != (
            "generator_coverage_diagnostic_and_frozen_baseline_only"
        ):
            raise ValueError("v32 distribution cannot define a v33 biological target")
        if self.literature_evidence_basis.get("target_rule") != (
            "relative_matched_intervention_not_absolute_v32_derived_interval"
        ):
            raise ValueError("v33 target rule must be literature-led and relative")
        if len(self.literature_evidence_basis.get("primary_studies", [])) < 5:
            raise ValueError("v33 literature basis is incomplete")
        if self.literature_evidence_basis.get("manifest_semantics") != (
            "external_primary_evidence_defines_questions_controls_and_forbidden_inferences_not_a_universal_numeric_target"
        ):
            raise ValueError("v33 literature manifest semantics drifted")

        expected_arms = {
            "baseline_unedited",
            "lysine_one",
            "arginine_one",
            "one_charge_preserving_control",
            "lysine_two",
            "arginine_two",
            "two_charge_preserving_control",
        }
        names = [arm.name for arm in self.arms]
        if len(names) != len(set(names)) or set(names) != expected_arms:
            raise ValueError("v33 requires seven unique matched counterfactual arms")

        required_controls = {"Q": "N", "N": "Q", "S": "T", "T": "S"}
        if self.edit_contract["charge_preserving_control_mapping"] != required_controls:
            raise ValueError("charge-preserving control mapping drifted")
        if self.edit_contract["editable_source_residues"] != ["Q", "N", "S", "T"]:
            raise ValueError("primary v33 must not conflate anion removal with cation addition")
        if self.edit_contract["introduced_positive_residues"] != ["K", "R"]:
            raise ValueError("v33 introduces only K/R; histidine remains observational")

        required_metrics = {
            "net_charge_ph7_4",
            "charge_density_ph7_4",
            "maximum_cationic_run",
            "hydrophobic_moment_eisenberg",
            "macrel_amp_probability",
            "llamp_log10_mic_um",
            "amp_read_log10_mic_um",
            "toxinpred3_hybrid_score",
            "macrel_hemolysis_probability",
        }
        if not required_metrics.issubset(self.required_metric_names):
            raise ValueError("v33 required metric families are incomplete")
        if set(self.pareto_families) != {
            "membrane",
            "activity_mic",
            "risk_control",
        }:
            raise ValueError("v33 Pareto families drifted")

        search = self.search_sufficiency
        if not search.fixed_full_budget_required or not search.adaptive_early_stopping_forbidden:
            raise ValueError("v33 must run the fixed budget before a saturation verdict")
        if "global_optimum" not in search.forbidden_verdicts:
            raise ValueError("global optimality claims must remain forbidden")
        if not search.weighted_total_score_forbidden:
            raise ValueError("weighted totals are forbidden")
        if not search.single_hypervolume_completion_claim_forbidden:
            raise ValueError("single hypervolume cannot establish completion")
        if not search.front_size_alone_completion_claim_forbidden:
            raise ValueError("front size alone cannot establish completion")
        if not search.candidate_identity_turnover_alone_completion_claim_forbidden:
            raise ValueError("candidate identity churn alone cannot establish completion")

        evidence = self.database_evidence_contract
        required_evidence = (
            "persist_literature_basis_as_content_addressed_manifest_artifact",
            "persist_exact_literature_source_record_bytes",
            "persist_literature_source_record_sha256_and_passage_locator",
            "persist_cross_study_conflict_witnesses",
            "persist_search_sufficiency_methods_as_content_addressed_manifest_artifact",
            "persist_raw_generator_batches",
            "persist_parent_child_candidate_edges",
            "persist_checkpoint_archive_snapshots",
            "persist_archive_add_remove_and_dominance_reasons",
            "persist_active_and_cumulative_epsilon_cell_history",
            "persist_cross_seed_attainment_surfaces",
            "persist_leave_one_soft_model_out_diagnostics",
            "persist_agent_decisions_and_all_tool_edges",
            "database_object_store_only_replay_required",
        )
        if not all(evidence.get(key) is True for key in required_evidence):
            raise ValueError("v33 database evidence contract is incomplete")

        boundaries = self.scientific_boundaries
        if boundaries.get("PepMLM_used") or boundaries.get("AMPlify_used"):
            raise ValueError("PepMLM and AMPlify are forbidden in v33")
        required_true_boundaries = (
            "no_experimental_activity_claim",
            "no_experimental_safety_claim",
            "no_AceA_binding_or_affinity_claim",
            "no_global_optimality_claim",
            "v32_backwrite_forbidden",
        )
        if not all(boundaries.get(key) is True for key in required_true_boundaries):
            raise ValueError("v33 scientific boundaries are incomplete")
        if boundaries.get("weighted_total_used") is not False:
            raise ValueError("v33 cannot use a weighted total")
        if self.formal_run.execution_authorized or self.formal_run.submitted:
            raise ValueError("v33 draft is not authorized for execution")
        if self.formal_run.run_id is not None or self.formal_run.workflow_id is not None:
            raise ValueError("unsubmitted v33 draft cannot carry run/workflow identities")
        revision = self.formal_run.implementation_revision
        if revision is not None and (
            len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            raise ValueError("frozen v33 implementation revision must be a full lowercase Git SHA")
        return self


def load_v33_preregistration(path: Path) -> V33Preregistration:
    manifest = V33Preregistration.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    evidence = manifest.literature_evidence_basis
    literature_path = (path.parent / evidence["manifest_path"]).resolve()
    payload = literature_path.read_bytes()
    if sha256_bytes(payload) != evidence["manifest_sha256"]:
        raise ValueError("v33 literature evidence manifest checksum mismatch")
    literature = yaml.safe_load(payload)
    target_policy = literature.get("biological_target_policy", {})
    if target_policy.get("v32_distribution_use") != (
        "generator_coverage_and_budget_feasibility_only"
    ):
        raise ValueError("v33 literature evidence reuses generated data as a biological target")
    evidence_items = [
        LiteratureEvidenceItem.model_validate(item)
        for item in literature.get("evidence_items", [])
    ]
    if len(evidence_items) < 9:
        raise ValueError("v33 literature evidence manifest is incomplete")
    evidence_ids = [item.evidence_id for item in evidence_items]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("v33 literature evidence identifiers are missing or duplicated")
    evidence_pmids = {item.citation.pmid for item in evidence_items}
    declared_studies = evidence.get("primary_studies", []) + evidence.get(
        "mechanistic_studies", []
    )
    declared_pmids = {str(item.get("pmid")) for item in declared_studies}
    if evidence_pmids != declared_pmids:
        raise ValueError("v33 benchmark and literature evidence PMID sets drifted")
    primary_count = sum(
        item.evidence_grade.startswith("primary_experimental_")
        for item in evidence_items
    )
    mechanistic_count = sum(
        item.evidence_grade
        == "mechanistic_molecular_dynamics_and_free_energy_simulation"
        for item in evidence_items
    )
    if primary_count < 8 or mechanistic_count != 1:
        raise ValueError("v33 evidence-grade composition drifted")
    source_record_hashes = [item.source_record.sha256 for item in evidence_items]
    if len(source_record_hashes) != len(set(source_record_hashes)):
        raise ValueError("v33 source-record hashes are duplicated")
    snapshot_policy = literature.get("source_snapshot_policy", {})
    required_snapshot_policy = {
        "record_format": "NCBI_PubMed_XML",
        "record_retrieved_on": "2026-08-11",
        "source_record_bytes_currently_in_formal_evidence_graph": False,
        "formal_run_requires_exact_source_record_artifact": True,
        "on_source_record_drift": "block_and_version_manifest_no_silent_refresh",
    }
    if snapshot_policy != required_snapshot_policy:
        raise ValueError("v33 source snapshot policy drifted")
    conflict_witnesses = literature.get("cross_study_conflict_witnesses", [])
    expected_conflict_ids = {
        "K_R_identity_direction_is_scaffold_dependent",
        "charge_amount_is_not_monotonic_activity_or_safety",
        "positive_charge_is_not_sufficient_for_activity",
    }
    observed_conflict_ids = {
        witness.get("conflict_id") for witness in conflict_witnesses
    }
    if observed_conflict_ids != expected_conflict_ids:
        raise ValueError("v33 cross-study conflict witness set drifted")
    for witness in conflict_witnesses:
        referenced_ids = set(witness.get("evidence_ids", []))
        if len(referenced_ids) < 2 or not referenced_ids.issubset(set(evidence_ids)):
            raise ValueError("v33 conflict witness references invalid evidence")
        if not witness.get("implication"):
            raise ValueError("v33 conflict witness lacks a design implication")
    formal_evidence = literature.get("formal_run_evidence_requirements", {})
    required_source_evidence = (
        "persist_exact_source_record_bytes_in_object_store",
        "persist_each_source_record_sha256_and_passage_locator",
        "persist_cross_study_conflict_witnesses",
    )
    if not all(formal_evidence.get(key) is True for key in required_source_evidence):
        raise ValueError("v33 source-level database evidence requirements drifted")
    forbidden = set(
        literature.get("cross_study_inference_rules", {}).get("forbidden", [])
    )
    required_forbidden = {
        "copy_any_study_specific_charge_threshold_into_a_universal_gate",
        "use_generated_charge_quantiles_as_biological_ground_truth",
        "call_K_or_R_globally_superior",
    }
    if not required_forbidden.issubset(forbidden):
        raise ValueError("v33 literature evidence lacks required anti-extrapolation rules")
    methods_path = (
        path.parent / manifest.search_sufficiency.methods_evidence_manifest_path
    ).resolve()
    methods_payload = methods_path.read_bytes()
    if sha256_bytes(methods_payload) != (
        manifest.search_sufficiency.methods_evidence_manifest_sha256
    ):
        raise ValueError("v33 search sufficiency methods manifest checksum mismatch")
    methods = yaml.safe_load(methods_payload)
    if sha256_json(methods) != (
        manifest.search_sufficiency.methods_evidence_manifest_canonical_sha256
    ):
        raise ValueError("v33 search sufficiency methods canonical checksum mismatch")
    if methods.get("methodological_position", {}).get("admissible_completion_claim") != (
        "empirical_stability_within_frozen_generator_metrics_seeds_and_budget"
    ):
        raise ValueError("v33 search sufficiency claim scope drifted")
    if len(methods.get("primary_method_sources", [])) < 5:
        raise ValueError("v33 search sufficiency methods evidence is incomplete")
    return manifest
