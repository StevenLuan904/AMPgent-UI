from __future__ import annotations

from typing import Any


def summarize_composition_pair_panel_robustness(
    candidates: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compare registered parent/scramble pairs across matching LOO panels.

    The diagnostic consumes already-computed proxy results. It does not create a
    new model observation and must not be treated as independent evidence.
    """
    candidates_by_sha: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        sequence_sha256 = str(candidate["sequence_sha256"])
        if sequence_sha256 in candidates_by_sha:
            raise ValueError("candidate sequence_sha256 values must be unique")
        candidates_by_sha[sequence_sha256] = candidate

    results_by_sha: dict[str, dict[str, Any]] = {}
    for result in results:
        sequence_sha256 = str(result["sequence_sha256"])
        if sequence_sha256 in results_by_sha:
            raise ValueError("result sequence_sha256 values must be unique")
        results_by_sha[sequence_sha256] = result

    pairs: list[dict[str, Any]] = []
    seen_parent_sha256: set[str] = set()
    for scramble_sha256, scramble in candidates_by_sha.items():
        parent_sha256 = scramble.get("composition_reference_sequence_sha256")
        if parent_sha256 is None:
            continue
        parent_sha256 = str(parent_sha256)
        if parent_sha256 in seen_parent_sha256:
            raise ValueError("each parent must have exactly one registered scramble")
        seen_parent_sha256.add(parent_sha256)
        if parent_sha256 not in candidates_by_sha:
            raise ValueError("composition reference candidate is missing")
        if parent_sha256 not in results_by_sha or scramble_sha256 not in results_by_sha:
            raise ValueError("paired candidate result is missing")

        parent = candidates_by_sha[parent_sha256]
        if sorted(str(parent["sequence"])) != sorted(str(scramble["sequence"])):
            raise ValueError("registered scramble is not composition-matched")

        parent_result = results_by_sha[parent_sha256]
        scramble_result = results_by_sha[scramble_sha256]
        parent_loo = _index_leave_one_control_out(parent_result)
        scramble_loo = _index_leave_one_control_out(scramble_result)
        if parent_loo.keys() != scramble_loo.keys():
            raise ValueError("paired results must use the same omitted-control panels")

        loo_gaps = [
            float(parent_loo[key]["target_specific_delta_nll"])
            - float(scramble_loo[key]["target_specific_delta_nll"])
            for key in parent_loo
        ]
        primary_gap = float(parent_result["target_specific_delta_nll"]) - float(
            scramble_result["target_specific_delta_nll"]
        )
        pairs.append(
            {
                "parent_sequence": parent["sequence"],
                "parent_sequence_sha256": parent_sha256,
                "scramble_sequence": scramble["sequence"],
                "scramble_sequence_sha256": scramble_sha256,
                "primary_target_specific_delta_nll_gap": primary_gap,
                "same_omission_gap_min": min(loo_gaps),
                "same_omission_gap_max": max(loo_gaps),
                "all_same_omission_gaps_positive": all(gap > 0 for gap in loo_gaps),
                "omission_panel_count": len(loo_gaps),
            }
        )

    if not pairs:
        raise ValueError("at least one registered parent/scramble pair is required")

    return {
        "method": "registered_composition_pairs_across_same_loo_panels",
        "diagnostic_only": True,
        "primary_metric_unchanged": True,
        "independent_evidence": False,
        "pair_count": len(pairs),
        "robust_positive_pair_count": sum(
            pair["all_same_omission_gaps_positive"] for pair in pairs
        ),
        "pairs": pairs,
    }


def _index_leave_one_control_out(
    result: dict[str, Any],
) -> dict[tuple[str, str | None, str | None], dict[str, Any]]:
    sensitivity = result.get("stratified_panel_sensitivity")
    if not isinstance(sensitivity, dict) or not sensitivity.get("diagnostic_only"):
        raise ValueError("stratified diagnostic-only panel sensitivity is required")
    indexed: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
    for item in sensitivity.get("leave_one_control_out", []):
        key = (
            str(item["omitted_control_type"]),
            item.get("omitted_accession"),
            item.get("omitted_sequence_sha256"),
        )
        if key in indexed:
            raise ValueError("omitted-control panel identifiers must be unique")
        indexed[key] = item
    if not indexed:
        raise ValueError("at least one leave-one-control-out panel is required")
    return indexed
