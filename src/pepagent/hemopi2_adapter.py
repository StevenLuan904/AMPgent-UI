from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import pickle
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from pepagent.archive_audit import scan_pickle_opcodes

STANDARD_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
AMINO_ACID_ORDER = "ACDEFGHIKLMNPQRSTVWY"
MOLECULAR_WEIGHT_KDA = {
    "A": 0.089,
    "R": 0.174,
    "N": 0.132,
    "D": 0.133,
    "C": 0.121,
    "E": 0.147,
    "Q": 0.146,
    "G": 0.075,
    "H": 0.155,
    "I": 0.131,
    "L": 0.131,
    "K": 0.146,
    "M": 0.150,
    "F": 0.165,
    "P": 0.115,
    "S": 0.105,
    "T": 0.119,
    "W": 0.204,
    "Y": 0.181,
    "V": 0.117,
}

HEMOPI2_PICKLE_GLOBALS = frozenset(
    {
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
)


def validate_sequences(sequences: list[str]) -> list[str]:
    if not sequences:
        raise ValueError("at least one smoke sequence is required")
    validated: list[str] = []
    for index, raw in enumerate(sequences):
        if not isinstance(raw, str):
            raise TypeError(f"sequence {index} must be a string")
        sequence = raw.strip().upper()
        if not sequence:
            raise ValueError(f"sequence {index} is empty")
        invalid = sorted(set(sequence) - STANDARD_AMINO_ACIDS)
        if invalid:
            raise ValueError(f"sequence {index} has invalid residues: {''.join(invalid)}")
        validated.append(sequence)
    return validated


def _rounded(value: float, digits: int) -> float:
    return float(f"{value:.{digits}f}")


def compute_basic_feature_block(sequences: list[str]) -> tuple[np.ndarray, list[str]]:
    """Compute the audited, file-free MW/length/AAC/DPC1 block."""

    validated = validate_sequences(sequences)
    if any(len(sequence) < 2 for sequence in validated):
        raise ValueError("DPC1 requires every sequence to contain at least two residues")
    feature_names = ["Molecular Weight (kDa)", "length"]
    feature_names.extend(f"AAC_{residue}" for residue in AMINO_ACID_ORDER)
    feature_names.extend(
        f"DPC1_{left}{right}"
        for left in AMINO_ACID_ORDER
        for right in AMINO_ACID_ORDER
    )
    rows: list[list[float]] = []
    for sequence in validated:
        counts = Counter(sequence)
        molecular_weight = sum(MOLECULAR_WEIGHT_KDA[aa] for aa in sequence)
        molecular_weight -= 0.018 * (len(sequence) - 1)
        row = [_rounded(molecular_weight, 3), float(len(sequence))]
        row.extend(
            _rounded(counts[residue] / len(sequence) * 100.0, 2)
            for residue in AMINO_ACID_ORDER
        )
        dipeptides = Counter(
            sequence[index : index + 2] for index in range(len(sequence) - 1)
        )
        denominator = len(sequence) - 1
        row.extend(
            _rounded(dipeptides[left + right] / denominator * 100.0, 2)
            for left in AMINO_ACID_ORDER
            for right in AMINO_ACID_ORDER
        )
        rows.append(row)
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 422) or not np.isfinite(values).all():
        raise ValueError("basic feature block failed its fixed shape or finiteness contract")
    return values, feature_names


def compute_entropy_feature_block(
    sequences: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Compute residue-level entropy followed by whole-sequence entropy."""

    validated = validate_sequences(sequences)
    feature_names = [f"SER_{residue}" for residue in AMINO_ACID_ORDER] + ["SEP"]
    rows: list[list[float]] = []
    for sequence in validated:
        counts = Counter(sequence)
        length = len(sequence)
        residue_entropy = []
        for residue in AMINO_ACID_ORDER:
            frequency = counts[residue] / length
            value = 0.0 if frequency == 0.0 else frequency * math.log2(frequency)
            residue_entropy.append(round(value, 3))
        whole_entropy = -sum(
            (count / length) * math.log2(count / length) for count in counts.values()
        )
        rows.append([*residue_entropy, round(whole_entropy, 3)])
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 21) or not np.isfinite(values).all():
        raise ValueError("entropy feature block failed its fixed shape or finiteness contract")
    return values, feature_names


def _read_numeric_csv(path: Path, *, skip_header: bool = False) -> list[list[float]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if skip_header:
        rows = rows[1:]
    try:
        return [[float(value) for value in row] for row in rows if row]
    except ValueError as exc:
        raise ValueError(f"non-numeric reference table: {path.name}") from exc


def _repeat_index(sequence: str, residue: str, *, cursor: int) -> tuple[float, int]:
    cumulative: list[int] = []
    count = 0
    for item in sequence:
        if item == residue:
            count += 1
            cumulative.append(count)
        else:
            count = 0
    for index in range(cursor, len(cumulative) - 1):
        if cumulative[index] < cumulative[index + 1]:
            cumulative[index] = 0
    retained = [value for value in cumulative if value != 0]
    denominator = sum(retained)
    value = (
        0.0
        if denominator == 0
        else sum(item * item for item in retained) / denominator
    )
    return value, max(cursor, len(cumulative))


def compute_reference_table_feature_block(
    sequences: list[str], data_root: Path
) -> tuple[np.ndarray, list[str]]:
    """Compute ATC/BTC/PCP/RRI/PRI/DDR without importing upstream code."""

    validated = validate_sequences(sequences)
    atom_path = data_root / "atom.csv"
    bond_path = data_root / "bonds.csv"
    pcp_path = data_root / "PhysicoChemical.csv"
    with atom_path.open(encoding="utf-8", newline="") as handle:
        atom_rows = list(csv.reader(handle))
    if len(atom_rows) != 20 or any(len(row) != 2 for row in atom_rows):
        raise ValueError("atom reference table shape drifted")
    atom_counts = {
        residue.strip(): [formula.count(element) for element in "CHNOS"]
        for residue, formula in atom_rows
    }
    with bond_path.open(encoding="utf-8", newline="") as handle:
        bond_rows = list(csv.reader(handle))
    if len(bond_rows) != 21 or bond_rows[0] != [
        "Name",
        "nBonds_tot",
        "Hydrogen_bonds",
        "nBondsS",
        "nBondsD",
    ]:
        raise ValueError("bond reference table shape or header drifted")
    bond_values = {
        row[0]: [float(value) for value in row[1:]] for row in bond_rows[1:]
    }
    pcp = np.asarray(_read_numeric_csv(pcp_path), dtype=np.float64)
    if pcp.shape != (30, 20) or not np.isfinite(pcp).all():
        raise ValueError("physicochemical reference table must be finite 30x20")

    pcp_names = [
        "PC",
        "NC",
        "NE",
        "PO",
        "NP",
        "AL",
        "CY",
        "AR",
        "AC",
        "BS",
        "NE_pH",
        "HB",
        "HL",
        "NT",
        "HX",
        "SC",
        "SS_HE",
        "SS_ST",
        "SS_CO",
        "SA_BU",
        "SA_EX",
        "SA_IN",
        "TN",
        "SM",
        "LR",
        "Z1",
        "Z2",
        "Z3",
        "Z4",
        "Z5",
    ]
    feature_names = [f"ATC_{element}" for element in "CHNOS"]
    feature_names.extend(["BTC_T", "BTC_H", "BTC_S", "BTC_D"])
    feature_names.extend(f"PCP_{name}" for name in pcp_names)
    feature_names.extend(f"RRI_{residue}" for residue in AMINO_ACID_ORDER)
    feature_names.extend(f"PRI_{name}" for name in pcp_names[:25])
    feature_names.extend(f"DDR_{residue}" for residue in AMINO_ACID_ORDER)

    rows: list[list[float]] = []
    rri_cursor = 0
    residue_index = {residue: index for index, residue in enumerate(AMINO_ACID_ORDER)}
    for sequence in validated:
        atom_totals = np.sum([atom_counts[residue] for residue in sequence], axis=0)
        atom_percent = np.round(atom_totals / atom_totals.sum() * 100.0, 2)
        bonds = np.sum([bond_values[residue] for residue in sequence], axis=0)
        encoded = np.asarray([residue_index[residue] for residue in sequence])
        pcp_means = np.round(pcp[:, encoded].mean(axis=1), 3)
        rri: list[float] = []
        for residue in AMINO_ACID_ORDER:
            repeat_value, rri_cursor = _repeat_index(
                sequence, residue, cursor=rri_cursor
            )
            rri.append(_rounded(repeat_value, 2))
        pri: list[float] = []
        for feature_index in range(25):
            profile = pcp[feature_index, encoded]
            run = numerator = denominator = ones = 0.0
            for index, value in enumerate(profile):
                if value == 0:
                    numerator += run * run
                    denominator += run
                    run = 0.0
                else:
                    run += 1.0
                    ones += 1.0
                if index == len(profile) - 1 and value != 0:
                    numerator += run * run
                    denominator += run
            del denominator
            pri.append(0.0 if ones == 0 else round(numerator / (ones * ones), 2))
        reversed_sequence = sequence[::-1]
        ddr: list[float] = []
        for residue in AMINO_ACID_ORDER:
            positions = [i for i, value in enumerate(sequence) if value == residue]
            reverse_positions = [
                i for i, value in enumerate(reversed_sequence) if value == residue
            ]
            gaps = [positions[i + 1] - positions[i] - 1 for i in range(len(positions) - 1)]
            if positions:
                gaps.insert(0, positions[0])
                gaps.append(reverse_positions[0])
            ddr.append(_rounded(sum(gap * gap for gap in gaps) / (sum(gaps) + 1), 2))
        rows.append(
            [
                *atom_percent.tolist(),
                *bonds.tolist(),
                *pcp_means.tolist(),
                *rri,
                *pri,
                *ddr,
            ]
        )
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 104) or not np.isfinite(values).all():
        raise ValueError("reference-table feature block failed shape or finiteness contract")
    return values, feature_names


@dataclass(frozen=True)
class FeatureMatrixContract:
    feature_count: int
    ordered_feature_names_sha256: str
    first_feature: str
    last_feature: str

    def validate(self, values: np.ndarray, feature_names: list[str]) -> None:
        if values.ndim != 2:
            raise ValueError("feature matrix must be two-dimensional")
        if values.shape[1] != self.feature_count:
            raise ValueError("feature matrix column count drifted")
        if len(feature_names) != self.feature_count:
            raise ValueError("feature-name count drifted")
        if len(set(feature_names)) != len(feature_names):
            raise ValueError("feature names must be unique")
        if feature_names[0] != self.first_feature or feature_names[-1] != self.last_feature:
            raise ValueError("feature boundary names drifted")
        names_payload = ("\n".join(feature_names) + "\n").encode()
        if hashlib.sha256(names_payload).hexdigest() != self.ordered_feature_names_sha256:
            raise ValueError("ordered feature-name digest drifted")
        if not np.issubdtype(values.dtype, np.number):
            raise TypeError("feature matrix must be numeric")
        if not np.isfinite(values).all():
            raise ValueError("feature matrix contains non-finite values")


class RestrictedSklearnUnpickler(pickle.Unpickler):
    def __init__(self, file: BinaryIO, *, allowed_globals: frozenset[str]) -> None:
        super().__init__(file)
        self._allowed_globals = allowed_globals

    def find_class(self, module: str, name: str) -> Any:
        qualified_name = f"{module}.{name}"
        if qualified_name not in self._allowed_globals:
            raise pickle.UnpicklingError(
                f"pickle global is outside the allowlist: {qualified_name}"
            )
        return super().find_class(module, name)


def load_restricted_sklearn_pickle(
    path: Path,
    *,
    expected_sha256: str,
    allowed_globals: frozenset[str] = HEMOPI2_PICKLE_GLOBALS,
) -> Any:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("serialized model SHA-256 mismatch")
    audit = scan_pickle_opcodes(payload)
    if audit["unresolved_stack_global_count"] != 0:
        raise ValueError("serialized model contains unresolved STACK_GLOBAL references")
    observed = frozenset(audit["global_references"])
    if not observed.issubset(allowed_globals):
        raise ValueError("serialized model references globals outside the allowlist")
    return RestrictedSklearnUnpickler(
        io.BytesIO(payload), allowed_globals=allowed_globals
    ).load()


def canonical_smoke_bytes(records: list[dict[str, Any]]) -> bytes:
    if not records:
        raise ValueError("smoke output cannot be empty")
    return (
        "\n".join(
            json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
            for record in records
        )
        + "\n"
    ).encode()
