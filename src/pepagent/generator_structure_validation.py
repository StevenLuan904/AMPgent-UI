from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from pepagent.developability import sequence_developability_metrics
from pepagent.selection import sequence_distance, sequence_similarity

CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
REQUIRED_SOURCE_COLUMNS = frozenset(
    {
        "candidate_id",
        "generator_id",
        "seed",
        "selected_rank",
        "sequence",
        "sequence_sha256",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FrozenGeneratorSourceSpec(BaseModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_row_count: int = Field(ge=1)


class GeneratorQuotaSpec(BaseModel):
    generator_id: Literal["hydramp", "ampgan_v2", "amp_designer"]
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    seeds: list[int] = Field(min_length=1)
    expected_source_rows_per_seed: int = Field(ge=1)
    selected_per_seed: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_quota(self) -> GeneratorQuotaSpec:
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("generator seeds must be unique")
        if self.selected_per_seed > self.expected_source_rows_per_seed:
            raise ValueError("selected_per_seed cannot exceed source rows")
        return self


class StructureScreenSelectionSpec(BaseModel):
    method: Literal["seed_stratified_maximin_levenshtein"]
    first_item_tiebreak: Literal["sequence_sha256_then_source_rank"]
    subsequent_tiebreak: Literal["source_rank_then_sequence_sha256"]
    soft_metric_columns_ignored: bool
    pepmlm_used: bool
    global_sequence_uniqueness_required: bool = True

    @model_validator(mode="after")
    def protect_target_blind_selection(self) -> StructureScreenSelectionSpec:
        if not self.soft_metric_columns_ignored:
            raise ValueError("v31 cohort selection must ignore all soft metric columns")
        if self.pepmlm_used:
            raise ValueError("PepMLM cannot select the v31 structure cohort")
        if not self.global_sequence_uniqueness_required:
            raise ValueError("v31 structure cohort requires globally unique sequences")
        return self


class DescriptorQualificationSpec(BaseModel):
    maximum_identical_residue_run: int = Field(ge=1)
    maximum_hydrophobic_run: int = Field(ge=1)
    minimum_net_charge_ph7_4: float
    maximum_net_charge_ph7_4: float

    @model_validator(mode="after")
    def validate_bounds(self) -> DescriptorQualificationSpec:
        if self.minimum_net_charge_ph7_4 > self.maximum_net_charge_ph7_4:
            raise ValueError("minimum charge cannot exceed maximum charge")
        return self


class GeneratorStructureScreenCompletion(BaseModel):
    cohort_path: str = Field(min_length=1)
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_path: str = Field(min_length=1)
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_count: int = Field(ge=1)


class GeneratorStructureScreenManifest(BaseModel):
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    version: str = Field(min_length=1)
    execution_status: Literal["preregistered", "cohort_frozen", "running", "completed"]
    phase: Literal["balanced_fast_screen"]
    spec_path: str = Field(min_length=1)
    target_accession: str = Field(min_length=1)
    sources: list[FrozenGeneratorSourceSpec] = Field(min_length=1)
    generators: list[GeneratorQuotaSpec] = Field(min_length=2)
    selection: StructureScreenSelectionSpec
    descriptor_qualification: DescriptorQualificationSpec
    output_cohort_path: str = Field(min_length=1)
    output_audit_path: str = Field(min_length=1)
    structure_claim_scope: Literal[
        "same_protocol_relative_target_specific_computational_evidence"
    ]
    no_binding_or_affinity_claim: bool
    frozen_predecessors_unchanged: bool
    completion: GeneratorStructureScreenCompletion | None = None

    @field_validator("sources")
    @classmethod
    def unique_sources(
        cls, value: list[FrozenGeneratorSourceSpec]
    ) -> list[FrozenGeneratorSourceSpec]:
        ids = [item.source_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> GeneratorStructureScreenManifest:
        generator_ids = [item.generator_id for item in self.generators]
        if len(generator_ids) != len(set(generator_ids)):
            raise ValueError("generator_id values must be unique")
        if set(generator_ids) != {"hydramp", "ampgan_v2", "amp_designer"}:
            raise ValueError("v31 fast screen requires the three qualified generator families")
        source_ids = {item.source_id for item in self.sources}
        if any(item.source_id not in source_ids for item in self.generators):
            raise ValueError("generator quota references an unknown source_id")
        if not self.no_binding_or_affinity_claim:
            raise ValueError("v31 must preserve the no-binding/no-affinity claim boundary")
        if not self.frozen_predecessors_unchanged:
            raise ValueError("v31 cannot rewrite v23 or v25")
        if self.execution_status == "preregistered" and self.completion is not None:
            raise ValueError("preregistered manifest cannot contain completion hashes")
        if self.execution_status != "preregistered" and self.completion is None:
            raise ValueError("frozen or later manifest requires completion hashes")
        return self


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base_dir / path).resolve()


def _validate_sequence(row: dict[str, str]) -> None:
    sequence = row["sequence"].strip().upper()
    if not sequence or set(sequence) - CANONICAL_AMINO_ACIDS:
        raise ValueError(f"invalid canonical sequence for {row['candidate_id']}")
    actual = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    if actual != row["sequence_sha256"]:
        raise ValueError(f"sequence SHA mismatch for {row['candidate_id']}")
    row["sequence"] = sequence


def load_frozen_sources(
    manifest: GeneratorStructureScreenManifest, base_dir: Path
) -> dict[str, list[dict[str, str]]]:
    loaded: dict[str, list[dict[str, str]]] = {}
    seen_candidate_ids: set[str] = set()
    for source in manifest.sources:
        path = _resolve(base_dir, source.path)
        if sha256_file(path) != source.sha256:
            raise ValueError(f"source SHA mismatch: {source.source_id}")
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or not REQUIRED_SOURCE_COLUMNS.issubset(
                reader.fieldnames
            ):
                raise ValueError(f"source columns incomplete: {source.source_id}")
            rows = list(reader)
        if len(rows) != source.expected_row_count:
            raise ValueError(
                f"source row count mismatch for {source.source_id}: "
                f"{len(rows)} != {source.expected_row_count}"
            )
        for row in rows:
            _validate_sequence(row)
            if row["candidate_id"] in seen_candidate_ids:
                raise ValueError(f"duplicate source candidate_id: {row['candidate_id']}")
            seen_candidate_ids.add(row["candidate_id"])
        loaded[source.source_id] = rows
    return loaded


def _maximin_select(
    rows: list[dict[str, str]], count: int, globally_selected: set[str]
) -> list[dict[str, str]]:
    available = [row for row in rows if row["sequence"] not in globally_selected]
    if len(available) < count:
        raise ValueError("not enough globally unique source candidates for fixed quota")
    first = min(
        available,
        key=lambda row: (row["sequence_sha256"], int(row["selected_rank"])),
    )
    selected = [first]
    available.remove(first)
    while len(selected) < count:
        chosen = max(
            available,
            key=lambda row: (
                min(
                    sequence_distance(row["sequence"], incumbent["sequence"])
                    / max(len(row["sequence"]), len(incumbent["sequence"]))
                    for incumbent in selected
                ),
                -int(row["selected_rank"]),
                row["sequence_sha256"],
            ),
        )
        selected.append(chosen)
        available.remove(chosen)
    return selected


def _descriptor_violations(
    row: dict[str, str], qualification: DescriptorQualificationSpec
) -> list[str]:
    metrics = sequence_developability_metrics(row["sequence"])
    violations = []
    if (
        int(metrics["maximum_identical_residue_run"])
        > qualification.maximum_identical_residue_run
    ):
        violations.append("maximum_identical_residue_run")
    if int(metrics["maximum_hydrophobic_run"]) > qualification.maximum_hydrophobic_run:
        violations.append("maximum_hydrophobic_run")
    charge = float(metrics["net_charge_ph7_4"])
    if charge < qualification.minimum_net_charge_ph7_4:
        violations.append("minimum_net_charge_ph7_4")
    if charge > qualification.maximum_net_charge_ph7_4:
        violations.append("maximum_net_charge_ph7_4")
    return violations


def freeze_balanced_structure_cohort(
    manifest: GeneratorStructureScreenManifest, base_dir: Path
) -> tuple[list[dict[str, str | int]], dict[str, object]]:
    sources = load_frozen_sources(manifest, base_dir)
    selected_rows: list[dict[str, str | int]] = []
    globally_selected: set[str] = set()
    cell_audit: list[dict[str, object]] = []
    for generator in manifest.generators:
        source_rows = sources[generator.source_id]
        for seed in generator.seeds:
            cell = [
                row
                for row in source_rows
                if row["generator_id"] == generator.generator_id
                and int(row["seed"]) == seed
            ]
            if len(cell) != generator.expected_source_rows_per_seed:
                raise ValueError(
                    f"source cell count mismatch for {generator.generator_id}/{seed}: "
                    f"{len(cell)} != {generator.expected_source_rows_per_seed}"
                )
            rejection_counts: dict[str, int] = {}
            qualified = []
            for row in cell:
                violations = _descriptor_violations(
                    row, manifest.descriptor_qualification
                )
                if not violations:
                    qualified.append(row)
                for violation in violations:
                    rejection_counts[violation] = rejection_counts.get(violation, 0) + 1
            chosen = _maximin_select(
                qualified, generator.selected_per_seed, globally_selected
            )
            for within_seed_rank, row in enumerate(chosen, start=1):
                globally_selected.add(row["sequence"])
                selected_rows.append(
                    {
                        "screening_rank": len(selected_rows) + 1,
                        "generator_id": generator.generator_id,
                        "generator_seed": seed,
                        "within_seed_diversity_rank": within_seed_rank,
                        "source_id": generator.source_id,
                        "source_candidate_id": row["candidate_id"],
                        "source_selected_rank": int(row["selected_rank"]),
                        "sequence": row["sequence"],
                        "sequence_sha256": row["sequence_sha256"],
                    }
                )
            similarities = [
                sequence_similarity(left["sequence"], right["sequence"])
                for index, left in enumerate(chosen)
                for right in chosen[index + 1 :]
            ]
            cell_audit.append(
                {
                    "generator_id": generator.generator_id,
                    "seed": seed,
                    "source_count": len(cell),
                    "descriptor_qualified_count": len(qualified),
                    "descriptor_rejection_counts": rejection_counts,
                    "selected_count": len(chosen),
                    "maximum_within_cell_similarity": max(similarities, default=0.0),
                    "minimum_within_cell_similarity": min(similarities, default=0.0),
                }
            )
    expected = sum(
        len(generator.seeds) * generator.selected_per_seed
        for generator in manifest.generators
    )
    if len(selected_rows) != expected or len(globally_selected) != expected:
        raise ValueError("frozen cohort count or global uniqueness contract failed")
    audit: dict[str, object] = {
        "benchmark_id": manifest.benchmark_id,
        "version": manifest.version,
        "selection_method": manifest.selection.method,
        "soft_metric_columns_ignored": True,
        "pepmlm_used": False,
        "selected_count": len(selected_rows),
        "global_unique_sequence_count": len(globally_selected),
        "cells": cell_audit,
    }
    return selected_rows, audit


COHORT_FIELDNAMES = [
    "screening_rank",
    "generator_id",
    "generator_seed",
    "within_seed_diversity_rank",
    "source_id",
    "source_candidate_id",
    "source_selected_rank",
    "sequence",
    "sequence_sha256",
]


def write_frozen_outputs(
    rows: list[dict[str, str | int]],
    audit: dict[str, object],
    cohort_path: Path,
    audit_path: Path,
) -> tuple[str, str]:
    cohort_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with cohort_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COHORT_FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return sha256_file(cohort_path), sha256_file(audit_path)


def load_frozen_structure_cohort(
    manifest: GeneratorStructureScreenManifest, base_dir: Path
) -> list[dict[str, str]]:
    if manifest.completion is None:
        raise ValueError("frozen cohort completion hashes are missing")
    cohort_path = _resolve(base_dir, manifest.completion.cohort_path)
    audit_path = _resolve(base_dir, manifest.completion.audit_path)
    if sha256_file(cohort_path) != manifest.completion.cohort_sha256:
        raise ValueError("frozen cohort SHA mismatch")
    if sha256_file(audit_path) != manifest.completion.audit_sha256:
        raise ValueError("frozen cohort audit SHA mismatch")
    with cohort_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != COHORT_FIELDNAMES:
            raise ValueError("frozen cohort column order mismatch")
        rows = list(reader)
    if len(rows) != manifest.completion.selected_count:
        raise ValueError("frozen cohort row count mismatch")
    expected_count = sum(
        len(generator.seeds) * generator.selected_per_seed
        for generator in manifest.generators
    )
    if len(rows) != expected_count:
        raise ValueError("frozen cohort does not match preregistered quota")
    expected_ranks = list(range(1, len(rows) + 1))
    if [int(row["screening_rank"]) for row in rows] != expected_ranks:
        raise ValueError("frozen cohort screening ranks are not contiguous and ordered")
    candidate_ids: set[str] = set()
    sequences: set[str] = set()
    for row in rows:
        _validate_sequence(row)
        if row["source_candidate_id"] in candidate_ids:
            raise ValueError("duplicate source candidate ID in frozen cohort")
        if row["sequence"] in sequences:
            raise ValueError("duplicate sequence in frozen cohort")
        candidate_ids.add(row["source_candidate_id"])
        sequences.add(row["sequence"])
    for generator in manifest.generators:
        for seed in generator.seeds:
            cell_count = sum(
                row["generator_id"] == generator.generator_id
                and int(row["generator_seed"]) == seed
                for row in rows
            )
            if cell_count != generator.selected_per_seed:
                raise ValueError(
                    f"frozen cell count mismatch for {generator.generator_id}/{seed}"
                )
    return rows
