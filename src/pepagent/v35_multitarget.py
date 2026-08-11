from __future__ import annotations

import json
import math
from functools import partial
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_bytes

SCHEMA_VERSION = "v35.target-qualification-replay.1"
SELECTION_METHOD = "hard_gate_then_anchor_aware_maximin_v1"
FORBIDDEN_SELECTION_KEY_TOKENS = {
    "amp_score",
    "mic",
    "toxicity",
    "hemolysis",
    "boltz",
    "pair_iptm",
    "rosetta",
    "pepshot",
    "generated_peptide",
    "expected_success",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetAuditItem(StrictModel):
    shortlist_order: int = Field(gt=0)
    target_key: str = Field(min_length=1)
    organism_and_strain: str = Field(min_length=1)
    sequence_accession: str = Field(min_length=1)
    sequence_entry_version: str = Field(min_length=1)
    sequence_admission_basis: Literal[
        "UniProtKB_reviewed",
        "unreviewed_with_explicit_evidence_and_manual_mapping",
    ]
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_source_type: Literal[
        "experimental_exact_target",
        "experimental_close_homolog",
        "predicted_hypothesis_only",
        "none",
    ]
    coordinate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    structure_validation_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    sequence_structure_mapping_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    primary_pocket_grade: Literal["A", "B", "C", "D"] | None
    primary_pocket_definition_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    wrong_pocket_definition_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    audit_status: Literal["qualified_primary", "rejected"]
    rejection_reasons: list[str]
    diversity_vector: list[float] | None

    @model_validator(mode="after")
    def validate_audit_outcome(self) -> TargetAuditItem:
        if self.audit_status == "rejected":
            if not self.rejection_reasons:
                raise ValueError("rejected target must preserve at least one rejection reason")
            return self
        if self.rejection_reasons:
            raise ValueError("qualified target cannot carry a rejection reason")
        if self.primary_pocket_grade not in {"A", "B"}:
            raise ValueError("primary target requires pocket evidence grade A or B")
        if self.structure_source_type not in {
            "experimental_exact_target",
            "experimental_close_homolog",
        }:
            raise ValueError("primary target requires an experimentally anchored structure")
        required = {
            "coordinate_sha256": self.coordinate_sha256,
            "structure_validation_sha256": self.structure_validation_sha256,
            "sequence_structure_mapping_sha256": self.sequence_structure_mapping_sha256,
            "primary_pocket_definition_sha256": self.primary_pocket_definition_sha256,
            "wrong_pocket_definition_sha256": self.wrong_pocket_definition_sha256,
        }
        missing = sorted(name for name, value in required.items() if value is None)
        if missing:
            raise ValueError(f"qualified target is missing evidence: {missing}")
        if not self.diversity_vector or any(
            not math.isfinite(value) for value in self.diversity_vector
        ):
            raise ValueError("qualified target requires a finite diversity vector")
        return self

    def artifact_digests(self) -> set[str]:
        values = {
            self.sequence_artifact_sha256,
            self.source_manifest_sha256,
            self.feature_evidence_sha256,
            self.coordinate_sha256,
            self.structure_validation_sha256,
            self.sequence_structure_mapping_sha256,
            self.primary_pocket_definition_sha256,
            self.wrong_pocket_definition_sha256,
        }
        return {value for value in values if value is not None}


class TargetQualificationSnapshot(StrictModel):
    schema_version: Literal["v35.target-qualification-replay.1"]
    audit_scope_id: str = Field(min_length=1)
    target_names_selected_before_audit: Literal[False]
    peptide_or_structure_outcomes_used_for_selection: Literal[False]
    target_agnostic_amp_lane_retained: Literal[True]
    acea_anchor_vector: list[float]
    requested_new_target_count: int = Field(ge=3, le=5)
    shortlist: list[TargetAuditItem] = Field(min_length=8)
    selected_target_keys: list[str]
    selection_method: Literal["hard_gate_then_anchor_aware_maximin_v1"]
    selection_witness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_snapshot(self) -> TargetQualificationSnapshot:
        orders = [item.shortlist_order for item in self.shortlist]
        if orders != list(range(1, len(self.shortlist) + 1)):
            raise ValueError("target shortlist order must be complete and contiguous")
        keys = [item.target_key for item in self.shortlist]
        if len(keys) != len(set(keys)):
            raise ValueError("target shortlist identities must be unique")
        if len(self.selected_target_keys) != self.requested_new_target_count:
            raise ValueError("selected target count differs from the frozen panel size")
        qualified = [
            item for item in self.shortlist if item.audit_status == "qualified_primary"
        ]
        if len(qualified) < self.requested_new_target_count:
            raise ValueError("too few qualified targets for the frozen panel size")
        dimensions = {len(item.diversity_vector or []) for item in qualified}
        if len(dimensions) != 1 or dimensions != {len(self.acea_anchor_vector)}:
            raise ValueError("target diversity vectors do not match the AceA anchor")
        if not self.acea_anchor_vector or any(
            not math.isfinite(value) for value in self.acea_anchor_vector
        ):
            raise ValueError("AceA anchor diversity vector must be finite")
        return self


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


def _maximin_priority(
    item: TargetAuditItem, *, references: tuple[list[float], ...]
) -> tuple[float, int, str]:
    vector = item.diversity_vector or []
    minimum_distance = min(_distance(vector, reference) for reference in references)
    return (-minimum_distance, item.shortlist_order, item.target_key)


def deterministic_panel_selection(
    shortlist: list[TargetAuditItem],
    *,
    acea_anchor_vector: list[float],
    panel_size: int,
) -> list[str]:
    eligible = [item for item in shortlist if item.audit_status == "qualified_primary"]
    if len(eligible) < panel_size:
        raise ValueError("too few qualified targets for deterministic panel selection")
    selected: list[TargetAuditItem] = []
    remaining = list(eligible)
    while len(selected) < panel_size:
        references = tuple(
            [acea_anchor_vector, *(item.diversity_vector or [] for item in selected)]
        )
        chosen = min(remaining, key=partial(_maximin_priority, references=references))
        selected.append(chosen)
        remaining.remove(chosen)
    return [item.target_key for item in selected]


def build_selection_witness(snapshot: TargetQualificationSnapshot) -> dict[str, Any]:
    eligible = [
        item for item in snapshot.shortlist if item.audit_status == "qualified_primary"
    ]
    selected = deterministic_panel_selection(
        snapshot.shortlist,
        acea_anchor_vector=snapshot.acea_anchor_vector,
        panel_size=snapshot.requested_new_target_count,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "selection_method": SELECTION_METHOD,
        "audit_scope_id": snapshot.audit_scope_id,
        "shortlist_target_keys_in_order": [item.target_key for item in snapshot.shortlist],
        "qualified_target_keys_in_order": [item.target_key for item in eligible],
        "rejected_target_keys_in_order": [
            item.target_key for item in snapshot.shortlist if item.audit_status == "rejected"
        ],
        "requested_new_target_count": snapshot.requested_new_target_count,
        "acea_anchor_vector": snapshot.acea_anchor_vector,
        "selected_target_keys": selected,
        "selection_inputs": "source_mapping_pocket_and_preregistered_diversity_only",
        "peptide_outcomes_used": False,
    }


def _reject_forbidden_selection_keys(payload: Any, path: str = "snapshot") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = key.lower()
            if any(token in normalized for token in FORBIDDEN_SELECTION_KEY_TOKENS):
                raise ValueError(
                    "forbidden peptide/tool outcome entered target selection: "
                    f"{path}.{key}"
                )
            _reject_forbidden_selection_keys(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _reject_forbidden_selection_keys(value, f"{path}[{index}]")


def verify_target_qualification_snapshot(
    snapshot_payload: dict[str, Any], artifact_payloads: dict[str, bytes]
) -> dict[str, Any]:
    _reject_forbidden_selection_keys(snapshot_payload)
    snapshot = TargetQualificationSnapshot.model_validate(snapshot_payload)
    for item in snapshot.shortlist:
        sequence = artifact_payloads.get(item.sequence_artifact_sha256)
        if sequence is None or sha256_bytes(sequence) != item.sequence_artifact_sha256:
            raise ValueError(f"sequence artifact missing or corrupt: {item.target_key}")
        if sha256_bytes(sequence) != item.sequence_sha256:
            raise ValueError(f"sequence identity differs from immutable bytes: {item.target_key}")
        for digest in item.artifact_digests():
            payload = artifact_payloads.get(digest)
            if payload is None or sha256_bytes(payload) != digest:
                raise ValueError(f"target evidence artifact missing or corrupt: {item.target_key}")
    expected_selection = deterministic_panel_selection(
        snapshot.shortlist,
        acea_anchor_vector=snapshot.acea_anchor_vector,
        panel_size=snapshot.requested_new_target_count,
    )
    if snapshot.selected_target_keys != expected_selection:
        raise ValueError("stored target panel differs from deterministic hard-gate maximin replay")
    witness_bytes = artifact_payloads.get(snapshot.selection_witness_sha256)
    if witness_bytes is None or sha256_bytes(witness_bytes) != snapshot.selection_witness_sha256:
        raise ValueError("target panel selection witness is missing or corrupt")
    try:
        witness = json.loads(witness_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("target panel selection witness is not valid JSON") from error
    if witness != build_selection_witness(snapshot):
        raise ValueError("target panel selection witness differs from replay")
    return {
        "schema_version": SCHEMA_VERSION,
        "exact_replay": True,
        "shortlist_count": len(snapshot.shortlist),
        "qualified_count": sum(
            item.audit_status == "qualified_primary" for item in snapshot.shortlist
        ),
        "rejected_count": sum(item.audit_status == "rejected" for item in snapshot.shortlist),
        "selected_target_keys": snapshot.selected_target_keys,
        "candidate_count": 0,
        "evaluation_count": 0,
        "target_names_selected": True,
        "panel_execution_authorized": False,
        "generalization_evaluated": False,
    }


def load_v35_framework(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload.get("scope", {}).get("target_selection_authorized") is not False:
        raise ValueError("v35 target selection is not authorized")
    if payload.get("selection_separation", {}).get("minimum_new_target_shortlist_size") != 8:
        raise ValueError("v35 minimum target shortlist size drifted")
    if payload.get("pocket_evidence_grades", {}).get(
        "primary_panel_requires_grade_A_or_B"
    ) is not True:
        raise ValueError("v35 primary pocket evidence gate drifted")
    return payload
