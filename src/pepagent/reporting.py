from __future__ import annotations

import csv
import io
from typing import Any

from pepagent.domain.enums import MetricName
from pepagent.selection import sequence_similarity

BULK_ROSETTA_CSV_COLUMNS = [
    "run_id",
    "candidate_id",
    "sequence",
    "length",
    "generation",
    "bulk_status",
    "conditional_ppl",
    "instability_index",
    "hydrophobic_fraction",
    "maximum_hydrophobic_run",
    "maximum_identical_residue_run",
    "molecular_weight_da",
    "net_charge_ph7_4",
    "isoelectric_point",
    "gravy",
    "cationic_residue_fraction",
    "hydrophobic_ratio_modlamp",
    "hydrophobic_moment_eisenberg",
    "hemopi2_hemolysis_score",
    "hemopi2_hemolysis_label",
    "macrel_amp_probability",
    "macrel_hemolysis_probability",
    "macrel_hemolysis_label",
    "hemolysis_consensus_decision",
    "toxinpred3_ml_score",
    "toxinpred3_hybrid_score",
    "toxinpred3_label",
    "llamp_log10_mic_um",
    "llamp_predicted_mic_um",
    "amp_read_log10_mic_um",
    "amp_read_predicted_mic_um",
    "amp_read_cnn_log10_mic_um",
    "amp_read_transformer_log10_mic_um",
    "amp_read_attention_log10_mic_um",
    "amp_read_lstm_log10_mic_um",
    "amplify_probability",
    "amplify_label",
    "mmseqs_nearest_identity",
    "mmseqs_nearest_query_coverage",
    "mmseqs_nearest_target_coverage",
    "mmseqs_nearest_evalue",
    "esm2_nearest_cosine_similarity",
    "peptiverse_predicted_log1p_half_life_hours",
    "peptiverse_predicted_half_life_hours",
    "aggrescanai_apr_mean",
    "aggrescanai_apr_max",
    "nearest_selected_sequence_similarity",
    "boltz2_pair_iptm",
    "boltz2_pair_iptm_median",
    "pocket_contact_count",
    "interface_clash_count",
    "structure_support",
    "rosetta_dg_separated_reu",
    "rosetta_dg_minimum_reu",
    "rosetta_peptide_bb_rmsd_angstrom",
    "rosetta_interface_score",
    "rosetta_reweighted_score",
    "rosetta_interface_hbonds",
    "rosetta_buried_surface_area",
    "rosetta_nstruct",
    "rosetta_adapter_version",
    "rosetta_score_function",
    "prepack",
    "pack_input",
    "pack_separated",
    "rosetta_tool_call_id",
]


def render_bulk_rosetta_csv(rows: list[dict[str, Any]]) -> bytes:
    """Render a stable UTF-8 CSV with an explicit, versioned column contract."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=BULK_ROSETTA_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column) for column in BULK_ROSETTA_CSV_COLUMNS})
    return stream.getvalue().encode("utf-8")


def build_bulk_rosetta_rows(
    candidates: list[Any],
    evaluations: list[Any],
    result_status: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build protocol-explicit rows from canonical candidates and evaluations."""
    statuses = result_status or {}
    latest: dict[Any, dict[str, Any]] = {candidate.id: {} for candidate in candidates}
    for evaluation in evaluations:
        if evaluation.candidate_id in latest:
            latest[evaluation.candidate_id][evaluation.metric_name] = evaluation

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        metrics = latest[candidate.id]
        dg_evaluation = metrics.get(MetricName.ROSETTA_DG_SEPARATED_REU)
        rosetta_raw = dg_evaluation.raw_json if dg_evaluation is not None else {}

        def numeric(metric_name: str, metric_values: dict[str, Any] = metrics) -> float | None:
            evaluation = metric_values.get(metric_name)
            return evaluation.numeric_value if evaluation is not None else None

        def text_value(metric_name: str, metric_values: dict[str, Any] = metrics) -> str | None:
            evaluation = metric_values.get(metric_name)
            return evaluation.text_value if evaluation is not None else None

        structure_support = metrics.get(MetricName.STRUCTURE_SUPPORT)
        similarities = [
            sequence_similarity(candidate.sequence, other.sequence)
            for other in candidates
            if other.id != candidate.id
        ]
        rows.append(
            {
                "run_id": str(candidate.run_id),
                "candidate_id": str(candidate.id),
                "sequence": candidate.sequence,
                "length": len(candidate.sequence),
                "generation": candidate.generation,
                "bulk_status": statuses.get(
                    str(candidate.id), "succeeded" if dg_evaluation is not None else "missing"
                ),
                "conditional_ppl": numeric(MetricName.CONDITIONAL_PPL),
                "instability_index": numeric(MetricName.INSTABILITY_INDEX),
                "hydrophobic_fraction": numeric(MetricName.HYDROPHOBIC_FRACTION),
                "maximum_hydrophobic_run": numeric(MetricName.MAXIMUM_HYDROPHOBIC_RUN),
                "maximum_identical_residue_run": numeric(
                    MetricName.MAXIMUM_IDENTICAL_RESIDUE_RUN
                ),
                "molecular_weight_da": numeric("molecular_weight_da"),
                "net_charge_ph7_4": numeric("net_charge_ph7_4"),
                "isoelectric_point": numeric("isoelectric_point"),
                "gravy": numeric("gravy"),
                "cationic_residue_fraction": numeric(
                    "cationic_residue_fraction"
                ),
                "hydrophobic_ratio_modlamp": numeric("hydrophobic_ratio_modlamp"),
                "hydrophobic_moment_eisenberg": numeric(
                    "hydrophobic_moment_eisenberg"
                ),
                "hemopi2_hemolysis_score": numeric("hemopi2_hemolysis_score"),
                "hemopi2_hemolysis_label": text_value("hemopi2_hemolysis_label"),
                "macrel_amp_probability": numeric("macrel_amp_probability"),
                "macrel_hemolysis_probability": numeric(
                    "macrel_hemolysis_probability"
                ),
                "macrel_hemolysis_label": text_value("macrel_hemolysis_label"),
                "hemolysis_consensus_decision": text_value(
                    "hemolysis_consensus_decision"
                ),
                "toxinpred3_ml_score": numeric("toxinpred3_ml_score"),
                "toxinpred3_hybrid_score": numeric("toxinpred3_hybrid_score"),
                "toxinpred3_label": text_value("toxinpred3_label"),
                "llamp_log10_mic_um": numeric("llamp_log10_mic_um"),
                "llamp_predicted_mic_um": numeric("llamp_predicted_mic_um"),
                "amp_read_log10_mic_um": numeric("amp_read_log10_mic_um"),
                "amp_read_predicted_mic_um": numeric(
                    "amp_read_predicted_mic_um"
                ),
                "amp_read_cnn_log10_mic_um": numeric(
                    "amp_read_cnn_log10_mic_um"
                ),
                "amp_read_transformer_log10_mic_um": numeric(
                    "amp_read_transformer_log10_mic_um"
                ),
                "amp_read_attention_log10_mic_um": numeric(
                    "amp_read_attention_log10_mic_um"
                ),
                "amp_read_lstm_log10_mic_um": numeric(
                    "amp_read_lstm_log10_mic_um"
                ),
                "amplify_probability": numeric("amplify_probability"),
                "amplify_label": text_value("amplify_label"),
                "mmseqs_nearest_identity": numeric("mmseqs_nearest_identity"),
                "mmseqs_nearest_query_coverage": numeric(
                    "mmseqs_nearest_query_coverage"
                ),
                "mmseqs_nearest_target_coverage": numeric(
                    "mmseqs_nearest_target_coverage"
                ),
                "mmseqs_nearest_evalue": numeric("mmseqs_nearest_evalue"),
                "esm2_nearest_cosine_similarity": numeric(
                    "esm2_nearest_cosine_similarity"
                ),
                "peptiverse_predicted_log1p_half_life_hours": numeric(
                    "peptiverse_predicted_log1p_half_life_hours"
                ),
                "peptiverse_predicted_half_life_hours": numeric(
                    "peptiverse_predicted_half_life_hours"
                ),
                "aggrescanai_apr_mean": numeric("aggrescanai_apr_mean"),
                "aggrescanai_apr_max": numeric("aggrescanai_apr_max"),
                "nearest_selected_sequence_similarity": max(similarities, default=0.0),
                "boltz2_pair_iptm": numeric(MetricName.BOLTZ2_PAIR_IPTM),
                "boltz2_pair_iptm_median": numeric(MetricName.BOLTZ2_PAIR_IPTM_MEDIAN),
                "pocket_contact_count": numeric(MetricName.POCKET_CONTACT_COUNT),
                "interface_clash_count": numeric(MetricName.INTERFACE_CLASH_COUNT),
                "structure_support": (
                    structure_support.text_value if structure_support is not None else None
                ),
                "rosetta_dg_separated_reu": numeric(MetricName.ROSETTA_DG_SEPARATED_REU),
                "rosetta_dg_minimum_reu": numeric(MetricName.ROSETTA_DG_MINIMUM_REU),
                "rosetta_peptide_bb_rmsd_angstrom": numeric(
                    MetricName.ROSETTA_PEPTIDE_BB_RMSD_ANGSTROM
                ),
                "rosetta_interface_score": numeric(MetricName.ROSETTA_INTERFACE_SCORE),
                "rosetta_reweighted_score": numeric(MetricName.ROSETTA_REWEIGHTED_SCORE),
                "rosetta_interface_hbonds": numeric(MetricName.ROSETTA_INTERFACE_HBONDS),
                "rosetta_buried_surface_area": numeric(MetricName.ROSETTA_BURIED_SURFACE_AREA),
                "rosetta_nstruct": rosetta_raw.get("nstruct"),
                "rosetta_adapter_version": rosetta_raw.get("adapter_version"),
                "rosetta_score_function": rosetta_raw.get("score_function"),
                "prepack": bool(rosetta_raw.get("prepacked_input_sha256")),
                "pack_input": rosetta_raw.get("pack_input"),
                "pack_separated": rosetta_raw.get("pack_separated"),
                "rosetta_tool_call_id": (
                    str(dg_evaluation.tool_call_id) if dg_evaluation is not None else None
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            row["rosetta_dg_separated_reu"] is None,
            row["rosetta_dg_separated_reu"]
            if row["rosetta_dg_separated_reu"] is not None
            else float("inf"),
            row["sequence"],
        )
    )
    return rows
