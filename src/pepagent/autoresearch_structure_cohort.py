from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from pepagent.autoresearch_wetlab_gold import (
    CANONICAL_AMINO_ACIDS,
    TargetStructureQualificationEvidence,
)
from pepagent.domain.schemas import PocketCatalogSpec, PocketCatalogTargetSpec, TargetPocketSpec
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text

TARGET_KEYS = ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
DEFAULT_STRUCTURE_ESCALATION_COUNT = 96
MINIMUM_PEPTIDE_LENGTH = 10
MAXIMUM_PEPTIDE_LENGTH = 30

REQUIRED_STRICT_LIBRARY_COLUMNS = frozenset(
    {
        "activity_model_support_count",
        "candidate_id",
        "display_eligible",
        "family_key_80_80",
        "formal_metric_count",
        "formal_metrics_complete",
        "generator_id",
        "guruprasad_instability_index",
        "guruprasad_instability_ood",
        "macrel_hemolysis_label",
        "maximum_hydrophobic_run",
        "pareto_depth_within_expansion_target",
        "safety_labels_pass",
        "sequence",
        "sequence_sha256",
        "source_result_sha256",
        "target_key",
        "toxinpred3_label",
        "valid_sequence",
    }
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FrozenScoreLibrarySource(FrozenModel):
    schema_version: Literal["ampgent.structure-score-source.1"] = "ampgent.structure-score-source.1"
    bundle_schema_version: str = Field(min_length=1)
    bundle_run_id: str = Field(min_length=1)
    bundle_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_storage_uri: str = Field(min_length=1)
    strict_library_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strict_library_bytes: int = Field(ge=1)
    strict_library_row_count: int = Field(ge=1)
    score_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StructureEscalationCandidate(FrozenModel):
    schema_version: Literal["ampgent.structure-escalation-candidate.1"] = (
        "ampgent.structure-escalation-candidate.1"
    )
    target_key: str = Field(min_length=1)
    structure_rank: int = Field(ge=1)
    selection_front: Literal["activity_consensus", "stability", "pareto_depth"]
    candidate_id: str = Field(min_length=1)
    sequence: str = Field(min_length=MINIMUM_PEPTIDE_LENGTH, max_length=MAXIMUM_PEPTIDE_LENGTH)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    family_key_80_80: str = Field(min_length=1)
    activity_model_support_count: int = Field(ge=2, le=3)
    guruprasad_instability_index: float = Field(lt=50.0)
    guruprasad_instability_ood: Literal[False] = False
    maximum_hydrophobic_run: int = Field(ge=0)
    pareto_depth: int | None = Field(default=None, ge=1)
    generator_id: str = Field(min_length=1)
    source_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strict_library_row_number: int = Field(ge=2)
    strict_library_row_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_candidate(self) -> StructureEscalationCandidate:
        normalized = "".join(self.sequence.split()).upper()
        if normalized != self.sequence:
            raise ValueError("structure cohort sequence is not normalized")
        if set(normalized) - CANONICAL_AMINO_ACIDS:
            raise ValueError("structure cohort contains non-canonical residues")
        if sha256_text(normalized) != self.sequence_sha256:
            raise ValueError("structure cohort sequence SHA-256 drifted")
        if not math.isfinite(self.guruprasad_instability_index):
            raise ValueError("structure cohort instability must be finite")
        return self


class TargetStructureEscalationCohort(FrozenModel):
    schema_version: Literal["ampgent.target-structure-escalation-cohort.1"] = (
        "ampgent.target-structure-escalation-cohort.1"
    )
    target_key: str = Field(min_length=1)
    qualification: TargetStructureQualificationEvidence
    requested_count: int = Field(ge=50)
    eligible_candidate_count: int = Field(ge=0)
    eligible_family_count: int = Field(ge=0)
    excluded_instability_ood_count: int = Field(ge=0)
    selected: tuple[StructureEscalationCandidate, ...]
    no_weighted_total_score: Literal[True] = True

    @model_validator(mode="after")
    def validate_target_cohort(self) -> TargetStructureEscalationCohort:
        if self.qualification.target_key != self.target_key:
            raise ValueError("target qualification crossed structure cohort branches")
        if len(self.selected) != self.requested_count:
            raise ValueError("structure escalation cohort has a target shortfall")
        if [item.structure_rank for item in self.selected] != list(
            range(1, len(self.selected) + 1)
        ):
            raise ValueError("structure escalation ranks are not contiguous")
        if any(item.target_key != self.target_key for item in self.selected):
            raise ValueError("structure escalation candidate crossed target branches")
        if len({item.sequence_sha256 for item in self.selected}) != len(self.selected):
            raise ValueError("structure escalation candidate sequences are not unique")
        if len({item.family_key_80_80 for item in self.selected}) != len(self.selected):
            raise ValueError("structure escalation cohort must use one sequence per 80/80 family")
        return self


class StructureEscalationCohort(FrozenModel):
    schema_version: Literal["ampgent.structure-escalation-cohort.1"] = (
        "ampgent.structure-escalation-cohort.1"
    )
    source: FrozenScoreLibrarySource
    target_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pocket_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    per_target_requested_count: int = Field(ge=50)
    target_cohorts: tuple[TargetStructureEscalationCohort, ...]
    selection_method: Literal["round_robin_activity_consensus_stability_pareto_family_unique"] = (
        "round_robin_activity_consensus_stability_pareto_family_unique"
    )
    instability_ood_excluded: Literal[True] = True
    exact_sequence_and_family_uniqueness_required: Literal[True] = True
    no_weighted_total_score: Literal[True] = True
    no_binding_or_affinity_claim: Literal[True] = True
    minimum_rosetta_decoys_per_completed_candidate: Literal[200] = 200

    @model_validator(mode="after")
    def validate_six_target_cohort(self) -> StructureEscalationCohort:
        if tuple(item.target_key for item in self.target_cohorts) != TARGET_KEYS:
            raise ValueError("structure escalation cohort must preserve six-target order")
        if any(
            item.requested_count != self.per_target_requested_count for item in self.target_cohorts
        ):
            raise ValueError("per-target structure cohort quota drifted")
        all_sequences = [
            item.sequence_sha256 for cohort in self.target_cohorts for item in cohort.selected
        ]
        if len(all_sequences) != len(set(all_sequences)):
            raise ValueError("one peptide sequence cannot cross target structure cohorts")
        return self

    @computed_field(return_type=int)
    @property
    def selected_count(self) -> int:
        return sum(len(item.selected) for item in self.target_cohorts)

    @computed_field(return_type=str)
    @property
    def cohort_sha256(self) -> str:
        return sha256_json(
            self.model_dump(
                mode="json",
                exclude={"cohort_sha256", "selected_count"},
                exclude_computed_fields=True,
            )
        )


class FrozenCohortFile(FrozenModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=1)


class StructureEscalationFreezeReceipt(FrozenModel):
    schema_version: Literal["ampgent.structure-escalation-freeze-receipt.1"]
    status: Literal["frozen"]
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_count: int = Field(ge=300)
    per_target_count: int = Field(ge=50)
    files: dict[str, FrozenCohortFile]
    source: FrozenScoreLibrarySource
    no_binding_or_affinity_claim: Literal[True]
    minimum_rosetta_decoys_per_completed_candidate: Literal[200]

    @model_validator(mode="after")
    def validate_files(self) -> StructureEscalationFreezeReceipt:
        required = {
            "structure_escalation_audit.json",
            "structure_escalation_cohort.csv",
            "structure_escalation_cohort.json",
        }
        if set(self.files) != required:
            raise ValueError("structure escalation receipt file set drifted")
        if self.selected_count != self.per_target_count * len(TARGET_KEYS):
            raise ValueError("structure escalation receipt count drifted")
        return self


class _TargetManifestItem(FrozenModel):
    target_key: Literal["acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1"]
    protein_accession: str = Field(min_length=1)
    sequence: str = Field(min_length=1)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_sequence(self) -> _TargetManifestItem:
        normalized = "".join(self.sequence.split()).upper()
        if normalized != self.sequence or set(normalized) - CANONICAL_AMINO_ACIDS:
            raise ValueError("target manifest sequence is not canonical and normalized")
        if sha256_text(normalized) != self.sequence_sha256:
            raise ValueError("target manifest sequence SHA-256 drifted")
        return self


class _TargetManifest(FrozenModel):
    schema_version: Literal["ampgent.target_sequence_manifest.v1"]
    target_count: Literal[6]
    targets: tuple[_TargetManifestItem, ...]

    @model_validator(mode="after")
    def validate_targets(self) -> _TargetManifest:
        if tuple(item.target_key for item in self.targets) != TARGET_KEYS:
            raise ValueError("target manifest must preserve six-target order")
        if len({item.sequence_sha256 for item in self.targets}) != 6:
            raise ValueError("target manifest sequences must be unique")
        return self


def _parse_bool(value: str, *, column: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"invalid boolean in strict library column {column}")


def _normalize_label(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _load_json(path: Path, expected_sha256: str) -> dict[str, Any]:
    if sha256_file(path) != expected_sha256:
        raise ValueError(f"file SHA-256 drifted: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return payload


def _load_yaml(path: Path, expected_sha256: str) -> dict[str, Any]:
    if sha256_file(path) != expected_sha256:
        raise ValueError(f"file SHA-256 drifted: {path.name}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be an object: {path.name}")
    return payload


def _best_pocket(
    target: PocketCatalogTargetSpec,
) -> tuple[TargetPocketSpec, str]:
    admitted = [
        pocket
        for pocket in target.pockets
        if pocket.conditioning_enabled and pocket.evidence_grade in {"A", "B"}
    ]
    if admitted:
        priority = {"primary": 0, "secondary": 1}
        return (
            min(
                admitted,
                key=lambda pocket: (
                    priority.get(pocket.conditioning_priority, 9),
                    0 if pocket.evidence_grade == "A" else 1,
                    -pocket.evidence_score,
                    pocket.key,
                ),
            ),
            "admitted_target_conditioned_relative_ranking",
        )
    exploratory = [
        pocket
        for pocket in target.pockets
        if not pocket.conditioning_enabled and pocket.evidence_grade in {"C", "D", "U"}
    ]
    if not exploratory:
        raise ValueError(f"target {target.accession} has no defensible structure evidence mode")
    grade = {"C": 0, "D": 1, "U": 2}
    return (
        min(
            exploratory,
            key=lambda pocket: (
                grade[pocket.evidence_grade],
                -pocket.evidence_score,
                pocket.key,
            ),
        ),
        "exploratory_low_confidence_relative_ranking",
    )


def build_target_qualifications(
    *,
    target_manifest_payload: dict[str, Any],
    target_manifest_sha256: str,
    pocket_catalog_payload: dict[str, Any],
    pocket_catalog_sha256: str,
) -> dict[str, TargetStructureQualificationEvidence]:
    raw_targets = target_manifest_payload.get("targets")
    if not isinstance(raw_targets, list):
        raise ValueError("target manifest targets must be a list")
    manifest = _TargetManifest.model_validate(
        {
            "schema_version": target_manifest_payload.get("schema_version"),
            "target_count": target_manifest_payload.get("target_count"),
            "targets": [
                {
                    "target_key": item.get("target_key"),
                    "protein_accession": item.get("protein_accession"),
                    "sequence": item.get("sequence"),
                    "sequence_sha256": item.get("sequence_sha256"),
                }
                for item in raw_targets
                if isinstance(item, dict)
            ],
        }
    )
    catalog = PocketCatalogSpec.model_validate(pocket_catalog_payload)
    catalog_by_sequence = {sha256_text(item.sequence): item for item in catalog.targets}
    if len(catalog_by_sequence) != len(catalog.targets):
        raise ValueError("pocket catalog contains duplicate target sequences")
    result: dict[str, TargetStructureQualificationEvidence] = {}
    for item in manifest.targets:
        catalog_target = catalog_by_sequence.get(item.sequence_sha256)
        if catalog_target is None:
            raise ValueError(f"target {item.target_key} has no exact-sequence pocket catalog entry")
        pocket, mode = _best_pocket(catalog_target)
        limitations = {
            "reu_is_not_experimental_affinity",
            "starting_pose_requires_supported_interface_geometry",
            *pocket.limitations,
            *(limitation for evidence in pocket.evidence for limitation in evidence.limitations),
        }
        if mode == "admitted_target_conditioned_relative_ranking":
            limitations.add("relative_computational_evidence_only")
        else:
            limitations.update(
                {
                    "exploratory_structure_ranking_only",
                    "target_interface_mapping_unqualified",
                }
            )
        result[item.target_key] = TargetStructureQualificationEvidence(
            target_key=item.target_key,
            target_sequence_sha256=item.sequence_sha256,
            target_role=catalog_target.role,
            pocket_catalog_version=catalog.catalog_version,
            pocket_catalog_sha256=pocket_catalog_sha256,
            pocket_key=pocket.key,
            pocket_evidence_grade=pocket.evidence_grade,
            pocket_conditioning_enabled=pocket.conditioning_enabled,
            structure_evidence_mode=mode,
            limitations=tuple(sorted(limitations)),
        )
    if tuple(result) != TARGET_KEYS:
        raise ValueError("target qualification set is incomplete")
    if not target_manifest_sha256:
        raise ValueError("target manifest SHA-256 is required")
    return result


def _parse_candidate(
    row: dict[str, str],
    *,
    csv_row_number: int,
) -> StructureEscalationCandidate | None:
    sequence = "".join(row["sequence"].split()).upper()
    if not sequence or set(sequence) - CANONICAL_AMINO_ACIDS:
        raise ValueError(f"invalid sequence at strict-library row {csv_row_number}")
    if sha256_text(sequence) != row["sequence_sha256"]:
        raise ValueError(f"sequence SHA drift at strict-library row {csv_row_number}")
    if int(row["formal_metric_count"]) != 12 or not _parse_bool(
        row["formal_metrics_complete"], column="formal_metrics_complete"
    ):
        raise ValueError(f"formal-12 evidence incomplete at strict-library row {csv_row_number}")
    if not _parse_bool(row["display_eligible"], column="display_eligible"):
        raise ValueError(f"non-strict row entered strict library at row {csv_row_number}")
    if not _parse_bool(row["valid_sequence"], column="valid_sequence"):
        raise ValueError(f"invalid row entered strict library at row {csv_row_number}")
    if not _parse_bool(row["safety_labels_pass"], column="safety_labels_pass"):
        raise ValueError(f"unsafe row entered strict library at row {csv_row_number}")
    instability = float(row["guruprasad_instability_index"])
    if not math.isfinite(instability) or instability >= 50.0:
        raise ValueError(f"instability gate drift at strict-library row {csv_row_number}")
    if _normalize_label(row["toxinpred3_label"]) not in {"non-toxin", "nontoxin"}:
        raise ValueError(f"toxicity gate drift at strict-library row {csv_row_number}")
    if _normalize_label(row["macrel_hemolysis_label"]) != "low":
        raise ValueError(f"hemolysis gate drift at strict-library row {csv_row_number}")
    if row["target_key"] not in TARGET_KEYS:
        raise ValueError(f"unknown target at strict-library row {csv_row_number}")
    support = int(row["activity_model_support_count"])
    instability_ood = _parse_bool(
        row["guruprasad_instability_ood"], column="guruprasad_instability_ood"
    )
    if (
        support < 2
        or instability_ood
        or len(sequence) < MINIMUM_PEPTIDE_LENGTH
        or len(sequence) > MAXIMUM_PEPTIDE_LENGTH
    ):
        return None
    pareto_raw = row["pareto_depth_within_expansion_target"].strip()
    pareto_depth = int(pareto_raw) if pareto_raw else None
    canonical_row = {key: row[key] for key in sorted(row)}
    return StructureEscalationCandidate(
        target_key=row["target_key"],
        structure_rank=1,
        selection_front="activity_consensus",
        candidate_id=row["candidate_id"],
        sequence=sequence,
        sequence_sha256=row["sequence_sha256"],
        family_key_80_80=row["family_key_80_80"],
        activity_model_support_count=support,
        guruprasad_instability_index=instability,
        guruprasad_instability_ood=False,
        maximum_hydrophobic_run=int(float(row["maximum_hydrophobic_run"])),
        pareto_depth=pareto_depth,
        generator_id=row["generator_id"],
        source_result_sha256=row["source_result_sha256"],
        strict_library_row_number=csv_row_number,
        strict_library_row_sha256=sha256_json(canonical_row),
    )


def _round_robin_select(
    rows: Sequence[StructureEscalationCandidate], count: int
) -> tuple[StructureEscalationCandidate, ...]:
    pareto_sentinel = 2**31 - 1
    fronts: dict[str, list[StructureEscalationCandidate]] = {
        "activity_consensus": sorted(
            rows,
            key=lambda row: (
                -row.activity_model_support_count,
                row.guruprasad_instability_index,
                row.pareto_depth if row.pareto_depth is not None else pareto_sentinel,
                row.sequence_sha256,
            ),
        ),
        "stability": sorted(
            rows,
            key=lambda row: (
                row.guruprasad_instability_index,
                -row.activity_model_support_count,
                row.pareto_depth if row.pareto_depth is not None else pareto_sentinel,
                row.sequence_sha256,
            ),
        ),
        "pareto_depth": sorted(
            rows,
            key=lambda row: (
                row.pareto_depth if row.pareto_depth is not None else pareto_sentinel,
                -row.activity_model_support_count,
                row.guruprasad_instability_index,
                row.sequence_sha256,
            ),
        ),
    }
    cursors = {name: 0 for name in fronts}
    selected: list[StructureEscalationCandidate] = []
    selected_sequences: set[str] = set()
    selected_families: set[str] = set()
    while len(selected) < count:
        progress = False
        for name, front in fronts.items():
            while cursors[name] < len(front):
                candidate = front[cursors[name]]
                cursors[name] += 1
                if candidate.sequence_sha256 in selected_sequences:
                    continue
                if candidate.family_key_80_80 in selected_families:
                    continue
                selected.append(
                    candidate.model_copy(
                        update={
                            "structure_rank": len(selected) + 1,
                            "selection_front": name,
                        }
                    )
                )
                selected_sequences.add(candidate.sequence_sha256)
                selected_families.add(candidate.family_key_80_80)
                progress = True
                break
            if len(selected) >= count:
                break
        if not progress:
            break
    if len(selected) != count:
        raise ValueError(f"structure cohort has only {len(selected)} family-unique candidates")
    return tuple(selected)


def freeze_structure_escalation_cohort(
    *,
    strict_library_path: Path,
    strict_library_sha256: str,
    bundle_receipt_path: Path,
    bundle_receipt_sha256: str,
    target_manifest_path: Path,
    target_manifest_sha256: str,
    pocket_catalog_path: Path,
    pocket_catalog_sha256: str,
    per_target_count: int = DEFAULT_STRUCTURE_ESCALATION_COUNT,
) -> StructureEscalationCohort:
    if per_target_count < 50:
        raise ValueError("structure escalation must retain at least 50 candidates per target")
    if sha256_file(strict_library_path) != strict_library_sha256:
        raise ValueError("global strict library SHA-256 drifted")
    bundle = _load_json(bundle_receipt_path, bundle_receipt_sha256)
    strict_ref = bundle.get("global_strict_library")
    if bundle.get("status") != "succeeded" or not isinstance(strict_ref, dict):
        raise ValueError("score-all bundle is not a succeeded global-library receipt")
    if strict_ref.get("sha256") != strict_library_sha256:
        raise ValueError("bundle receipt does not bind the supplied strict library")
    if int(strict_ref.get("bytes", -1)) != strict_library_path.stat().st_size:
        raise ValueError("strict library byte count drifted")
    registry_sha256 = bundle.get("runtime", {}).get("registry_sha256")
    if not isinstance(registry_sha256, str):
        raise ValueError("score-all bundle omits its runtime registry SHA-256")
    manifest_payload = _load_json(target_manifest_path, target_manifest_sha256)
    catalog_payload = _load_yaml(pocket_catalog_path, pocket_catalog_sha256)
    qualifications = build_target_qualifications(
        target_manifest_payload=manifest_payload,
        target_manifest_sha256=target_manifest_sha256,
        pocket_catalog_payload=catalog_payload,
        pocket_catalog_sha256=pocket_catalog_sha256,
    )

    eligible: dict[str, list[StructureEscalationCandidate]] = {target: [] for target in TARGET_KEYS}
    all_candidate_ids: set[str] = set()
    all_sequence_sha256s: set[str] = set()
    ood_counts = {target: 0 for target in TARGET_KEYS}
    row_count = 0
    with strict_library_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not REQUIRED_STRICT_LIBRARY_COLUMNS.issubset(
            reader.fieldnames
        ):
            raise ValueError("global strict library columns are incomplete")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("global strict library contains duplicate columns")
        for csv_row_number, row in enumerate(reader, start=2):
            row_count += 1
            candidate_id = row["candidate_id"]
            sequence_sha256 = row["sequence_sha256"]
            if candidate_id in all_candidate_ids:
                raise ValueError("global strict library candidate IDs are not unique")
            if sequence_sha256 in all_sequence_sha256s:
                raise ValueError("global strict library sequences are not unique")
            all_candidate_ids.add(candidate_id)
            all_sequence_sha256s.add(sequence_sha256)
            if _parse_bool(row["guruprasad_instability_ood"], column="guruprasad_instability_ood"):
                ood_counts[row["target_key"]] += 1
            parsed = _parse_candidate(row, csv_row_number=csv_row_number)
            if parsed is not None:
                eligible[parsed.target_key].append(parsed)
    if row_count < 1:
        raise ValueError("global strict library is empty")

    target_cohorts: list[TargetStructureEscalationCohort] = []
    globally_selected: set[str] = set()
    for target in TARGET_KEYS:
        rows = eligible[target]
        selected = _round_robin_select(rows, per_target_count)
        overlap = globally_selected & {item.sequence_sha256 for item in selected}
        if overlap:
            raise ValueError("selected peptide sequence crossed target branches")
        globally_selected.update(item.sequence_sha256 for item in selected)
        target_cohorts.append(
            TargetStructureEscalationCohort(
                target_key=target,
                qualification=qualifications[target],
                requested_count=per_target_count,
                eligible_candidate_count=len(rows),
                eligible_family_count=len({item.family_key_80_80 for item in rows}),
                excluded_instability_ood_count=ood_counts[target],
                selected=selected,
            )
        )
    return StructureEscalationCohort(
        source=FrozenScoreLibrarySource(
            bundle_schema_version=str(bundle.get("schema_version", "")),
            bundle_run_id=str(bundle.get("run_id", "")),
            bundle_receipt_sha256=bundle_receipt_sha256,
            bundle_storage_uri=str(bundle.get("storage_uri", "")),
            strict_library_sha256=strict_library_sha256,
            strict_library_bytes=strict_library_path.stat().st_size,
            strict_library_row_count=row_count,
            score_registry_sha256=registry_sha256,
        ),
        target_manifest_sha256=target_manifest_sha256,
        pocket_catalog_sha256=pocket_catalog_sha256,
        per_target_requested_count=per_target_count,
        target_cohorts=tuple(target_cohorts),
    )


def iter_cohort_csv_rows(
    cohort: StructureEscalationCohort,
) -> Iterable[dict[str, str | int | float | bool | None]]:
    for target in cohort.target_cohorts:
        for item in target.selected:
            yield {
                "target_key": target.target_key,
                "structure_rank": item.structure_rank,
                "selection_front": item.selection_front,
                "structure_evidence_mode": target.qualification.structure_evidence_mode,
                "pocket_key": target.qualification.pocket_key,
                "pocket_evidence_grade": target.qualification.pocket_evidence_grade,
                "candidate_id": item.candidate_id,
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
                "family_key_80_80": item.family_key_80_80,
                "activity_model_support_count": item.activity_model_support_count,
                "guruprasad_instability_index": item.guruprasad_instability_index,
                "guruprasad_instability_ood": item.guruprasad_instability_ood,
                "maximum_hydrophobic_run": item.maximum_hydrophobic_run,
                "pareto_depth": item.pareto_depth,
                "generator_id": item.generator_id,
                "source_result_sha256": item.source_result_sha256,
                "strict_library_row_number": item.strict_library_row_number,
                "strict_library_row_sha256": item.strict_library_row_sha256,
                "target_qualification_sha256": target.qualification.qualification_sha256,
            }


def load_frozen_structure_escalation_cohort(
    output_dir: Path,
    *,
    receipt_sha256: str,
) -> tuple[StructureEscalationCohort, StructureEscalationFreezeReceipt]:
    output_dir = output_dir.resolve()
    receipt_path = output_dir / "freeze.receipt.json"
    if sha256_file(receipt_path) != receipt_sha256:
        raise ValueError("structure escalation freeze receipt SHA-256 drifted")
    receipt = StructureEscalationFreezeReceipt.model_validate(
        json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    )
    for name, evidence in receipt.files.items():
        path = output_dir / name
        if not path.is_file():
            raise ValueError(f"structure escalation file is missing: {name}")
        if path.stat().st_size != evidence.bytes or sha256_file(path) != evidence.sha256:
            raise ValueError(f"structure escalation file identity drifted: {name}")
    cohort_payload = json.loads(
        (output_dir / "structure_escalation_cohort.json").read_text(encoding="utf-8-sig")
    )
    if not isinstance(cohort_payload, dict):
        raise ValueError("structure escalation cohort JSON root must be an object")
    embedded_sha256 = cohort_payload.pop("cohort_sha256", None)
    embedded_count = cohort_payload.pop("selected_count", None)
    cohort = StructureEscalationCohort.model_validate(cohort_payload)
    if (
        embedded_sha256 != cohort.cohort_sha256
        or receipt.cohort_sha256 != cohort.cohort_sha256
        or output_dir.name != cohort.cohort_sha256
    ):
        raise ValueError("structure escalation cohort content address drifted")
    if embedded_count != cohort.selected_count or receipt.selected_count != cohort.selected_count:
        raise ValueError("structure escalation cohort selected count drifted")
    if receipt.source != cohort.source:
        raise ValueError("structure escalation cohort source receipt drifted")
    return cohort, receipt


__all__ = [
    "DEFAULT_STRUCTURE_ESCALATION_COUNT",
    "FrozenScoreLibrarySource",
    "StructureEscalationCandidate",
    "StructureEscalationCohort",
    "StructureEscalationFreezeReceipt",
    "TargetStructureEscalationCohort",
    "build_target_qualifications",
    "freeze_structure_escalation_cohort",
    "iter_cohort_csv_rows",
    "load_frozen_structure_escalation_cohort",
]
