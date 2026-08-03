from __future__ import annotations

import math
import statistics
from typing import Any


def validate_rosetta_protocol_policy(source_policy: dict[str, Any]) -> None:
    """Reject validation suites that do not match the active scoring protocol."""
    if source_policy.get("prepack") is not True:
        raise ValueError("FlexPepDock validation requires one prepack before refinement")
    if source_policy.get("pack_separated") is not False:
        raise ValueError(
            "active InterfaceAnalyzer protocol requires pack_separated=false"
        )


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average = (start + end - 1) / 2 + 1
        for index in order[start:end]:
            ranks[index] = average
        start = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    left_delta = [value - left_mean for value in left]
    right_delta = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_delta)
        * sum(value * value for value in right_delta)
    )
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(left_delta, right_delta, strict=True)) / denominator


def summarize_native_start_validation(result: dict[str, Any]) -> dict[str, Any]:
    """Derive replayable validation-only checks without creating a decision metric."""
    decoys = list(result.get("decoys", []))
    if not decoys:
        raise ValueError("Rosetta result contains no decoys")
    required = {"reweighted_sc", "dG_separated", "peptide_bb_rmsd"}
    if any(not required.issubset(decoy) for decoy in decoys):
        raise ValueError("Rosetta decoys are missing validation fields")

    ranked = sorted(decoys, key=lambda decoy: float(decoy["reweighted_sc"]))
    top_count = min(10, len(ranked))
    top = ranked[:top_count]
    rmsd = [float(decoy["peptide_bb_rmsd"]) for decoy in decoys]
    reweighted = [float(decoy["reweighted_sc"]) for decoy in decoys]
    d_g = [float(decoy["dG_separated"]) for decoy in decoys]

    return {
        "nstruct": len(decoys),
        "primary_dG_separated_reu": float(result["primary_dG_separated_reu"]),
        "dG_minimum_reu": min(d_g),
        "dG_median_reu": float(statistics.median(d_g)),
        "rmsd_minimum_angstrom": min(rmsd),
        "rmsd_median_angstrom": float(statistics.median(rmsd)),
        "rmsd_maximum_angstrom": max(rmsd),
        "fraction_rmsd_le_1_angstrom": sum(value <= 1 for value in rmsd) / len(rmsd),
        "fraction_rmsd_le_2_angstrom": sum(value <= 2 for value in rmsd) / len(rmsd),
        "top_reweighted_count": top_count,
        "top1_reweighted_rmsd_angstrom": float(top[0]["peptide_bb_rmsd"]),
        "top_reweighted_rmsd_median_angstrom": float(
            statistics.median(float(decoy["peptide_bb_rmsd"]) for decoy in top)
        ),
        "top_reweighted_dG_median_reu": float(
            statistics.median(float(decoy["dG_separated"]) for decoy in top)
        ),
        "reweighted_rmsd_spearman": _pearson(
            _average_ranks(reweighted), _average_ranks(rmsd)
        ),
        "interpretation": {
            "native_start_recovery_only": True,
            "experimental_affinity_calibration": False,
            "reu_is_not_kcal_per_mol": True,
            "visual_snapshot_used_for_decision": False,
        },
    }
