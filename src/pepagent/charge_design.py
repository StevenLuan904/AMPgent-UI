from __future__ import annotations

import hashlib
import itertools
import json
import math
from typing import TYPE_CHECKING, Literal

from Bio.SeqUtils.ProtParam import ProteinAnalysis
from pydantic import BaseModel, Field, model_validator

from pepagent.developability import (
    CANONICAL_AMINO_ACIDS,
    sequence_developability_metrics,
)

if TYPE_CHECKING:
    from pepagent.v33_preregistration import V33Preregistration

CHARGE_TRANSFORMER_VERSION = "v33-charge-transformer-v2-literature-led"
EISENBERG_HYDROPATHY = {
    "A": 0.62,
    "C": 0.29,
    "D": -0.90,
    "E": -0.74,
    "F": 1.19,
    "G": 0.48,
    "H": -0.40,
    "I": 1.38,
    "K": -1.50,
    "L": 1.06,
    "M": 0.64,
    "N": -0.78,
    "P": 0.12,
    "Q": -0.85,
    "R": -2.53,
    "S": -0.18,
    "T": -0.05,
    "V": 1.08,
    "W": 0.81,
    "Y": 0.26,
}


class ChargeInterventionDose(BaseModel):
    name: Literal["one_positive_residue", "two_positive_residues"]
    edit_count: int = Field(ge=1, le=2)
    expected_formal_charge_delta: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def validate_dose(self) -> ChargeInterventionDose:
        if self.edit_count != self.expected_formal_charge_delta:
            raise ValueError("each neutral-to-K/R edit adds one formal positive charge")
        return self


class ChargeEditContract(BaseModel):
    editable_source_residues: tuple[str, ...] = ("Q", "N", "S", "T")
    introduced_positive_residues: tuple[str, ...] = ("K", "R")
    control_mapping: dict[str, str] = Field(
        default_factory=lambda: {"Q": "N", "N": "Q", "S": "T", "T": "S"}
    )
    forbidden_terminal_positions: bool = True
    maximum_edit_count: int = Field(default=2, ge=1)
    maximum_edit_fraction: float = Field(default=0.20, gt=0, le=1)
    maximum_new_adjacent_kr_run: int = Field(default=3, ge=1)
    maximum_identical_residue_run: int = Field(default=4, ge=1)
    maximum_net_charge_ph7_4: float = 8.0
    maximum_charge_density_ph7_4: float = 0.50


class ChargeArmResult(BaseModel):
    arm: str
    sequence: str
    sequence_sha256: str
    edit_positions_zero_based: list[int]
    substitutions: list[str]
    edit_count: int
    metrics: dict[str, float | int]
    descriptor_deltas_from_parent: dict[str, float]


class ChargeDoseBlock(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    transformer_version: Literal["v33-charge-transformer-v2-literature-led"] = (
        CHARGE_TRANSFORMER_VERSION
    )
    block_id: str
    dose_contract_sha256: str
    edit_contract_sha256: str
    dose_name: str
    reachable: bool
    unreachable_reason: str | None = None
    edit_positions_zero_based: list[int]
    lysine_arm: ChargeArmResult | None = None
    arginine_arm: ChargeArmResult | None = None
    control_arm: ChargeArmResult | None = None


class ChargeParentResult(BaseModel):
    parent_id: str
    parent_sequence: str
    parent_sequence_sha256: str
    baseline: ChargeArmResult
    dose_blocks: dict[str, ChargeDoseBlock]
    all_doses_reachable: bool


class CounterfactualRejection(BaseModel):
    stream_index_zero_based: int
    parent_id: str
    sequence_sha256: str
    reason: str


class CounterfactualParentRecord(ChargeParentResult):
    stream_index_zero_based: int


class CounterfactualCohortResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    transformer_version: Literal["v33-charge-transformer-v2-literature-led"] = (
        CHARGE_TRANSFORMER_VERSION
    )
    input_count: int
    scanned_count: int
    selection_stopped_after_stream_index_zero_based: int | None
    selected_count: int
    maximum_parent_count: int
    shortfall_count: int
    selected_parents: list[CounterfactualParentRecord]
    rejections: list[CounterfactualRejection]


def charge_components_from_preregistration(
    manifest: V33Preregistration,
) -> tuple[dict[str, ChargeInterventionDose], ChargeEditContract]:
    doses = {
        name: ChargeInterventionDose(name=name, **raw)
        for name, raw in manifest.charge_definition["intervention_doses"].items()
    }
    raw = manifest.edit_contract
    guards = manifest.charge_definition["operational_guards_not_biological_targets"]
    contract = ChargeEditContract(
        editable_source_residues=tuple(raw["editable_source_residues"]),
        introduced_positive_residues=tuple(raw["introduced_positive_residues"]),
        control_mapping=raw["charge_preserving_control_mapping"],
        forbidden_terminal_positions=raw["forbidden_terminal_positions"],
        maximum_edit_count=raw["maximum_edit_count"],
        maximum_edit_fraction=raw["maximum_edit_fraction"],
        maximum_new_adjacent_kr_run=raw["maximum_new_adjacent_kr_run"],
        maximum_identical_residue_run=raw["maximum_identical_residue_run"],
        maximum_net_charge_ph7_4=guards["maximum_net_charge_ph7_4"],
        maximum_charge_density_ph7_4=guards["maximum_charge_density_ph7_4"],
    )
    return doses, contract


def _normalize_sequence(sequence: str) -> str:
    normalized = "".join(sequence.split()).upper()
    if not normalized:
        raise ValueError("peptide sequence cannot be empty")
    invalid = sorted(set(normalized) - CANONICAL_AMINO_ACIDS)
    if invalid:
        raise ValueError(f"non-canonical amino acids: {''.join(invalid)}")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mean_eisenberg(sequence: str) -> float:
    return sum(EISENBERG_HYDROPATHY[residue] for residue in sequence) / len(sequence)


def _hydrophobic_moment(sequence: str, angle_degrees: float = 100.0) -> float:
    angle = math.radians(angle_degrees)
    x = sum(
        EISENBERG_HYDROPATHY[residue] * math.cos(index * angle)
        for index, residue in enumerate(sequence)
    )
    y = sum(
        EISENBERG_HYDROPATHY[residue] * math.sin(index * angle)
        for index, residue in enumerate(sequence)
    )
    return math.hypot(x, y) / len(sequence)


def _charge(sequence: str) -> float:
    return float(ProteinAnalysis(sequence).charge_at_pH(7.4))


def _maximum_run(sequence: str, residues: frozenset[str]) -> int:
    maximum = 0
    current = 0
    for residue in sequence:
        current = current + 1 if residue in residues else 0
        maximum = max(maximum, current)
    return maximum


def _maximum_new_kr_run(sequence: str, edited_positions: tuple[int, ...]) -> int:
    edited = set(edited_positions)
    maximum = 0
    start = 0
    while start < len(sequence):
        if sequence[start] not in "KR":
            start += 1
            continue
        end = start
        while end + 1 < len(sequence) and sequence[end + 1] in "KR":
            end += 1
        if any(position in edited for position in range(start, end + 1)):
            maximum = max(maximum, end - start + 1)
        start = end + 1
    return maximum


def _mutate(sequence: str, positions: tuple[int, ...], residues: tuple[str, ...]) -> str:
    output = list(sequence)
    for position, residue in zip(positions, residues, strict=True):
        output[position] = residue
    return "".join(output)


def _labels(
    parent: str, positions: tuple[int, ...], residues: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(
        f"{parent[position]}{position + 1}{residue}"
        for position, residue in zip(positions, residues, strict=True)
    )


def _arm_metrics(sequence: str, formal_charge_delta: int) -> dict[str, float | int]:
    descriptors = sequence_developability_metrics(sequence)
    charge = float(descriptors["net_charge_ph7_4"])
    length = len(sequence)
    return {
        "sequence_length": length,
        "net_charge_ph7_4": charge,
        "charge_density_ph7_4": charge / length,
        "formal_charge_delta_from_parent": formal_charge_delta,
        "lysine_fraction": sequence.count("K") / length,
        "arginine_fraction": sequence.count("R") / length,
        "histidine_fraction": sequence.count("H") / length,
        "acidic_residue_fraction": (sequence.count("D") + sequence.count("E"))
        / length,
        "maximum_cationic_run": _maximum_run(sequence, frozenset("KR")),
        "maximum_identical_residue_run": int(
            descriptors["maximum_identical_residue_run"]
        ),
        "mean_eisenberg_hydropathy": _mean_eisenberg(sequence),
        "hydrophobic_moment_eisenberg": _hydrophobic_moment(sequence),
        "maximum_hydrophobic_run": int(descriptors["maximum_hydrophobic_run"]),
    }


def _arm_result(
    arm: str,
    sequence: str,
    parent_sequence: str,
    parent_metrics: dict[str, float | int],
    positions: tuple[int, ...] = (),
    residues: tuple[str, ...] = (),
    formal_charge_delta: int = 0,
) -> ChargeArmResult:
    metrics = _arm_metrics(sequence, formal_charge_delta)
    deltas = {
        name: float(metrics[name]) - float(parent_metrics[name])
        for name in metrics
        if name != "sequence_length"
    }
    return ChargeArmResult(
        arm=arm,
        sequence=sequence,
        sequence_sha256=_sha256(sequence),
        edit_positions_zero_based=list(positions),
        substitutions=list(_labels(parent_sequence, positions, residues)),
        edit_count=len(positions),
        metrics={name: value for name, value in metrics.items()},
        descriptor_deltas_from_parent=deltas,
    )


def _passes_guards(
    sequence: str, positions: tuple[int, ...], contract: ChargeEditContract
) -> bool:
    charge = _charge(sequence)
    return (
        charge <= contract.maximum_net_charge_ph7_4
        and charge / len(sequence) <= contract.maximum_charge_density_ph7_4
        and _maximum_new_kr_run(sequence, positions)
        <= contract.maximum_new_adjacent_kr_run
        and max(len(list(group)) for _, group in itertools.groupby(sequence))
        <= contract.maximum_identical_residue_run
    )


def _select_positions(
    parent: str, dose: ChargeInterventionDose, contract: ChargeEditContract
) -> tuple[int, ...] | None:
    first = 1 if contract.forbidden_terminal_positions else 0
    last = len(parent) - 1 if contract.forbidden_terminal_positions else len(parent)
    editable = tuple(
        position
        for position in range(first, last)
        if parent[position] in contract.editable_source_residues
    )
    maximum_edits = min(
        contract.maximum_edit_count,
        math.floor(len(parent) * contract.maximum_edit_fraction),
        len(editable),
    )
    if dose.edit_count > maximum_edits:
        return None
    parent_moment = _hydrophobic_moment(parent)
    parent_hydropathy = _mean_eisenberg(parent)
    ranked: list[tuple[tuple[float | int | tuple[int, ...], ...], tuple[int, ...]]] = []
    for positions in itertools.combinations(editable, dose.edit_count):
        lysine = _mutate(parent, positions, ("K",) * dose.edit_count)
        arginine = _mutate(parent, positions, ("R",) * dose.edit_count)
        if not all(_passes_guards(item, positions, contract) for item in (lysine, arginine)):
            continue
        key = (
            max(
                abs(_hydrophobic_moment(item) - parent_moment)
                for item in (lysine, arginine)
            ),
            max(
                abs(_mean_eisenberg(item) - parent_hydropathy)
                for item in (lysine, arginine)
            ),
            max(_maximum_run(item, frozenset("KR")) for item in (lysine, arginine)),
            positions,
        )
        ranked.append((key, positions))
    return min(ranked, default=((), None))[1]


def build_charge_parent_result(
    *,
    parent_id: str,
    parent_sequence: str,
    doses: dict[str, ChargeInterventionDose],
    contract: ChargeEditContract,
) -> ChargeParentResult:
    parent = _normalize_sequence(parent_sequence)
    parent_metrics = _arm_metrics(parent, 0)
    baseline = _arm_result("baseline_unedited", parent, parent, parent_metrics)
    dose_blocks: dict[str, ChargeDoseBlock] = {}
    edit_contract_json = json.dumps(
        contract.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    for dose_name, dose in sorted(doses.items()):
        dose_json = json.dumps(
            dose.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        dose_sha = _sha256(dose_json)
        edit_sha = _sha256(edit_contract_json)
        block_id = _sha256(
            json.dumps(
                {
                    "parent_sequence_sha256": _sha256(parent),
                    "dose_contract_sha256": dose_sha,
                    "edit_contract_sha256": edit_sha,
                    "transformer_version": CHARGE_TRANSFORMER_VERSION,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        positions = _select_positions(parent, dose, contract)
        if positions is None:
            dose_blocks[dose_name] = ChargeDoseBlock(
                block_id=block_id,
                dose_contract_sha256=dose_sha,
                edit_contract_sha256=edit_sha,
                dose_name=dose_name,
                reachable=False,
                unreachable_reason="no_matched_K_R_position_set_passes_preregistered_guards",
                edit_positions_zero_based=[],
            )
            continue
        lysine_residues = ("K",) * dose.edit_count
        arginine_residues = ("R",) * dose.edit_count
        control_residues = tuple(contract.control_mapping[parent[p]] for p in positions)
        dose_blocks[dose_name] = ChargeDoseBlock(
            block_id=block_id,
            dose_contract_sha256=dose_sha,
            edit_contract_sha256=edit_sha,
            dose_name=dose_name,
            reachable=True,
            edit_positions_zero_based=list(positions),
            lysine_arm=_arm_result(
                f"lysine_{'one' if dose.edit_count == 1 else 'two'}",
                _mutate(parent, positions, lysine_residues),
                parent,
                parent_metrics,
                positions,
                lysine_residues,
                dose.expected_formal_charge_delta,
            ),
            arginine_arm=_arm_result(
                f"arginine_{'one' if dose.edit_count == 1 else 'two'}",
                _mutate(parent, positions, arginine_residues),
                parent,
                parent_metrics,
                positions,
                arginine_residues,
                dose.expected_formal_charge_delta,
            ),
            control_arm=_arm_result(
                f"{'one' if dose.edit_count == 1 else 'two'}_charge_preserving_control",
                _mutate(parent, positions, control_residues),
                parent,
                parent_metrics,
                positions,
                control_residues,
                0,
            ),
        )
    return ChargeParentResult(
        parent_id=parent_id,
        parent_sequence=parent,
        parent_sequence_sha256=_sha256(parent),
        baseline=baseline,
        dose_blocks=dose_blocks,
        all_doses_reachable=all(block.reachable for block in dose_blocks.values()),
    )


def build_charge_counterfactual_cohort(
    *,
    parents_in_stream_order: list[dict[str, str]],
    doses: dict[str, ChargeInterventionDose],
    contract: ChargeEditContract,
    maximum_parent_count: int = 200,
    minimum_length: int = 10,
    maximum_length: int = 25,
    minimum_editable_positions: int = 2,
) -> CounterfactualCohortResult:
    selected: list[CounterfactualParentRecord] = []
    rejections: list[CounterfactualRejection] = []
    seen_ids: set[str] = set()
    seen_sequences: set[str] = set()
    last_scanned_index: int | None = None
    for index, raw in enumerate(parents_in_stream_order):
        last_scanned_index = index
        parent_id = str(raw["id"])
        raw_sequence = str(raw["sequence"])
        raw_digest = _sha256("".join(raw_sequence.split()).upper())

        def reject(
            reason: str,
            *,
            rejection_index: int = index,
            rejection_parent_id: str = parent_id,
            rejection_digest: str = raw_digest,
        ) -> None:
            rejections.append(
                CounterfactualRejection(
                    stream_index_zero_based=rejection_index,
                    parent_id=rejection_parent_id,
                    sequence_sha256=rejection_digest,
                    reason=reason,
                )
            )

        if parent_id in seen_ids:
            reject("duplicate_parent_id")
            continue
        seen_ids.add(parent_id)
        try:
            sequence = _normalize_sequence(raw_sequence)
        except ValueError:
            reject("invalid_or_noncanonical_sequence")
            continue
        if sequence in seen_sequences:
            reject("duplicate_sequence")
            continue
        seen_sequences.add(sequence)
        if not minimum_length <= len(sequence) <= maximum_length:
            reject("length_outside_preregistered_range")
            continue
        internal = sequence[1:-1] if contract.forbidden_terminal_positions else sequence
        if sum(residue in contract.editable_source_residues for residue in internal) < (
            minimum_editable_positions
        ):
            reject("insufficient_neutral_editable_positions")
            continue
        result = build_charge_parent_result(
            parent_id=parent_id,
            parent_sequence=sequence,
            doses=doses,
            contract=contract,
        )
        if not result.all_doses_reachable:
            reject("all_charge_dose_identity_arms_not_reachable")
            continue
        selected.append(
            CounterfactualParentRecord(
                **result.model_dump(), stream_index_zero_based=index
            )
        )
        if len(selected) == maximum_parent_count:
            break
    return CounterfactualCohortResult(
        input_count=len(parents_in_stream_order),
        scanned_count=0 if last_scanned_index is None else last_scanned_index + 1,
        selection_stopped_after_stream_index_zero_based=last_scanned_index,
        selected_count=len(selected),
        maximum_parent_count=maximum_parent_count,
        shortfall_count=maximum_parent_count - len(selected),
        selected_parents=selected,
        rejections=rejections,
    )
