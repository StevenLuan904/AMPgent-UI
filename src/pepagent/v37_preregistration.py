from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from pepagent.provenance.hashing import sha256_file


class V37Engine(BaseModel):
    generator_id: Literal["hydramp", "ampgan_v2", "amp_designer"]
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    seeds: list[int] = Field(min_length=3, max_length=3)


class V37FormalRun(BaseModel):
    direction_authorized: Literal[True]
    execution_authorized: Literal[False]
    submitted: Literal[False]
    implementation_revision: None
    run_id: None
    workflow_id: None


class V37Manifest(BaseModel):
    benchmark_id: Literal["amp_rapid_champion_generation_v37"]
    version: Literal["v37.0.0-preregistered"]
    execution_status: Literal["direction_authorized_pending_preexecution_gates"]
    track: Literal["single_arm_rapid_champion_generation"]
    scientific_question: dict[str, Any]
    design: dict[str, Any]
    target: dict[str, Any]
    generators: dict[str, Any]
    charge_policy: dict[str, Any]
    verified_auxiliaries: dict[str, Any]
    stage_1_sequence_evaluation: dict[str, Any]
    stage_2_structure_confirmation: dict[str, Any]
    final_portfolio: dict[str, Any]
    stop_conditions: dict[str, Any]
    database_evidence_contract: dict[str, Any]
    pre_execution_gates: list[str]
    scientific_boundaries: dict[str, Any]
    formal_run: V37FormalRun

    @model_validator(mode="after")
    def validate_frozen_contract(self) -> V37Manifest:
        if self.design.get("arms") != 1:
            raise ValueError("v37 is one champion arm")
        if self.design.get("weighted_total_score_forbidden") is not True:
            raise ValueError("v37 forbids weighted total scores")
        engines = [V37Engine.model_validate(item) for item in self.generators["engines"]]
        if [item.generator_id for item in engines] != [
            "hydramp",
            "ampgan_v2",
            "amp_designer",
        ]:
            raise ValueError("v37 generator order drifted")
        seeds = [seed for engine in engines for seed in engine.seeds]
        if len(seeds) != len(set(seeds)) or len(seeds) != 9:
            raise ValueError("v37 requires nine globally unique generator seeds")
        retained = self.generators["evaluated_valid_unique_per_generator_seed"]
        expected = len(seeds) * retained
        if expected != self.generators["expected_candidate_count"]:
            raise ValueError("v37 generator budget is inconsistent")
        if expected != self.stage_1_sequence_evaluation["expected_candidate_count"]:
            raise ValueError("v37 stage-1 budget is inconsistent")
        structure = self.stage_2_structure_confirmation
        if structure.get("boltz_seeds") != [20270380, 20270381, 20270382]:
            raise ValueError("v37 requires the three frozen Boltz seeds")
        if structure.get("poses_per_candidate") != 3:
            raise ValueError("v37 requires three poses per shortlisted candidate")
        if structure.get("rosetta_decoys_per_pose") != 16:
            raise ValueError("v37 requires 16 Rosetta decoys per pose")
        expected_poses = structure["expected_maximum_candidates"] * 3
        if structure.get("expected_maximum_poses") != expected_poses:
            raise ValueError("v37 maximum pose budget drifted")
        if structure.get("expected_maximum_rosetta_decoys") != expected_poses * 16:
            raise ValueError("v37 maximum Rosetta budget drifted")
        if self.charge_policy.get("mode") != "observe_only_not_an_optimization_axis":
            raise ValueError("v37 may not optimize positive charge")
        if self.verified_auxiliaries.get("effectiveness_claim_allowed") is not False:
            raise ValueError("v37 cannot claim auxiliary effectiveness")
        evidence = self.database_evidence_contract
        if not evidence.get("database_object_store_only_replay_required"):
            raise ValueError("v37 requires database/object-store replay")
        if not evidence.get("persist_all_metric_ToolCalls_Evaluations_and_dependencies"):
            raise ValueError("v37 requires typed metric evidence")
        if self.scientific_boundaries.get("AMPlify_used") is not False:
            raise ValueError("AMPlify is permanently retired")
        return self


def load_v37_preregistration(path: Path) -> V37Manifest:
    return V37Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def validate_v37_experiment_spec(
    manifest: V37Manifest,
    config_path: Path,
    *,
    spec_path_override: Path | None = None,
) -> dict[str, Any]:
    """Bind the executable structure spec to the frozen benchmark exactly."""
    structure = manifest.stage_2_structure_confirmation
    spec_path = spec_path_override or (
        config_path.parent / structure["experiment_spec_path"]
    )
    observed_sha = sha256_file(spec_path)
    if observed_sha != structure["experiment_spec_sha256"]:
        raise ValueError("v37 experiment spec SHA drifted")
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if spec.get("experiment_id") != "acea_v37_rapid_champion_structure":
        raise ValueError("v37 experiment spec identity drifted")
    if spec.get("version") != manifest.version:
        raise ValueError("v37 experiment spec version drifted")
    if spec.get("structure_protocol") != "diagnostic_fast":
        raise ValueError("v37 structure support thresholds must remain diagnostic-only")
    if spec.get("interface_support_thresholds_decision_use") != (
        "observe_only_coordinate_audit_not_candidate_gate"
    ):
        raise ValueError("v37 coordinate-audit thresholds may not become candidate gates")
    if spec.get("boltz_seed_values") != structure["boltz_seeds"]:
        raise ValueError("v37 experiment spec Boltz seeds drifted")
    if spec.get("boltz_seeds_per_candidate") != structure["poses_per_candidate"]:
        raise ValueError("v37 experiment spec pose count drifted")
    if spec.get("rosetta_nstruct") != structure["rosetta_decoys_per_pose"]:
        raise ValueError("v37 experiment spec Rosetta decoy count drifted")
    if spec.get("rosetta_score_function") != structure["rosetta_score_function"]:
        raise ValueError("v37 experiment spec Rosetta score function drifted")
    if spec.get("rosetta_all_boltz_samples") is not True:
        raise ValueError("v37 experiment spec must score every Boltz pose")
    if spec.get("boltz_force_pocket") is not True:
        raise ValueError("v37 experiment spec must force the frozen pocket")
    eligibility = structure["structural_eligibility"]
    if eligibility.get("no_numerical_binding_threshold") is not True:
        raise ValueError("v37 forbids a numerical binding threshold")
    if eligibility.get("coordinate_audit_thresholds_observational_only") is not True:
        raise ValueError("v37 coordinate-audit thresholds must be observe-only")
    target_path = config_path.parent / manifest.target["target_spec_path"]
    if sha256_file(target_path) != manifest.target["target_spec_sha256"]:
        raise ValueError("v37 target spec SHA drifted")
    target_spec = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    if spec.get("target") != target_spec.get("target"):
        raise ValueError("v37 experiment target differs from the frozen target spec")
    return {
        "experiment_spec_path": str(structure["experiment_spec_path"]),
        "experiment_spec_sha256": observed_sha,
        "target_spec_sha256": manifest.target["target_spec_sha256"],
        "boltz_seeds": list(structure["boltz_seeds"]),
        "rosetta_decoys_per_pose": structure["rosetta_decoys_per_pose"],
    }
