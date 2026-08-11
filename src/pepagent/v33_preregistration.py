from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from pepagent.provenance.hashing import sha256_bytes


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


class SearchSufficiency(BaseModel):
    fixed_full_budget_required: bool
    adaptive_early_stopping_forbidden: bool
    saturation_assessment_checkpoints: list[int]
    allowed_verdicts: list[str]
    forbidden_verdicts: list[str]
    weighted_total_score_forbidden: bool
    single_hypervolume_completion_claim_forbidden: bool


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

        evidence = self.database_evidence_contract
        required_evidence = (
            "persist_literature_basis_as_content_addressed_manifest_artifact",
            "persist_raw_generator_batches",
            "persist_parent_child_candidate_edges",
            "persist_checkpoint_archive_snapshots",
            "persist_archive_add_remove_and_dominance_reasons",
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
    evidence_items = literature.get("evidence_items", [])
    if len(evidence_items) < 7:
        raise ValueError("v33 literature evidence manifest is incomplete")
    evidence_ids = [item.get("evidence_id") for item in evidence_items]
    if None in evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("v33 literature evidence identifiers are missing or duplicated")
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
    return manifest
