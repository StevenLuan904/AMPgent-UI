from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


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
