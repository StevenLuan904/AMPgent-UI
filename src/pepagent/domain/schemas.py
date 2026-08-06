from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pepagent.handoff_metrics import METRIC_PLUGIN_CONTRACTS


class TargetSpec(BaseModel):
    name: str
    sequence: str
    organism: str | None = None
    accession: str | None = None
    source_database: str | None = None
    source_uri: str | None = None
    source_version: str | None = None
    source_retrieved_at: datetime | None = None
    pocket_residues: list[int] = Field(default_factory=list)

    @field_validator("sequence")
    @classmethod
    def normalize_sequence(cls, value: str) -> str:
        normalized = "".join(value.split()).upper()
        allowed = set("ACDEFGHIKLMNPQRSTVWY")
        invalid = sorted(set(normalized) - allowed)
        if invalid:
            raise ValueError(f"invalid amino-acid symbols: {invalid}")
        return normalized


class MetricPolicyRule(BaseModel):
    metric_name: str
    role: Literal["qualification", "objective", "diversity", "diagnostic"]
    direction: Literal["minimize", "maximize"] | None = None
    minimum: float | None = None
    maximum: float | None = None
    hard: bool = True
    missing_policy: Literal["fail", "worst", "ignore"] = "fail"
    priority: int = Field(default=100, ge=0)
    stages: list[Literal["proposal", "research", "final"]] = Field(
        default_factory=lambda: ["research", "final"]
    )
    rationale: str

    @model_validator(mode="after")
    def validate_role_contract(self) -> "MetricPolicyRule":
        if self.role == "objective" and self.direction is None:
            raise ValueError("objective rules require minimize or maximize direction")
        if self.role in {"qualification", "diversity"}:
            if self.minimum is None and self.maximum is None:
                raise ValueError(f"{self.role} rules require a minimum or maximum")
        if self.role in {"qualification", "diversity"} and self.direction is not None:
            raise ValueError(f"{self.role} rules do not use optimization direction")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("metric-rule minimum cannot exceed maximum")
        if not self.stages:
            raise ValueError("metric rules must apply to at least one selection stage")
        return self


class OptionalMetricSpec(BaseModel):
    name: Literal[
        "physicochemical_developability",
        "hemolysis_risk",
        "toxicity_risk",
        "mic_potency",
        "mic_potency_amp_read",
        "amp_likeness",
        "sequence_novelty",
        "serum_half_life",
        "aggregation_apr",
    ]
    enabled: bool = True
    trust: Literal["descriptor", "soft", "shadow"]
    stages: list[Literal["proposal", "research", "final"]] = Field(
        default_factory=lambda: ["research", "final"]
    )
    failure_policy: Literal["record_unavailable", "fail_run"] = "record_unavailable"
    parameters: dict[str, bool | int | float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_handoff_contract(self) -> "OptionalMetricSpec":
        if not self.stages:
            raise ValueError("optional metric must run in at least one stage")
        maximum = METRIC_PLUGIN_CONTRACTS[self.name]["maximum_trust"]
        allowed = {
            "descriptor": {"descriptor"},
            "soft": {"soft", "shadow"},
            "shadow": {"shadow"},
        }[maximum]
        if self.trust not in allowed:
            raise ValueError(
                f"{self.name} permits trust {sorted(allowed)}, not {self.trust!r}"
            )
        if self.name == "physicochemical_developability":
            permitted = {"ph", "c_terminal_amidated", "hydrophobic_moment_angle"}
            unknown = set(self.parameters) - permitted
            if unknown:
                raise ValueError(
                    f"unknown physicochemical parameter(s): {sorted(unknown)}"
                )
        return self


class MutationKnowledgeCard(BaseModel):
    item_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_uri: str | None = None
    source_locator: str | None = None


class ExperimentSpec(BaseModel):
    target: TargetSpec
    peptide_lengths: list[int] = Field(default_factory=lambda: [12, 16, 20])
    candidates_per_length: int = 64
    structure_top_k: int = 8
    generations: int = 2
    autoresearch_enabled: bool = False
    structure_protocol: Literal["legacy_ensemble_gate", "diagnostic_fast"] = (
        "legacy_ensemble_gate"
    )
    search_structure_comprehensive_count: int = Field(default=2, ge=0, le=10)
    search_structure_diversity_count: int = Field(default=2, ge=0, le=10)
    final_structure_candidate_count: int = Field(default=8, ge=1, le=10)
    severe_structure_clash_count: int = Field(default=25, ge=1)
    seed: int = 20260731
    pepmlm_model: str = "ChatterjeeLab/PepMLM-650M"
    search_regime: Literal["E0", "E1", "E2", "E3", "E4"] = "E0"
    pepmlm_de_novo_top_k: int = Field(default=3, ge=1, le=20)
    pepmlm_mutation_top_k: int = Field(default=5, ge=1, le=20)
    pepmlm_temperature: float = Field(default=1.0, ge=0.1, le=3.0)
    boltz_method: str = "boltz2"
    diffusion_samples: int = 5
    boltz_seeds_per_candidate: int = Field(default=1, ge=1, le=16)
    boltz_recycling_steps: int = 3
    boltz_sampling_steps: int = 200
    boltz_use_potentials: bool = True
    boltz_no_kernels: bool = True
    use_msa_server: bool = True
    boltz_force_pocket: bool = False
    pocket_max_distance_angstrom: float = Field(default=8.0, gt=0)
    interface_contact_distance_angstrom: float = Field(default=5.0, gt=0)
    interface_clash_distance_angstrom: float = Field(default=1.5, gt=0)
    interface_min_pocket_contacts: int = Field(default=1, ge=1)
    interface_min_seed_consistency: float = Field(default=0.5, ge=0, le=1)
    interface_min_pair_iptm_median: float = Field(default=0.2, ge=0, le=1)
    interface_pose_cluster_rmsd_angstrom: float = Field(default=4.0, gt=0)
    interface_min_pose_cluster_fraction: float = Field(default=0.5, ge=0, le=1)
    maximum_sequence_similarity: float = Field(default=0.75, ge=0, le=1)
    elite_parent_count: int = Field(default=3, ge=1)
    mutation_children_per_parent: int = Field(default=3, ge=1)
    mutation_count_min: int = Field(default=1, ge=1)
    mutation_count_max: int = Field(default=3, ge=1)
    exploration_candidates_per_length: int = Field(default=2, ge=0)
    mutation_knowledge_cards: list[MutationKnowledgeCard] = Field(
        default_factory=list,
        description=(
            "Versioned, atomic knowledge items supplied to the mutation Director. "
            "Each item is content-addressed independently when the decision is recorded."
        ),
    )
    rosetta_enabled: bool = False
    rosetta_top_k: int = Field(default=1, ge=1)
    rosetta_nstruct: int = Field(default=200, ge=1)
    rosetta_parallel_decoys: int = Field(default=1, ge=1, le=16)
    rosetta_pair_iptm_min: float = Field(default=0.5, ge=0, le=1)
    rosetta_score_function: str = "ref2015"
    exploratory_rosetta_slots: int = Field(default=0, ge=0, le=1)
    bulk_rosetta_all_qualified: bool = False
    bulk_rosetta_candidate_limit: int = Field(default=250, ge=1, le=500)
    bulk_csv_report_threshold: int = Field(default=200, ge=1, le=500)
    bulk_evaluation_concurrency: int = Field(default=4, ge=1, le=8)
    optional_metrics: list[OptionalMetricSpec] = Field(default_factory=list)
    metric_policy: list[MetricPolicyRule] = Field(default_factory=list)
    affinity_evaluators: list[str] = Field(
        default_factory=list,
        description=(
            "Reserved for affinity evaluators that pass the reproducibility admission gate."
        ),
    )

    @field_validator("affinity_evaluators")
    @classmethod
    def reject_frozen_evaluators(cls, value: list[str]) -> list[str]:
        if "peppap" in {name.lower() for name in value}:
            raise ValueError("PepPAP is frozen and is not admitted to the experiment workflow")
        return value

    @field_validator("rosetta_score_function")
    @classmethod
    def require_calibrated_rosetta_score_function(cls, value: str) -> str:
        if value != "ref2015":
            raise ValueError("MVP-v2 admits only the versioned ref2015 Rosetta protocol")
        return value

    @model_validator(mode="after")
    def validate_rosetta_protocol(self) -> "ExperimentSpec":
        if (
            self.rosetta_enabled
            and self.structure_protocol == "legacy_ensemble_gate"
            and self.rosetta_nstruct < 200
        ):
            raise ValueError(
                "decision-bearing FlexPepDock runs require at least 200 refinement decoys"
            )
        if self.rosetta_top_k > max(self.structure_top_k, self.final_structure_candidate_count):
            raise ValueError("rosetta_top_k cannot exceed structure_top_k")
        if self.mutation_count_max < self.mutation_count_min:
            raise ValueError("mutation_count_max cannot be below mutation_count_min")
        if self.autoresearch_enabled:
            if self.generations < 3:
                raise ValueError("MVP-v2 Auto Research requires at least three generations")
            if not self.target.pocket_residues:
                raise ValueError("MVP-v2 Auto Research requires versioned pocket residues")
            if (
                self.structure_protocol == "legacy_ensemble_gate"
                and self.boltz_seeds_per_candidate < 2
            ):
                raise ValueError("MVP-v2 Auto Research requires multiple independent Boltz seeds")
            if self.structure_protocol == "legacy_ensemble_gate" and not self.rosetta_enabled:
                raise ValueError("MVP-v2 Auto Research requires the admitted Rosetta lane")
            if self.structure_protocol == "diagnostic_fast":
                representative_count = (
                    self.search_structure_comprehensive_count
                    + self.search_structure_diversity_count
                )
                if representative_count < 1:
                    raise ValueError("diagnostic_fast requires at least one search representative")
                if self.boltz_seeds_per_candidate != 1:
                    raise ValueError(
                        "diagnostic_fast requires exactly one Boltz seed per candidate"
                    )
                if self.rosetta_enabled and self.rosetta_nstruct > 8:
                    raise ValueError("diagnostic_fast permits at most eight shadow Rosetta decoys")
                if self.bulk_rosetta_all_qualified and self.rosetta_nstruct > 8:
                    raise ValueError(
                        "bulk diagnostic Rosetta permits at most eight decoys per candidate"
                    )
        if self.bulk_rosetta_all_qualified and not self.autoresearch_enabled:
            raise ValueError("bulk Rosetta evaluation requires Auto Research")
        diversity_rules = [
            rule
            for rule in self.metric_policy
            if rule.role == "diversity" and rule.metric_name == "sequence_similarity"
        ]
        if len(diversity_rules) > 1:
            raise ValueError("metric_policy admits at most one sequence_similarity rule")
        enabled_plugins = [metric for metric in self.optional_metrics if metric.enabled]
        plugin_names = [metric.name for metric in enabled_plugins]
        if len(plugin_names) != len(set(plugin_names)):
            raise ValueError("optional_metrics cannot enable the same plugin more than once")
        output_to_plugin = {
            output_name: plugin
            for plugin in enabled_plugins
            for output_name in METRIC_PLUGIN_CONTRACTS[plugin.name]["outputs"]
        }
        for rule in self.metric_policy:
            plugin = output_to_plugin.get(rule.metric_name)
            if plugin is None:
                continue
            if plugin.trust == "shadow" and rule.role != "diagnostic":
                raise ValueError(
                    f"shadow metric {rule.metric_name} cannot enter Agent selection"
                )
            if plugin.trust == "soft" and rule.role in {"qualification", "diversity"}:
                raise ValueError(
                    f"soft metric {rule.metric_name} cannot be a hard selection gate"
                )
        return self


class CandidateRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    sequence: str
    sequence_sha256: str
    generation: int
    parent_id: UUID | None
    status: str
    created_at: datetime


class ModelEvidence(BaseModel):
    model_name: str
    model_version: str
    weights_sha256: str
    environment_sha256: str
    parameters: dict[str, Any]
    random_seed: int | None = None
    input_sha256: str
    output_sha256: str | None = None
    artifact_uris: list[str] = Field(default_factory=list)
    out_of_domain: bool = False
    limitations: list[str] = Field(default_factory=list)


class GeneratedPeptide(BaseModel):
    sequence: str
    conditional_nll: float
    conditional_ppl: float
    per_residue_log_probabilities: list[float]
    seed: int


class Boltz2Result(BaseModel):
    confidence_score: float | None = None
    iptm: float | None = None
    pair_iptm: float | None = None
    complex_iplddt: float | None = None
    artifacts: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class AffinityPrediction(BaseModel):
    evaluator: str
    value: float | None = None
    unit: str
    derived_kd_molar: float | None = None
    status: str
    out_of_domain: bool = False
    limitations: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class PocketEvidenceSpec(BaseModel):
    evidence_kind: str
    evidence_grade: str
    source_type: str
    source_uri: str
    source_accession: str | None = None
    source_version: str | None = None
    source_revision_date: datetime | None = None
    retrieved_at: datetime
    chain_ids: list[str] = Field(default_factory=list)
    source_residue_indices: list[int] = Field(default_factory=list)
    target_residue_indices: list[int] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    experimental_method: str | None = None
    resolution_angstrom: float | None = Field(default=None, gt=0)
    mapping: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_grade")
    @classmethod
    def validate_evidence_grade(cls, value: str) -> str:
        if value not in {"A", "B", "C", "D", "U"}:
            raise ValueError("evidence_grade must be one of A, B, C, D, U")
        return value


class TargetPocketSpec(BaseModel):
    key: str
    name: str
    pocket_type: str
    functional_role: str
    status: str
    evidence_grade: str
    evidence_score: float = Field(ge=0, le=1)
    conditioning_priority: str
    conditioning_enabled: bool
    residue_indices: list[int] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[PocketEvidenceSpec] = Field(default_factory=list)

    @field_validator("evidence_grade")
    @classmethod
    def validate_evidence_grade(cls, value: str) -> str:
        if value not in {"A", "B", "C", "D", "U"}:
            raise ValueError("evidence_grade must be one of A, B, C, D, U")
        return value


class PocketCatalogTargetSpec(BaseModel):
    name: str
    organism: str
    accession: str
    sequence: str
    role: str
    source_database: str
    source_uri: str
    source_version: str
    source_retrieved_at: datetime
    source_document_sha256: str | None = None
    pockets: list[TargetPocketSpec]

    @field_validator("sequence")
    @classmethod
    def normalize_sequence(cls, value: str) -> str:
        return TargetSpec.normalize_sequence(value)

    @model_validator(mode="after")
    def validate_residue_numbering(self) -> "PocketCatalogTargetSpec":
        length = len(self.sequence)
        for pocket in self.pockets:
            invalid = [index for index in pocket.residue_indices if index < 1 or index > length]
            if invalid:
                raise ValueError(
                    f"{self.accession}/{pocket.key} has residues outside 1..{length}: {invalid}"
                )
            for evidence in pocket.evidence:
                invalid = [
                    index
                    for index in evidence.target_residue_indices
                    if index < 1 or index > length
                ]
                if invalid:
                    raise ValueError(
                        f"{self.accession}/{pocket.key} evidence has invalid target residues: "
                        f"{invalid}"
                    )
        return self


class PocketCatalogSpec(BaseModel):
    catalog_version: str
    grading_rubric: dict[str, str]
    targets: list[PocketCatalogTargetSpec]
