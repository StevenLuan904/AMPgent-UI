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


CTC_GROUP = {
    "A": "1",
    "G": "1",
    "V": "1",
    "I": "2",
    "L": "2",
    "F": "2",
    "P": "2",
    "Y": "3",
    "M": "3",
    "T": "3",
    "S": "3",
    "H": "4",
    "N": "4",
    "Q": "4",
    "W": "4",
    "R": "5",
    "K": "5",
    "D": "6",
    "E": "6",
    "C": "7",
}


def compute_conjoint_triad_block(
    sequences: list[str],
) -> tuple[np.ndarray, list[str]]:
    validated = validate_sequences(sequences)
    if any(len(sequence) < 3 for sequence in validated):
        raise ValueError("CTC requires every sequence to contain at least three residues")
    triads = [f"{a}{b}{c}" for a in "1234567" for b in "1234567" for c in "1234567"]
    feature_names = [f"CTC_{triad}" for triad in triads]
    rows: list[list[float]] = []
    for sequence in validated:
        grouped = "".join(CTC_GROUP[residue] for residue in sequence)
        counts = Counter(grouped[index : index + 3] for index in range(len(grouped) - 2))
        raw = [counts[triad] for triad in triads]
        minimum = min(raw)
        maximum = max(raw)
        if maximum == 0:
            raise ValueError("CTC has no observed triad")
        rows.append([round((value - minimum) / maximum, 3) for value in raw])
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 343) or not np.isfinite(values).all():
        raise ValueError("CTC block failed shape or finiteness contract")
    return values, feature_names


def _load_ctd_groups(path: Path) -> list[list[set[str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if len(rows) != 8 or rows[0] != ["attr", "1", "2", "3"]:
        raise ValueError("CeTD attribute table shape or header drifted")
    groups: list[list[set[str]]] = []
    for row in rows[1:]:
        if len(row) != 4:
            raise ValueError("CeTD attribute row shape drifted")
        parsed = [set(value[::2]) for value in row[1:]]
        observed = set.union(*parsed)
        if (
            not observed.issubset(STANDARD_AMINO_ACIDS)
            or len(observed) < 19
            or sum(map(len, parsed)) != len(observed)
        ):
            raise ValueError("CeTD attribute groups must be disjoint standard residues")
        groups.append(parsed)
    return groups


def compute_ctd_block(
    sequences: list[str], attribute_path: Path
) -> tuple[np.ndarray, list[str]]:
    """Reproduce the reference CeTD value order and its published headers."""

    validated = validate_sequences(sequences)
    groups = _load_ctd_groups(attribute_path)
    attributes = ["HB", "VW", "PO", "PZ", "CH", "SS", "SA"]
    composition_names = [
        f"CeTD_{attribute}{group}"
        for attribute in attributes
        for group in range(1, 4)
    ]
    pairs = [f"{left}{right}" for left in range(1, 4) for right in range(1, 4)]
    transition_names = [
        f"CeTD_{pair}_{attribute}" for pair in pairs for attribute in attributes
    ]
    percentiles = ["0_p", "25_p", "50_p", "75_p", "100_p"]
    distribution_names = [
        f"CeTD_{percentile}_{attribute}{group}"
        for group in range(1, 4)
        for attribute in attributes
        for percentile in percentiles
    ]
    feature_names = composition_names + transition_names + distribution_names
    rows: list[list[float]] = []
    for sequence in validated:
        encoded_by_attribute: list[list[int]] = []
        for attribute_groups in groups:
            mapping = {
                residue: group_index
                for group_index, residues in enumerate(attribute_groups, start=1)
                for residue in residues
            }
            encoded = [mapping[residue] for residue in sequence if residue in mapping]
            if not encoded:
                raise ValueError("CeTD attribute has no mapped residues for a sequence")
            encoded_by_attribute.append(encoded)
        composition: list[float] = []
        for encoded in encoded_by_attribute:
            composition.extend(
                _rounded(encoded.count(group) / len(encoded) * 100.0, 2)
                for group in range(1, 4)
            )
        transitions: list[float] = []
        for encoded in encoded_by_attribute:
            adjacent = Counter(zip(encoded, encoded[1:], strict=False))
            transitions.extend(
                float(adjacent[(left, right)])
                for left in range(1, 4)
                for right in range(1, 4)
            )
        distribution: list[float] = []
        for encoded in encoded_by_attribute:
            for group in range(1, 4):
                count = encoded.count(group)
                distribution.extend(
                    float(math.floor(percentile * count / 100.0))
                    for percentile in (0, 25, 50, 75, 100)
                )
        rows.append([*composition, *transitions, *distribution])
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 189) or not np.isfinite(values).all():
        raise ValueError("CeTD block failed shape or finiteness contract")
    return values, feature_names


def _load_paac_properties(path: Path) -> np.ndarray:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if len(rows) != 4 or len(rows[0]) != 21:
        raise ValueError("PAAC property table shape drifted")
    values = np.asarray([[float(value) for value in row[1:]] for row in rows[1:]])
    means = values.mean(axis=1, keepdims=True)
    standard_deviations = np.sqrt(((values - means) ** 2).mean(axis=1, keepdims=True))
    normalized = (values - means) / standard_deviations
    if normalized.shape != (3, 20) or not np.isfinite(normalized).all():
        raise ValueError("PAAC normalized property table is invalid")
    return normalized


def _load_distance_matrix(path: Path) -> dict[str, dict[str, float]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) != 21 or len(rows[0]) != 21 or rows[0][0] != "Name":
        raise ValueError(f"distance matrix shape or header drifted: {path.name}")
    columns = rows[0][1:]
    matrix = {
        row[0]: {column: float(value) for column, value in zip(columns, row[1:], strict=True)}
        for row in rows[1:]
    }
    if set(columns) != STANDARD_AMINO_ACIDS or set(matrix) != STANDARD_AMINO_ACIDS:
        raise ValueError(f"distance matrix residues drifted: {path.name}")
    return matrix


def _squared_distance_sum(
    sequence: str, matrix: dict[str, dict[str, float]], gap: int = 1
) -> float:
    return sum(
        matrix[sequence[index + gap]][sequence[index]] ** 2
        for index in range(len(sequence) - gap)
    )


def compute_common_tail_block(
    sequences: list[str], data_root: Path
) -> tuple[np.ndarray, list[str]]:
    """Compute PAAC1, APAAC1 and QSO1 shared by both RF models."""

    validated = validate_sequences(sequences)
    if any(len(sequence) < 2 for sequence in validated):
        raise ValueError("PAAC/QSO gap 1 requires sequences of length at least two")
    properties = _load_paac_properties(data_root / "data")
    schneider = _load_distance_matrix(data_root / "Schneider-Wrede.csv")
    grantham = _load_distance_matrix(data_root / "Grantham.csv")
    residue_index = {residue: index for index, residue in enumerate(AMINO_ACID_ORDER)}
    paac_names = [f"PAAC1_{residue}" for residue in AMINO_ACID_ORDER] + [
        "PAAC1_lam1"
    ]
    apaac_names = [f"APAAC1_{residue}" for residue in AMINO_ACID_ORDER] + [
        "APAAC1_HB_lam1",
        "APAAC1_HL_lam1",
        "APAAC1_SC_lam1",
    ]
    qso_names = [f"QSO1_SC_{residue}" for residue in AMINO_ACID_ORDER]
    qso_names.extend(f"QSO1_G_{residue}" for residue in AMINO_ACID_ORDER)
    qso_names.extend(["QSO1_SC1", "QSO1_G1"])
    feature_names = paac_names + apaac_names + qso_names
    rows: list[list[float]] = []
    for sequence in validated:
        encoded = [residue_index[residue] for residue in sequence]
        counts = Counter(sequence)
        aac = [
            _rounded(counts[residue] / len(sequence) * 100.0, 2)
            for residue in AMINO_ACID_ORDER
        ]
        theta = sum(
            np.mean((properties[:, encoded[index]] - properties[:, encoded[index + 1]]) ** 2)
            for index in range(len(sequence) - 1)
        ) / (len(sequence) - 1)
        paac = [*aac, round((0.05 * theta) / (1.0 + 0.05 * theta), 4)]
        correlations = [
            sum(
                properties[property_index, encoded[index]]
                * properties[property_index, encoded[index + 1]]
                for index in range(len(sequence) - 1)
            )
            / (len(sequence) - 1)
            for property_index in range(3)
        ]
        correlation_denominator = 1.0 + 0.05 * sum(correlations)
        apaac = [
            *aac,
            *(round(0.05 * value / correlation_denominator, 4) for value in correlations),
        ]
        schneider_sum = _squared_distance_sum(sequence, schneider)
        grantham_sum = _squared_distance_sum(sequence, grantham)
        schneider_denominator = 1.0 + 0.1 * schneider_sum
        grantham_denominator = 1.0 + 0.1 * grantham_sum
        qso = [
            *(round(counts[residue] / schneider_denominator, 4) for residue in AMINO_ACID_ORDER),
            *(round(counts[residue] / grantham_denominator, 4) for residue in AMINO_ACID_ORDER),
            round(0.1 * schneider_sum / schneider_denominator, 4),
            round(0.1 * schneider_sum / schneider_denominator, 4),
        ]
        rows.append([*paac, *apaac, *qso])
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 86) or not np.isfinite(values).all():
        raise ValueError("common tail block failed shape or finiteness contract")
    return values, feature_names


def compute_classifier_specific_block(
    sequences: list[str], data_root: Path
) -> tuple[np.ndarray, list[str]]:
    validated = validate_sequences(sequences)
    pcp = np.asarray(_read_numeric_csv(data_root / "PhysicoChemical.csv"), dtype=np.float64)
    if pcp.shape != (30, 20):
        raise ValueError("physicochemical reference table must be 30x20")
    residue_index = {residue: index for index, residue in enumerate(AMINO_ACID_ORDER)}
    names = [
        "SEP_PC",
        "SEP_NC",
        "SEP_NE",
        "SEP_PO",
        "SEP_NP",
        "SEP_AL",
        "SEP_CY",
        "SEP_AR",
        "SEP_AC",
        "SEP_BS",
        "SEP_NE_pH",
        "SEP_HB",
        "SEP_HL",
        "SEP_NT",
        "SEP_HX",
        "SEP_SC",
        "SEP_SS_HE",
        "SEP_SS_ST",
        "SEP_SS_CO",
        "SEP_SA_BU",
        "SEP_SA_EX",
        "SEP_SA_IN",
        "SEP_TN",
        "SEP_SM",
        "SEP_LR",
    ]
    rows: list[list[float]] = []
    for sequence in validated:
        encoded = [residue_index[residue] for residue in sequence]
        probabilities = pcp[:25, encoded].mean(axis=1)
        entropy = [
            0.0
            if probability in {0.0, 1.0}
            else round(
                -(
                    probability * math.log2(probability)
                    + (1.0 - probability) * math.log2(1.0 - probability)
                ),
                3,
            )
            for probability in probabilities
        ]
        rows.append(entropy)
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 25) or not np.isfinite(values).all():
        raise ValueError("classifier-specific block failed shape or finiteness contract")
    return values, names


def compute_regression_specific_block(
    sequences: list[str], data_root: Path
) -> tuple[np.ndarray, list[str]]:
    validated = validate_sequences(sequences)
    if any(len(sequence) < 2 for sequence in validated):
        raise ValueError("SOC gap 1 requires sequences of length at least two")
    schneider = _load_distance_matrix(data_root / "Schneider-Wrede.csv")
    grantham = _load_distance_matrix(data_root / "Grantham.csv")
    rows = [
        [
            round(_squared_distance_sum(sequence, schneider) / (len(sequence) - 1), 4),
            round(_squared_distance_sum(sequence, grantham) / (len(sequence) - 1), 4),
        ]
        for sequence in validated
    ]
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(validated), 2) or not np.isfinite(values).all():
        raise ValueError("regression-specific block failed shape or finiteness contract")
    return values, ["SOC1_SC1", "SOC1_G1"]


def assemble_feature_matrices(
    sequences: list[str], data_root: Path
) -> dict[str, tuple[np.ndarray, list[str]]]:
    """Assemble and validate the two complete, model-specific feature matrices."""

    blocks = [
        compute_basic_feature_block(sequences),
        compute_reference_table_feature_block(sequences, data_root),
        compute_entropy_feature_block(sequences),
        compute_conjoint_triad_block(sequences),
        compute_ctd_block(sequences, data_root / "aa_attr_group.csv"),
        compute_common_tail_block(sequences, data_root),
    ]
    common_values = np.concatenate([block[0] for block in blocks], axis=1)
    common_names = [name for block in blocks for name in block[1]]
    classifier_tail = compute_classifier_specific_block(sequences, data_root)
    regression_tail = compute_regression_specific_block(sequences, data_root)
    classifier = (
        np.concatenate([common_values, classifier_tail[0]], axis=1),
        [*common_names, *classifier_tail[1]],
    )
    regression = (
        np.concatenate([common_values, regression_tail[0]], axis=1),
        [*common_names, *regression_tail[1]],
    )
    FeatureMatrixContract(
        feature_count=1190,
        ordered_feature_names_sha256=(
            "aad3eca84e467f5d6d48ab1f49096de5eb48b9354a71cef042529526342fc778"
        ),
        first_feature="Molecular Weight (kDa)",
        last_feature="SEP_LR",
    ).validate(*classifier)
    FeatureMatrixContract(
        feature_count=1167,
        ordered_feature_names_sha256=(
            "d8ea48ee923d6275ff3bb904b609974c4116ed9e0aecee968a52cb51066c618e"
        ),
        first_feature="Molecular Weight (kDa)",
        last_feature="SOC1_G1",
    ).validate(*regression)
    return {"classification": classifier, "regression": regression}


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
