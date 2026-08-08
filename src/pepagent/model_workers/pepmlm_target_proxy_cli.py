import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "ChatterjeeLab/PepMLM-650M"
DEFAULT_REVISION = "898fca941a9057aebdd1a6164b5ee09a1a71780e"
CANONICAL_AA = set("ACDEFGHIKLMNPQRSTVWY")
V21_METRIC_VERSION = "v21_pooled"
V22_METRIC_VERSION = "v22_stratified"


def _normalize_sequence(value: str, *, field: str) -> str:
    normalized = "".join(value.split()).upper()
    if not normalized or not set(normalized).issubset(CANONICAL_AA):
        raise ValueError(f"{field} must contain only canonical amino acids")
    return normalized


def _require_sequence_sha256(sequence: str, expected: str, *, field: str) -> None:
    actual = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError(f"{field} sequence_sha256 mismatch: expected {expected}, got {actual}")


def target_panel_sha256(targets: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        targets, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_target_panel_for_metric(
    targets: list[dict[str, Any]], metric_version: str
) -> None:
    if metric_version != V22_METRIC_VERSION:
        return
    allowed = {"primary", "unrelated", "composition_shuffle"}
    unknown = {item.get("control_type") for item in targets}.difference(allowed)
    if unknown:
        raise ValueError(
            "unknown control_type(s): "
            + ", ".join(sorted(repr(value) for value in unknown))
        )
    counts = {
        control_type: sum(item["control_type"] == control_type for item in targets)
        for control_type in allowed
    }
    expected = {"primary": 1, "unrelated": 4, "composition_shuffle": 8}
    if counts != expected:
        raise ValueError(
            "v22 target panel requires exactly 1 primary, 4 unrelated, "
            "and 8 composition_shuffle targets"
        )
    primary_sequence = next(
        item["sequence"] for item in targets if item["control_type"] == "primary"
    )
    for item in targets:
        if item["control_type"] == "composition_shuffle" and sorted(
            item["sequence"]
        ) != sorted(primary_sequence):
            raise ValueError("v22 composition_shuffle is not composition-matched")


def summarize_target_specific_delta_nll(
    peptide: dict[str, Any], target_scores: list[dict[str, Any]]
) -> dict[str, Any]:
    primary = [item for item in target_scores if item["control_type"] == "primary"]
    decoys = [item for item in target_scores if item["control_type"] != "primary"]
    if len(primary) != 1:
        raise ValueError("exactly one primary target is required")
    if len(decoys) < 2:
        raise ValueError("at least two decoy targets are required")
    acea_nll = float(primary[0]["conditional_nll"])
    decoy_nlls = [float(item["conditional_nll"]) for item in decoys]
    decoy_median = float(statistics.median(decoy_nlls))
    leave_one_decoy_out: list[dict[str, Any]] = []
    for omitted_index, omitted in enumerate(decoys):
        retained_nlls = [
            value for index, value in enumerate(decoy_nlls) if index != omitted_index
        ]
        retained_median = float(statistics.median(retained_nlls))
        leave_one_decoy_out.append(
            {
                "omitted_accession": omitted.get("accession"),
                "omitted_sequence_sha256": omitted.get("sequence_sha256"),
                "omitted_control_type": omitted["control_type"],
                "decoy_target_nll_median": retained_median,
                "target_specific_delta_nll": retained_median - acea_nll,
            }
        )
    leave_one_out_deltas = [
        item["target_specific_delta_nll"] for item in leave_one_decoy_out
    ]
    return {
        "sequence": peptide["sequence"],
        "sequence_sha256": peptide["sequence_sha256"],
        "primary_target_nll": acea_nll,
        "decoy_target_nll_median": decoy_median,
        "target_specific_delta_nll": decoy_median - acea_nll,
        "panel_sensitivity": {
            "method": "leave_one_decoy_out",
            "diagnostic_only": True,
            "target_specific_delta_nll_min": min(leave_one_out_deltas),
            "target_specific_delta_nll_max": max(leave_one_out_deltas),
            "target_specific_delta_nll_range": max(leave_one_out_deltas)
            - min(leave_one_out_deltas),
            "leave_one_decoy_out": leave_one_decoy_out,
        },
        "target_scores": target_scores,
        "interpretation": {
            "direction": "higher_values_rank_as_more_primary_target_conditioned",
            "confidence": "low",
            "admission_status": "out_of_domain",
            "evidence_kind": "sequence_binding_proxy",
            "rank_only": True,
            "is_binding_probability": False,
            "is_affinity": False,
            "may_override_structure_evidence": False,
            "independence": "not_independent_from_pepmlm_generation_or_ppl",
        },
    }


def summarize_stratified_target_specific_delta_nll(
    peptide: dict[str, Any], target_scores: list[dict[str, Any]]
) -> dict[str, Any]:
    """Summarize the preregistered v22, control-type-stratified proxy.

    This is deliberately separate from ``summarize_target_specific_delta_nll`` so
    callers of the pooled v21 metric retain its existing behavior and schema.
    """
    allowed_control_types = {"primary", "unrelated", "composition_shuffle"}
    unknown_control_types = {
        item.get("control_type")
        for item in target_scores
        if item.get("control_type") not in allowed_control_types
    }
    if unknown_control_types:
        raise ValueError(
            "unknown control_type(s): "
            + ", ".join(sorted(repr(value) for value in unknown_control_types))
        )

    normalized_scores: list[tuple[dict[str, Any], float]] = []
    for item in target_scores:
        conditional_nll = float(item["conditional_nll"])
        if not math.isfinite(conditional_nll):
            raise ValueError("conditional_nll must be finite")
        normalized_scores.append((item, conditional_nll))

    primary_nlls = [
        value
        for item, value in normalized_scores
        if item["control_type"] == "primary"
    ]
    if len(primary_nlls) != 1:
        raise ValueError("exactly one primary target is required")

    strata: dict[str, list[float]] = {
        control_type: [
            value
            for item, value in normalized_scores
            if item["control_type"] == control_type
        ]
        for control_type in ("unrelated", "composition_shuffle")
    }
    for control_type, values in strata.items():
        if len(values) < 2:
            raise ValueError(
                f"at least two {control_type} control targets are required"
            )

    primary_nll = primary_nlls[0]
    stratum_medians = {
        control_type: float(statistics.median(values))
        for control_type, values in strata.items()
    }
    stratified_control_nll = 0.5 * (
        stratum_medians["unrelated"] + stratum_medians["composition_shuffle"]
    )
    pooled_median = float(
        statistics.median(strata["unrelated"] + strata["composition_shuffle"])
    )
    type_deltas = {
        control_type: median - primary_nll
        for control_type, median in stratum_medians.items()
    }
    leave_one_control_out: list[dict[str, Any]] = []
    for control_type in ("unrelated", "composition_shuffle"):
        stratum_scores = [
            (item, value)
            for item, value in normalized_scores
            if item["control_type"] == control_type
        ]
        other_control_type = (
            "composition_shuffle"
            if control_type == "unrelated"
            else "unrelated"
        )
        for omitted_index, (omitted, _) in enumerate(stratum_scores):
            retained_values = [
                value
                for index, (_, value) in enumerate(stratum_scores)
                if index != omitted_index
            ]
            retained_median = float(statistics.median(retained_values))
            stratified_control_nll_without_omitted = 0.5 * (
                retained_median + stratum_medians[other_control_type]
            )
            leave_one_control_out.append(
                {
                    "omitted_accession": omitted.get("accession"),
                    "omitted_sequence_sha256": omitted.get("sequence_sha256"),
                    "omitted_control_type": control_type,
                    "retained_control_type_nll_median": retained_median,
                    "stratified_control_target_nll": (
                        stratified_control_nll_without_omitted
                    ),
                    "target_specific_delta_nll": (
                        stratified_control_nll_without_omitted - primary_nll
                    ),
                }
            )
    leave_one_out_deltas = [
        item["target_specific_delta_nll"] for item in leave_one_control_out
    ]

    return {
        "sequence": peptide["sequence"],
        "sequence_sha256": peptide["sequence_sha256"],
        "metric_version": "v22_stratified",
        "primary_target_nll": primary_nll,
        "control_type_nll_medians": stratum_medians,
        "stratified_control_target_nll": stratified_control_nll,
        "target_specific_delta_nll": stratified_control_nll - primary_nll,
        "pooled_v21_compatible_secondary": {
            "method": "median(all_control_target_nlls)-primary_target_nll",
            "secondary": True,
            "decoy_target_nll_median": pooled_median,
            "target_specific_delta_nll": pooled_median - primary_nll,
        },
        "control_type_sensitivity": {
            "method": "control_type_median_minus_primary_target_nll",
            "diagnostic_only": True,
            "target_specific_delta_nll_by_control_type": type_deltas,
            "target_specific_delta_nll_min": min(type_deltas.values()),
            "target_specific_delta_nll_max": max(type_deltas.values()),
            "target_specific_delta_nll_range": max(type_deltas.values())
            - min(type_deltas.values()),
        },
        "stratified_panel_sensitivity": {
            "method": "leave_one_control_out_within_stratum",
            "diagnostic_only": True,
            "primary_metric_unchanged": True,
            "target_specific_delta_nll_min": min(leave_one_out_deltas),
            "target_specific_delta_nll_max": max(leave_one_out_deltas),
            "target_specific_delta_nll_range": max(leave_one_out_deltas)
            - min(leave_one_out_deltas),
            "leave_one_control_out": leave_one_control_out,
        },
        "target_scores": target_scores,
        "interpretation": {
            "direction": "higher_values_rank_as_more_primary_target_conditioned",
            "confidence": "low",
            "admission_status": "out_of_domain",
            "evidence_kind": "sequence_binding_proxy",
            "rank_only": True,
            "is_binding_probability": False,
            "is_affinity": False,
            "may_override_structure_evidence": False,
            "independence": "not_independent_from_pepmlm_generation_or_ppl",
        },
    }


def summarize_target_specific_delta_nll_for_version(
    peptide: dict[str, Any],
    target_scores: list[dict[str, Any]],
    metric_version: str | None,
) -> dict[str, Any]:
    """Route explicitly versioned metrics without changing legacy v21 behavior."""
    if metric_version in (None, V21_METRIC_VERSION):
        return summarize_target_specific_delta_nll(peptide, target_scores)
    if metric_version == V22_METRIC_VERSION:
        return summarize_stratified_target_specific_delta_nll(peptide, target_scores)
    raise ValueError(f"unsupported metric_version: {metric_version!r}")


def pseudo_perplexity(
    model: Any, tokenizer: Any, target: str, peptide: str
) -> tuple[float, float, list[float]]:
    # Heavy model dependencies stay worker-local so control-plane tests and imports do not require
    # the GPU environment.
    import math

    import torch

    encoded = tokenizer.encode(target + peptide, return_tensors="pt").to(model.device)
    peptide_length = len(peptide)
    masked = encoded.repeat(peptide_length, 1)
    positions = torch.arange(-peptide_length - 1, -1, device=model.device)
    row_ids = torch.arange(peptide_length, device=model.device)
    labels = encoded[0, positions]
    masked[row_ids, positions] = tokenizer.mask_token_id
    with torch.inference_mode():
        logits = model(masked).logits[row_ids, positions]
        log_probs = torch.log_softmax(logits, dim=-1)
        residue_log_probs = log_probs[row_ids, labels]
    nll = -float(residue_log_probs.mean().item())
    return nll, math.exp(nll), [float(value) for value in residue_log_probs.cpu().tolist()]


def main() -> None:
    import torch
    from transformers import AutoModelForMaskedLM, AutoTokenizer

    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    metric_version = request.get("metric_version", V21_METRIC_VERSION)
    if metric_version not in {V21_METRIC_VERSION, V22_METRIC_VERSION}:
        raise ValueError(f"unsupported metric_version: {metric_version!r}")

    targets: list[dict[str, Any]] = []
    for index, raw in enumerate(request["targets"]):
        target = {**raw}
        target["sequence"] = _normalize_sequence(
            str(raw["sequence"]), field=f"targets[{index}].sequence"
        )
        _require_sequence_sha256(
            target["sequence"],
            str(raw["sequence_sha256"]),
            field=f"targets[{index}]",
        )
        targets.append(target)
    if len({item["sequence_sha256"] for item in targets}) != len(targets):
        raise ValueError("target control sequences must be unique")
    validate_target_panel_for_metric(targets, metric_version)
    actual_panel_sha256 = target_panel_sha256(targets)
    if actual_panel_sha256 != request["target_panel_sha256"]:
        raise ValueError(
            "target_panel_sha256 mismatch: "
            f"expected {request['target_panel_sha256']}, got {actual_panel_sha256}"
        )

    peptides: list[dict[str, Any]] = []
    for index, raw in enumerate(request["peptides"]):
        peptide = {**raw}
        peptide["sequence"] = _normalize_sequence(
            str(raw["sequence"]), field=f"peptides[{index}].sequence"
        )
        _require_sequence_sha256(
            peptide["sequence"],
            str(raw["sequence_sha256"]),
            field=f"peptides[{index}]",
        )
        peptides.append(peptide)

    model_name = request.get("model", DEFAULT_MODEL)
    revision = request.get("revision", DEFAULT_REVISION)
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
    model = AutoModelForMaskedLM.from_pretrained(
        model_name,
        revision=revision,
        dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()

    results: list[dict[str, Any]] = []
    for peptide in peptides:
        scores: list[dict[str, Any]] = []
        for target in targets:
            nll, ppl, residue_log_probabilities = pseudo_perplexity(
                model, tokenizer, target["sequence"], peptide["sequence"]
            )
            scores.append(
                {
                    "accession": target["accession"],
                    "sequence_sha256": target["sequence_sha256"],
                    "control_type": target["control_type"],
                    "conditional_nll": nll,
                    "conditional_ppl": ppl,
                    "per_residue_log_probabilities": residue_log_probabilities,
                }
            )
        results.append(
            summarize_target_specific_delta_nll_for_version(
                peptide, scores, metric_version
            )
        )

    if metric_version == V22_METRIC_VERSION:
        definition = (
            "0.5*median(unrelated_control_nlls)+"
            "0.5*median(composition_shuffle_control_nlls)-primary_target_nll"
        )
    else:
        definition = "median(decoy_target_nll)-primary_target_nll"

    output = {
        "schema_version": "1.0",
        "metric": "target_specific_delta_nll",
        "metric_version": metric_version,
        "definition": definition,
        "model": model_name,
        "revision": revision,
        "device": device,
        "target_panel_sha256": actual_panel_sha256,
        "targets": targets,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
