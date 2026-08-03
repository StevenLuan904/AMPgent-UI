from __future__ import annotations

from typing import Any

HANDOFF_METRIC_VERSION = "2026.08.04-v1"

METRIC_PLUGIN_CONTRACTS: dict[str, dict[str, Any]] = {
    "physicochemical_developability": {
        "default_trust": "descriptor",
        "maximum_trust": "descriptor",
        "reliability": "descriptor-R2",
        "provider": "builtin",
        "outputs": {
            "molecular_weight_da": ("molecular_weight", "Da", "numeric"),
            "net_charge_ph7_4": ("net_charge_ph7_4", "elementary_charge", "numeric"),
            "isoelectric_point": ("isoelectric_point", "pH", "numeric"),
            "hydrophobic_ratio_modlamp": ("hydrophobic_ratio", "fraction", "numeric"),
            "hydrophobic_moment_eisenberg": (
                "hydrophobic_moment",
                "dimensionless",
                "numeric",
            ),
        },
    },
    "hemolysis_risk": {
        "default_trust": "soft",
        "maximum_trust": "soft",
        "reliability": "R3-not-locked",
        "provider": "external",
        "outputs": {
            "hemopi2_hemolysis_score": ("hemopi2_score", "dimensionless", "numeric"),
            "hemopi2_hemolysis_label": ("hemopi2_label", None, "text"),
            "macrel_amp_probability": ("macrel_amp_probability", "fraction", "numeric"),
            "macrel_hemolysis_probability": (
                "macrel_hemolysis_probability",
                "fraction",
                "numeric",
            ),
            "macrel_hemolysis_label": ("macrel_risk", None, "text"),
            "hemolysis_consensus_decision": ("decision", None, "text"),
        },
    },
    "toxicity_risk": {
        "default_trust": "soft",
        "maximum_trust": "soft",
        "reliability": "R3-minus-not-assay-matched",
        "provider": "external",
        "outputs": {
            "toxinpred3_ml_score": ("toxinpred3_ml_score", "dimensionless", "numeric"),
            "toxinpred3_hybrid_score": (
                "toxinpred3_hybrid_score",
                "dimensionless",
                "numeric",
            ),
            "toxinpred3_label": ("toxinpred3_label", None, "text"),
        },
    },
    "mic_potency": {
        "default_trust": "soft",
        "maximum_trust": "soft",
        "reliability": "R3-author-test-not-similarity-isolated",
        "provider": "external",
        "outputs": {
            "llamp_log10_mic_um": ("llamp_log10_mic_um", "log10(umol/L)", "numeric"),
            "llamp_predicted_mic_um": (
                "llamp_predicted_mic_um",
                "umol/L",
                "numeric",
            ),
        },
    },
    "amp_likeness": {
        "default_trust": "soft",
        "maximum_trust": "soft",
        "reliability": "R2-plus-small-local-crosscheck",
        "provider": "external",
        "outputs": {
            "amplify_probability": ("amplify_probability", "fraction", "numeric"),
            "amplify_label": ("amplify_label", None, "text"),
        },
    },
    "sequence_novelty": {
        "default_trust": "descriptor",
        "maximum_trust": "descriptor",
        "reliability": "deterministic-representation-R2",
        "provider": "external",
        "outputs": {
            "mmseqs_nearest_identity": ("nearest_fident", "fraction", "numeric"),
            "mmseqs_nearest_query_coverage": (
                "nearest_query_coverage",
                "fraction",
                "numeric",
            ),
            "mmseqs_nearest_target_coverage": (
                "nearest_target_coverage",
                "fraction",
                "numeric",
            ),
            "mmseqs_nearest_evalue": ("nearest_evalue", "dimensionless", "numeric"),
            "esm2_nearest_cosine_similarity": (
                "esm2_cosine_similarity",
                "dimensionless",
                "numeric",
            ),
        },
    },
    "serum_half_life": {
        "default_trust": "shadow",
        "maximum_trust": "shadow",
        "reliability": "R2-training-replay-only",
        "provider": "external",
        "outputs": {
            "peptiverse_predicted_log1p_half_life_hours": (
                "peptiverse_predicted_log1p_half_life_hours",
                "log1p(hours)",
                "numeric",
            ),
            "peptiverse_predicted_half_life_hours": (
                "peptiverse_predicted_half_life_hours",
                "hours",
                "numeric",
            ),
        },
    },
    "aggregation_apr": {
        "default_trust": "shadow",
        "maximum_trust": "shadow",
        "reliability": "R2-technical-replay-no-short-AMP-crosscheck",
        "provider": "external",
        "outputs": {
            "aggrescanai_apr_mean": ("score_mean", "dimensionless", "numeric"),
            "aggrescanai_apr_max": ("score_max", "dimensionless", "numeric"),
        },
    },
}


def _descriptor_scalar(descriptor: object) -> float:
    return float(descriptor.descriptor[0][0])


def physicochemical_descriptors(
    sequence: str,
    *,
    ph: float = 7.4,
    c_terminal_amidated: bool = False,
    hydrophobic_moment_angle: int = 100,
) -> dict[str, Any]:
    """Reproduce the handoff's pinned modlAMP descriptor protocol exactly."""
    import importlib.metadata

    import modlamp
    from modlamp.descriptors import GlobalDescriptor, PeptideDescriptor

    global_descriptor = GlobalDescriptor(sequence)
    global_descriptor.calculate_MW(amide=c_terminal_amidated)
    molecular_weight = _descriptor_scalar(global_descriptor)
    global_descriptor.calculate_charge(ph=ph, amide=c_terminal_amidated)
    net_charge = _descriptor_scalar(global_descriptor)
    global_descriptor.isoelectric_point(amide=c_terminal_amidated)
    isoelectric_point = _descriptor_scalar(global_descriptor)
    global_descriptor.hydrophobic_ratio()
    hydrophobic_ratio = _descriptor_scalar(global_descriptor)

    moment_descriptor = PeptideDescriptor(sequence, "eisenberg")
    moment_descriptor.calculate_moment(
        window=1000, angle=hydrophobic_moment_angle, modality="max"
    )
    hydrophobic_moment = _descriptor_scalar(moment_descriptor)
    return {
        "molecular_weight": molecular_weight,
        "net_charge_ph7_4": net_charge,
        "isoelectric_point": isoelectric_point,
        "hydrophobic_ratio": hydrophobic_ratio,
        "hydrophobic_moment": hydrophobic_moment,
        "assumptions": {
            "ph": ph,
            "termini": (
                "free N-terminus and amidated C-terminus"
                if c_terminal_amidated
                else "free N-terminus and free C-terminus"
            ),
            "hydrophobicity_scale": "Eisenberg consensus",
            "hydrophobic_moment_angle_degrees": hydrophobic_moment_angle,
            "hydrophobic_moment_window": 1000,
            "modlamp_distribution_version": importlib.metadata.version("modlamp"),
            "modlamp_runtime_version": modlamp.__version__,
        },
        "limitations": [
            "Transparent sequence descriptors; not experimental safety, stability, or potency.",
            "The 100-degree hydrophobic moment is an idealized alpha-helical projection and does "
            "not assert that the peptide forms an alpha helix.",
            "Canonical sequence inference does not represent terminal capping, cyclization, "
            "D-residues, or other chemical modifications.",
        ],
        "method_version": HANDOFF_METRIC_VERSION,
    }


def normalize_metric_records(
    plugin_name: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    contract = METRIC_PLUGIN_CONTRACTS[plugin_name]
    normalized: list[dict[str, Any]] = []
    for row in rows:
        observations: list[dict[str, Any]] = []
        for metric_name, (source_field, unit, value_type) in contract["outputs"].items():
            raw_value = row.get(source_field)
            if raw_value in {None, ""}:
                continue
            if value_type == "numeric":
                try:
                    numeric_value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                observations.append(
                    {
                        "metric_name": metric_name,
                        "numeric_value": numeric_value,
                        "text_value": None,
                        "unit": unit,
                    }
                )
            else:
                observations.append(
                    {
                        "metric_name": metric_name,
                        "numeric_value": None,
                        "text_value": str(raw_value),
                        "unit": unit,
                    }
                )
        normalized.append(
            {
                "candidate_id": str(row.get("candidate_id") or row["internal_id"]),
                "sequence": row["sequence"],
                "status": row.get("status", "complete"),
                "observations": observations,
                "raw": row,
            }
        )
    return normalized
