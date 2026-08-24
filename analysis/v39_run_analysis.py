# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg
from matplotlib import font_manager
from scipy.stats import mannwhitneyu, spearmanr

DEFAULT_CONTROLLER_RUN_ID = "5557e950-5bd9-551d-ae1d-948f0ca29d0b"
DEFAULT_DATABASE_URL = "postgresql://pepagent:change-me@localhost:55432/pepagent"
REQUIRED_METRICS = [
    "amp_read_log10_mic_um",
    "llamp_log10_mic_um",
    "macrel_amp_probability",
    "toxinpred3_label",
    "toxinpred3_hybrid_score",
    "macrel_hemolysis_label",
    "macrel_hemolysis_probability",
    "net_charge_ph7_4",
    "hydrophobic_ratio_modlamp",
    "hydrophobic_moment_eisenberg",
    "maximum_hydrophobic_run",
    "guruprasad_instability_index",
]
NUMERIC_METRICS = [metric for metric in REQUIRED_METRICS if not metric.endswith("_label")]
LABEL_METRICS = ["toxinpred3_label", "macrel_hemolysis_label"]
LOWER_IS_BETTER = {
    "amp_read_log10_mic_um",
    "llamp_log10_mic_um",
    "toxinpred3_hybrid_score",
    "macrel_hemolysis_probability",
    "maximum_hydrophobic_run",
    "guruprasad_instability_index",
}
HIGHER_IS_BETTER = {
    "macrel_amp_probability",
    "hydrophobic_moment_eisenberg",
}
NON_DIRECTIONAL = {"net_charge_ph7_4", "hydrophobic_ratio_modlamp"}
OOD_NON_GATING = {"guruprasad_instability_index"}
DISPLAY_NAMES = {
    "amp_read_log10_mic_um": "AMP-READ log10 MIC",
    "llamp_log10_mic_um": "LLAMP log10 MIC",
    "macrel_amp_probability": "MACREL AMP probability",
    "toxinpred3_label": "ToxinPred3 label",
    "toxinpred3_hybrid_score": "ToxinPred3 hybrid risk",
    "macrel_hemolysis_label": "MACREL hemolysis label",
    "macrel_hemolysis_probability": "MACREL hemolysis risk",
    "net_charge_ph7_4": "Net charge (pH 7.4)",
    "hydrophobic_ratio_modlamp": "Hydrophobic ratio",
    "hydrophobic_moment_eisenberg": "Hydrophobic moment",
    "maximum_hydrophobic_run": "Maximum hydrophobic run",
    "guruprasad_instability_index": "Guruprasad instability",
}
COHORT_LABELS = {
    "all": "all unique",
    "hard_safety": "hard-safety pass",
    "mature_core": "mature core",
    "structure_selected": "structure selected",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze one v39 exploration controller from PostgreSQL evidence."
    )
    parser.add_argument("--controller-run-id", default=DEFAULT_CONTROLLER_RUN_ID)
    parser.add_argument(
        "--database-url",
        default=os.getenv("PEPAGENT_DATABASE_URL_PLAIN", DEFAULT_DATABASE_URL),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports") / "v39_run_analysis_5557e950_20260824",
    )
    return parser.parse_args()


def configure_plotting() -> None:
    candidates = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    available = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((name for name in candidates if name in available), "DejaVu Sans")
    plt.rcParams.update(
        {
            "font.family": selected,
            "axes.unicode_minus": False,
            "figure.dpi": 130,
            "savefig.dpi": 180,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def fetch_dataframe(
    connection: psycopg.Connection[Any], query: str, params: tuple[Any, ...]
) -> pd.DataFrame:
    cursor = connection.execute(query, params)
    rows = cursor.fetchall()
    return pd.DataFrame(rows, columns=[column.name for column in cursor.description])


def load_evidence(
    database_url: str, controller_run_id: str
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], pd.DataFrame]:
    with psycopg.connect(database_url) as connection:
        decision_row = connection.execute(
            """
            SELECT structured_json, response_sha256, created_at
            FROM agent_decisions
            WHERE run_id = %s::uuid
              AND decision_type = 'v39_cross_round_admission'
              AND status = 'succeeded'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (controller_run_id,),
        ).fetchone()
        if decision_row is None:
            raise RuntimeError("No succeeded v39 cross-round admission decision found")
        decision = dict(decision_row[0])
        decision["_response_sha256"] = decision_row[1]
        decision["_created_at"] = decision_row[2].isoformat()
        source_run_ids = list(decision["source_run_ids"])

        candidates = fetch_dataframe(
            connection,
            """
            SELECT
                c.id::text AS candidate_id,
                c.run_id::text AS run_id,
                c.sequence,
                c.sequence_sha256,
                c.status AS persistence_status,
                c.generation,
                c.metadata_json->>'generator_id' AS generator_id,
                (c.metadata_json->>'seed')::bigint AS seed,
                (c.metadata_json->>'source_ordinal')::integer AS source_ordinal,
                (c.metadata_json->>'raw_rank')::integer AS raw_rank
            FROM candidates c
            WHERE c.run_id = ANY(%s::uuid[])
            ORDER BY c.run_id, c.metadata_json->>'source_ordinal'
            """,
            (source_run_ids,),
        )
        evaluations = fetch_dataframe(
            connection,
            """
            SELECT
                e.candidate_id::text AS candidate_id,
                e.metric_name,
                e.numeric_value,
                e.text_value,
                e.unit,
                e.status AS evaluation_status,
                e.out_of_domain,
                e.created_at
            FROM evaluations e
            JOIN candidates c ON c.id = e.candidate_id
            WHERE c.run_id = ANY(%s::uuid[])
            ORDER BY e.candidate_id, e.metric_name
            """,
            (source_run_ids,),
        )
        occurrence_summary = fetch_dataframe(
            connection,
            """
            SELECT
                o.run_id::text AS run_id,
                COUNT(*)::integer AS raw_occurrences,
                COUNT(*) FILTER (
                    WHERE o.metadata_json->>'disposition' = 'new_unique'
                )::integer AS new_unique_occurrences,
                COUNT(*) FILTER (
                    WHERE o.metadata_json->>'disposition' <> 'new_unique'
                )::integer AS duplicate_or_invalid_occurrences
            FROM candidate_occurrences o
            WHERE o.run_id = ANY(%s::uuid[])
            GROUP BY o.run_id
            """,
            (source_run_ids,),
        )

    numeric = evaluations.pivot(index="candidate_id", columns="metric_name", values="numeric_value")
    labels = evaluations.pivot(index="candidate_id", columns="metric_name", values="text_value")
    metric_wide = numeric.combine_first(labels).reset_index()
    frame = candidates.merge(metric_wide, on="candidate_id", how="left", validate="one_to_one")

    decision_frame = pd.DataFrame(decision["admission"]["decisions"])
    frame = frame.merge(decision_frame, on="candidate_id", how="left", validate="one_to_one")
    selected_ids = set(
        decision.get("observer_decision_projection", {}).get("selected_candidate_ids", [])
    )
    frame["structure_selected"] = frame["candidate_id"].isin(selected_ids)
    frame["hard_safety_pass"] = frame["toxinpred3_label"].eq("Non-Toxin") & frame[
        "macrel_hemolysis_label"
    ].eq("low")
    frame["round"] = frame["run_id"].map(
        {run_id: index + 1 for index, run_id in enumerate(source_run_ids)}
    )
    return frame, evaluations, decision, occurrence_summary


def cohort_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "all": pd.Series(True, index=frame.index),
        "hard_safety": frame["hard_safety_pass"],
        "mature_core": frame["status"].eq("mature_core"),
        "structure_selected": frame["structure_selected"],
    }


def direction(metric: str) -> str:
    if metric in LOWER_IS_BETTER:
        return "lower_better"
    if metric in HIGHER_IS_BETTER:
        return "higher_better"
    return "descriptive_nonmonotonic"


def safe_effect_size(all_values: pd.Series, subset_values: pd.Series) -> tuple[float, float]:
    if subset_values.empty or all_values.empty:
        return math.nan, math.nan
    pooled = math.sqrt((all_values.var(ddof=1) + subset_values.var(ddof=1)) / 2)
    standardized = (subset_values.mean() - all_values.mean()) / pooled if pooled > 0 else math.nan
    statistic, p_value = mannwhitneyu(subset_values, all_values, alternative="two-sided")
    rank_biserial = 2 * statistic / (len(subset_values) * len(all_values)) - 1
    return float(standardized), float(rank_biserial)


def build_metric_summary(frame: pd.DataFrame, evaluations: pd.DataFrame) -> pd.DataFrame:
    masks = cohort_masks(frame)
    rows: list[dict[str, Any]] = []
    for cohort, mask in masks.items():
        subset = frame.loc[mask]
        for metric in NUMERIC_METRICS:
            values = pd.to_numeric(subset[metric], errors="coerce")
            valid = values.dropna()
            if valid.empty:
                continue
            best_index = (
                valid.idxmin()
                if metric in LOWER_IS_BETTER
                else valid.idxmax()
                if metric in HIGHER_IS_BETTER
                else None
            )
            worst_index = (
                valid.idxmax()
                if metric in LOWER_IS_BETTER
                else valid.idxmin()
                if metric in HIGHER_IS_BETTER
                else None
            )
            full_values = pd.to_numeric(frame[metric], errors="coerce").dropna()
            standardized_shift, rank_biserial = safe_effect_size(full_values, valid)
            metric_eval = evaluations.loc[
                evaluations["metric_name"].eq(metric)
                & evaluations["candidate_id"].isin(subset["candidate_id"])
            ]
            rows.append(
                {
                    "cohort": cohort,
                    "metric": metric,
                    "display_name": DISPLAY_NAMES[metric],
                    "direction": direction(metric),
                    "n": int(valid.size),
                    "missing": int(values.isna().sum()),
                    "failed": int((metric_eval["evaluation_status"] != "succeeded").sum()),
                    "ood": int(metric_eval["out_of_domain"].fillna(False).sum()),
                    "min": float(valid.min()),
                    "p10": float(valid.quantile(0.10)),
                    "p25": float(valid.quantile(0.25)),
                    "mean": float(valid.mean()),
                    "median": float(valid.median()),
                    "p75": float(valid.quantile(0.75)),
                    "p90": float(valid.quantile(0.90)),
                    "max": float(valid.max()),
                    "std": float(valid.std(ddof=1)),
                    "best_candidate_id": (
                        frame.loc[best_index, "candidate_id"] if best_index is not None else None
                    ),
                    "best_sequence": (
                        frame.loc[best_index, "sequence"] if best_index is not None else None
                    ),
                    "worst_candidate_id": (
                        frame.loc[worst_index, "candidate_id"] if worst_index is not None else None
                    ),
                    "worst_sequence": (
                        frame.loc[worst_index, "sequence"] if worst_index is not None else None
                    ),
                    "standardized_mean_shift_vs_all": standardized_shift,
                    "rank_biserial_vs_all": rank_biserial,
                }
            )
    return pd.DataFrame(rows)


def build_label_summary(frame: pd.DataFrame, evaluations: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for cohort, mask in cohort_masks(frame).items():
        subset = frame.loc[mask]
        for metric in LABEL_METRICS:
            metric_eval = evaluations.loc[
                evaluations["metric_name"].eq(metric)
                & evaluations["candidate_id"].isin(subset["candidate_id"])
            ]
            counts = subset[metric].fillna("<missing>").value_counts(dropna=False)
            for label, count in counts.items():
                rows.append(
                    {
                        "cohort": cohort,
                        "metric": metric,
                        "label": label,
                        "count": int(count),
                        "percentage": float(count / len(subset) * 100) if len(subset) else math.nan,
                        "failed": int((metric_eval["evaluation_status"] != "succeeded").sum()),
                        "ood": int(metric_eval["out_of_domain"].fillna(False).sum()),
                    }
                )
    return pd.DataFrame(rows)


def utility(frame: pd.DataFrame, metric: str) -> pd.Series:
    values = pd.to_numeric(frame[metric], errors="coerce")
    if metric in LOWER_IS_BETTER:
        return -values
    return values


def build_conflict_summary(frame: pd.DataFrame) -> pd.DataFrame:
    pairs = [
        ("amp_read_log10_mic_um", "llamp_log10_mic_um"),
        ("macrel_amp_probability", "amp_read_log10_mic_um"),
        ("macrel_amp_probability", "llamp_log10_mic_um"),
        ("macrel_amp_probability", "macrel_hemolysis_probability"),
        ("macrel_amp_probability", "toxinpred3_hybrid_score"),
        ("hydrophobic_moment_eisenberg", "macrel_hemolysis_probability"),
        ("net_charge_ph7_4", "macrel_hemolysis_probability"),
        ("hydrophobic_ratio_modlamp", "macrel_hemolysis_probability"),
    ]
    rows: list[dict[str, Any]] = []
    for cohort, mask in {
        "all": cohort_masks(frame)["all"],
        "mature_core": cohort_masks(frame)["mature_core"],
    }.items():
        subset = frame.loc[mask]
        for metric_a, metric_b in pairs:
            a = utility(subset, metric_a)
            b = utility(subset, metric_b)
            valid = a.notna() & b.notna()
            a = a[valid]
            b = b[valid]
            rho, p_value = spearmanr(a, b)
            a_top = a >= a.quantile(0.75)
            b_top = b >= b.quantile(0.75)
            a_bottom = a <= a.quantile(0.25)
            b_bottom = b <= b.quantile(0.25)
            union = (a_top | b_top).sum()
            overlap = (a_top & b_top).sum()
            opposition = ((a_top & b_bottom) | (a_bottom & b_top)).sum()
            rows.append(
                {
                    "cohort": cohort,
                    "metric_a": metric_a,
                    "metric_b": metric_b,
                    "n": int(valid.sum()),
                    "spearman_utility_rho": float(rho),
                    "spearman_p_value": float(p_value),
                    "top_quartile_overlap_n": int(overlap),
                    "top_quartile_jaccard": float(overlap / union) if union else math.nan,
                    "direct_opposition_n": int(opposition),
                    "direct_opposition_pct": float(opposition / valid.sum() * 100),
                }
            )
    return pd.DataFrame(rows)


def build_flow(frame: pd.DataFrame, occurrence_summary: pd.DataFrame) -> pd.DataFrame:
    raw = int(occurrence_summary["raw_occurrences"].sum())
    statuses = frame["status"].value_counts()
    return pd.DataFrame(
        [
            ("raw occurrences", raw),
            ("unique valid sequences", len(frame)),
            ("ToxinPred3 Non-Toxin", int(frame["toxinpred3_label"].eq("Non-Toxin").sum())),
            ("MACREL low hemolysis", int(frame["macrel_hemolysis_label"].eq("low").sum())),
            ("both hard-safety gates", int(frame["hard_safety_pass"].sum())),
            ("promising uncertain", int(statuses.get("promising_uncertain", 0))),
            ("mature core", int(statuses.get("mature_core", 0))),
            ("structure selected", int(frame["structure_selected"].sum())),
        ],
        columns=["stage", "count"],
    )


def build_generator_summary(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.groupby(["round", "run_id", "generator_id"], dropna=False)
    summary = grouped.agg(
        unique_sequences=("candidate_id", "size"),
        hard_safety_pass=("hard_safety_pass", "sum"),
        mature_core=("status", lambda values: int((values == "mature_core").sum())),
        structure_selected=("structure_selected", "sum"),
    ).reset_index()
    summary["raw_budget"] = 600
    summary["unique_yield_pct"] = summary["unique_sequences"] / summary["raw_budget"] * 100
    summary["hard_safety_yield_pct"] = (
        summary["hard_safety_pass"] / summary["unique_sequences"] * 100
    )
    summary["mature_core_yield_pct"] = summary["mature_core"] / summary["unique_sequences"] * 100
    return summary


def plot_overview(
    flow: pd.DataFrame, occurrence_summary: pd.DataFrame, frame: pd.DataFrame, output: Path
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
    stages = flow["stage"].tolist()
    values = flow["count"].to_numpy()
    colors = [
        "#64748b",
        "#2563eb",
        "#0ea5e9",
        "#0ea5e9",
        "#14b8a6",
        "#f59e0b",
        "#16a34a",
        "#7c3aed",
    ]
    axes[0].barh(stages[::-1], values[::-1], color=colors[::-1])
    for index, value in enumerate(values[::-1]):
        axes[0].text(value + max(values) * 0.01, index, f"{value:,}", va="center", fontsize=9)
    axes[0].set_title("Evidence funnel (cohorts are not strictly nested after safety)")
    axes[0].set_xlabel("Count")

    round_counts = frame.groupby("round").size().sort_index()
    raw_by_run = occurrence_summary.set_index("run_id")["raw_occurrences"]
    run_by_round = frame.groupby("round")["run_id"].first()
    raw_counts = pd.Series({r: int(raw_by_run.loc[run_id]) for r, run_id in run_by_round.items()})
    duplicate_counts = raw_counts - round_counts
    x = np.arange(len(round_counts))
    axes[1].bar(x, round_counts, label="unique valid", color="#2563eb")
    axes[1].bar(
        x, duplicate_counts, bottom=round_counts, label="duplicate/invalid", color="#cbd5e1"
    )
    for index, (unique, raw) in enumerate(zip(round_counts, raw_counts, strict=True)):
        axes[1].text(
            index, raw + 25, f"{unique}/{raw}\n{unique / raw:.1%}", ha="center", fontsize=9
        )
    axes[1].set_xticks(x, [f"Round {round_no}" for round_no in round_counts.index])
    axes[1].set_ylim(0, max(raw_counts) * 1.16)
    axes[1].set_ylabel("Occurrences")
    axes[1].set_title("Per-round unique-sequence yield")
    axes[1].legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_metric_distributions(frame: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(5, 2, figsize=(14, 18))
    core = frame["status"].eq("mature_core")
    for axis, metric in zip(axes.flat, NUMERIC_METRICS, strict=True):
        all_values = pd.to_numeric(frame[metric], errors="coerce").dropna()
        core_values = pd.to_numeric(frame.loc[core, metric], errors="coerce").dropna()
        low, high = all_values.quantile([0.005, 0.995])
        bins = np.linspace(low, high, 35) if high > low else 10
        axis.hist(
            all_values.clip(low, high),
            bins=bins,
            density=True,
            alpha=0.45,
            color="#94a3b8",
            label=f"all n={len(all_values)}",
        )
        axis.hist(
            core_values.clip(low, high),
            bins=bins,
            density=True,
            alpha=0.60,
            color="#16a34a",
            label=f"core n={len(core_values)}",
        )
        axis.axvline(all_values.median(), color="#475569", linestyle="--", linewidth=1)
        axis.axvline(core_values.median(), color="#15803d", linestyle="-", linewidth=1.3)
        suffix = " (OOD/non-gating)" if metric in OOD_NON_GATING else ""
        axis.set_title(DISPLAY_NAMES[metric] + suffix, fontsize=10)
        axis.legend(frameon=False, fontsize=8)
    fig.suptitle(
        "Score distributions: all unique sequences vs mature core\nValues clipped to all-pool P0.5–P99.5 for readability",
        y=1.005,
        fontsize=14,
    )
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_correlation(frame: pd.DataFrame, output: Path) -> None:
    objective_metrics = [
        "amp_read_log10_mic_um",
        "llamp_log10_mic_um",
        "macrel_amp_probability",
        "toxinpred3_hybrid_score",
        "macrel_hemolysis_probability",
        "net_charge_ph7_4",
        "hydrophobic_ratio_modlamp",
        "hydrophobic_moment_eisenberg",
        "maximum_hydrophobic_run",
    ]
    utilities = pd.DataFrame({metric: utility(frame, metric) for metric in objective_metrics})
    correlations = utilities.corr(method="spearman")
    fig, axis = plt.subplots(figsize=(10.5, 9))
    image = axis.imshow(correlations, cmap="RdBu_r", vmin=-1, vmax=1)
    short_names = [
        DISPLAY_NAMES[metric].replace(" probability", " prob.") for metric in objective_metrics
    ]
    axis.set_xticks(range(len(short_names)), short_names, rotation=55, ha="right", fontsize=8)
    axis.set_yticks(range(len(short_names)), short_names, fontsize=8)
    for row in range(len(short_names)):
        for column in range(len(short_names)):
            value = correlations.iloc[row, column]
            axis.text(
                column,
                row,
                f"{value:.2f}",
                ha="center",
                va="center",
                fontsize=7,
                color="white" if abs(value) > 0.55 else "black",
            )
    axis.set_title(
        "Spearman correlation of oriented utilities\nPositive = objectives improve together; negative = trade-off"
    )
    fig.colorbar(image, ax=axis, fraction=0.046, pad=0.04, label="Spearman ρ")
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_conflicts(frame: pd.DataFrame, output: Path) -> None:
    core = frame["status"].eq("mature_core")
    panels = [
        ("amp_read_log10_mic_um", "llamp_log10_mic_um", "MIC-model agreement"),
        ("macrel_amp_probability", "macrel_hemolysis_probability", "Activity vs hemolysis risk"),
        ("net_charge_ph7_4", "macrel_hemolysis_probability", "Charge vs hemolysis risk"),
        ("hydrophobic_ratio_modlamp", "toxinpred3_hybrid_score", "Hydrophobicity vs toxicity risk"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    for axis, (x_metric, y_metric, title) in zip(axes.flat, panels, strict=True):
        axis.scatter(
            frame[x_metric],
            frame[y_metric],
            s=8,
            alpha=0.14,
            color="#64748b",
            rasterized=True,
            label="all",
        )
        axis.scatter(
            frame.loc[core, x_metric],
            frame.loc[core, y_metric],
            s=40,
            alpha=0.9,
            color="#16a34a",
            edgecolor="white",
            linewidth=0.35,
            label="mature core",
        )
        rho, _ = spearmanr(frame[x_metric], frame[y_metric], nan_policy="omit")
        axis.set_xlabel(DISPLAY_NAMES[x_metric])
        axis.set_ylabel(DISPLAY_NAMES[y_metric])
        axis.set_title(f"{title} (raw-score ρ={rho:.2f})")
        axis.legend(frameon=False, fontsize=8)
    fig.suptitle(
        "Concrete objective relationships (predictions/descriptors, not experiments)", fontsize=14
    )
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_generator_yield(summary: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    generators = sorted(summary["generator_id"].dropna().unique())
    rounds = sorted(summary["round"].unique())
    width = 0.23
    x = np.arange(len(rounds))
    palette = ["#2563eb", "#f59e0b", "#14b8a6"]
    for index, generator in enumerate(generators):
        subset = summary[summary["generator_id"].eq(generator)].set_index("round").reindex(rounds)
        axes[0].bar(
            x + (index - 1) * width,
            subset["unique_yield_pct"],
            width,
            label=generator,
            color=palette[index % len(palette)],
        )
        axes[1].bar(
            x + (index - 1) * width,
            subset["hard_safety_yield_pct"],
            width,
            label=generator,
            color=palette[index % len(palette)],
        )
    for axis, title, ylabel in [
        (axes[0], "Unique yield from 600 raw/generator/round", "Unique yield (%)"),
        (axes[1], "Hard-safety pass among unique sequences", "Hard-safety pass (%)"),
    ]:
        axis.set_xticks(x, [f"R{round_no}" for round_no in rounds])
        axis.set_title(title)
        axis.set_ylabel(ylabel)
        axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def percentile_utility(frame: pd.DataFrame, metric: str) -> pd.Series:
    values = utility(frame, metric)
    return values.rank(pct=True, method="average")


def plot_mature_core(frame: pd.DataFrame, output: Path) -> None:
    metrics = [
        "amp_read_log10_mic_um",
        "llamp_log10_mic_um",
        "macrel_amp_probability",
        "toxinpred3_hybrid_score",
        "macrel_hemolysis_probability",
        "hydrophobic_moment_eisenberg",
        "maximum_hydrophobic_run",
    ]
    core = frame.loc[frame["status"].eq("mature_core")].copy()
    matrix = pd.DataFrame(
        {metric: percentile_utility(frame, metric).loc[core.index] for metric in metrics},
        index=core.index,
    )
    core["top_quartile_axes"] = (matrix >= 0.75).sum(axis=1)
    order = core.sort_values(["top_quartile_axes", "sequence"], ascending=[False, True]).index
    matrix = matrix.loc[order]
    labels = [
        f"{frame.loc[index, 'sequence']} ({str(frame.loc[index, 'candidate_id'])[:8]})"
        for index in order
    ]
    fig, axis = plt.subplots(figsize=(12, 12))
    image = axis.imshow(matrix.to_numpy(), aspect="auto", cmap="YlGnBu", vmin=0, vmax=1)
    axis.set_xticks(
        range(len(metrics)),
        [DISPLAY_NAMES[metric] for metric in metrics],
        rotation=50,
        ha="right",
        fontsize=8,
    )
    axis.set_yticks(range(len(labels)), labels, fontsize=6.5)
    axis.set_title(
        "Mature-core trade-off map\nPercentile utility against all 6,182 sequences; no weighted total"
    )
    fig.colorbar(image, ax=axis, fraction=0.025, pad=0.02, label="All-pool utility percentile")
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fmt(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NA"
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    return f"{float(value):.{digits}f}"


def metric_table_markdown(summary: pd.DataFrame) -> str:
    all_rows = summary[summary["cohort"].eq("all")].set_index("metric")
    core_rows = summary[summary["cohort"].eq("mature_core")].set_index("metric")
    lines = [
        "| 指标 | 方向 | 全体均值/中位数 | 核心均值/中位数 | 全体 P10–P90 | 核心相对变化 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for metric in NUMERIC_METRICS:
        all_row = all_rows.loc[metric]
        core_row = core_rows.loc[metric]
        direction_text = {
            "lower_better": "越低越有利",
            "higher_better": "越高越有利",
            "descriptive_nonmonotonic": "非单调描述",
        }[all_row["direction"]]
        shift = core_row["standardized_mean_shift_vs_all"]
        if metric in LOWER_IS_BETTER:
            practical = "改善" if shift < -0.2 else "变差" if shift > 0.2 else "接近"
        elif metric in HIGHER_IS_BETTER:
            practical = "改善" if shift > 0.2 else "变差" if shift < -0.2 else "接近"
        else:
            practical = "分布位移"
        lines.append(
            f"| {DISPLAY_NAMES[metric]} | {direction_text} | {fmt(all_row['mean'])}/{fmt(all_row['median'])} | "
            f"{fmt(core_row['mean'])}/{fmt(core_row['median'])} | {fmt(all_row['p10'])}–{fmt(all_row['p90'])} | "
            f"{practical}（标准化位移 {fmt(shift, 2)}） |"
        )
    return "\n".join(lines)


def detailed_metric_appendix(summary: pd.DataFrame) -> str:
    rows = summary[summary["cohort"].eq("all")].set_index("metric")
    lines = [
        "| 打分器 | n / 缺失 / 失败 / OOD | min / P10 / P25 / mean / median / P75 / P90 / max / SD | 最好序列 | 最差序列 |",
        "|---|---:|---|---|---|",
    ]
    for metric in NUMERIC_METRICS:
        row = rows.loc[metric]
        statistics = " / ".join(
            fmt(row[column])
            for column in ["min", "p10", "p25", "mean", "median", "p75", "p90", "max", "std"]
        )
        best = row["best_sequence"] if metric not in NON_DIRECTIONAL else "不定义单调最好"
        worst = row["worst_sequence"] if metric not in NON_DIRECTIONAL else "不定义单调最差"
        lines.append(
            f"| {DISPLAY_NAMES[metric]} | {int(row['n'])} / {int(row['missing'])} / "
            f"{int(row['failed'])} / {int(row['ood'])} | {statistics} | `{best}` | `{worst}` |"
        )
    return "\n".join(lines)


def detailed_label_appendix(label_summary: pd.DataFrame) -> str:
    rows = label_summary[label_summary["cohort"].eq("all")]
    lines = [
        "| 标签打分器 | 类别分布 | 缺失 / 失败 / OOD | 有利类别 |",
        "|---|---|---:|---|",
    ]
    for metric in LABEL_METRICS:
        subset = rows[rows["metric"].eq(metric)]
        distribution = "; ".join(
            f"{row.label}: {int(row.count):,} ({float(row.percentage):.1f}%)"
            for row in subset.itertuples()
        )
        missing = int(subset.loc[subset["label"].eq("<missing>"), "count"].sum())
        failed = int(subset["failed"].max()) if not subset.empty else 0
        ood = int(subset["ood"].max()) if not subset.empty else 0
        favorable = "Non-Toxin" if metric == "toxinpred3_label" else "low"
        lines.append(
            f"| {DISPLAY_NAMES[metric]} | {distribution} | {missing} / {failed} / {ood} | {favorable} |"
        )
    return "\n".join(lines)


def build_report(
    frame: pd.DataFrame,
    evaluations: pd.DataFrame,
    decision: dict[str, Any],
    occurrence_summary: pd.DataFrame,
    metric_summary: pd.DataFrame,
    label_summary: pd.DataFrame,
    conflict_summary: pd.DataFrame,
    generator_summary: pd.DataFrame,
    output_dir: Path,
) -> str:
    total = len(frame)
    raw = int(occurrence_summary["raw_occurrences"].sum())
    hard_safety = int(frame["hard_safety_pass"].sum())
    core = frame[frame["status"].eq("mature_core")]
    structure = frame[frame["structure_selected"]]
    rejected = int(frame["status"].eq("rejected").sum())
    uncertain = int(frame["status"].eq("promising_uncertain").sum())
    all_conflicts = conflict_summary[conflict_summary["cohort"].eq("all")]

    def conflict(metric_a: str, metric_b: str) -> pd.Series:
        row = all_conflicts[
            all_conflicts["metric_a"].eq(metric_a) & all_conflicts["metric_b"].eq(metric_b)
        ]
        return row.iloc[0]

    mic_conflict = conflict("amp_read_log10_mic_um", "llamp_log10_mic_um")
    activity_hemo = conflict("macrel_amp_probability", "macrel_hemolysis_probability")
    activity_toxin = conflict("macrel_amp_probability", "toxinpred3_hybrid_score")
    reasons = Counter(
        reason for item in decision["admission"]["decisions"] for reason in item.get("reasons", [])
    )

    generator_rollup = generator_summary.groupby("generator_id").agg(
        unique_sequences=("unique_sequences", "sum"),
        hard_safety_pass=("hard_safety_pass", "sum"),
        mature_core=("mature_core", "sum"),
        structure_selected=("structure_selected", "sum"),
    )
    generator_rollup["hard_safety_pct"] = (
        generator_rollup["hard_safety_pass"] / generator_rollup["unique_sequences"] * 100
    )

    report = f"""# v39 四轮序列探索：数据分析与可视化报告

生成时间：{datetime.now(UTC).isoformat()}
用户给定任务：`019fb225-0b2b-7b20-b258-24c1924f560e`（Codex 任务“MVP”，不是 PostgreSQL run ID）
权威控制 run：`{decision["run_id"]}`
跨轮决策 SHA-256：`{decision["_response_sha256"]}`

## 一句话结论

这次 run 的搜索广度和数据完整性是合格的：7,200 次生成得到 {total:,} 条唯一序列，12 个打分器全部覆盖；但“总体分数理想”只能说一半——硬安全门把候选压到 {hard_safety:,} 条（{hard_safety / total:.1%}），最终 mature core 只有 {len(core)} 条（{len(core) / total:.2%}），且两个 MIC 模型和 MACREL 活性模型并不一致，因此当前结果适合做结构确认和实验假设组合，不足以宣布单一冠军。

## 1. 分母与当前阶段

- 原始生成 occurrence：{raw:,}（4 轮 × 1,800）。
- 有效唯一序列：{total:,}，整体去重/无效损耗 {raw - total:,}（{(raw - total) / raw:.1%}）。
- 每条完整评价：12/12；总 Evaluation {len(evaluations):,}。
- 两个硬安全标签都通过：{hard_safety:,}（{hard_safety / total:.1%}）。
- 决策状态：rejected {rejected:,}；promising_uncertain {uncertain:,}；mature_core {len(core):,}。
- 进入后续双靶点结构预算：{len(structure):,}（{int(core["candidate_id"].isin(structure["candidate_id"]).sum())} 条核心 + {len(structure) - int(core["candidate_id"].isin(structure["candidate_id"]).sum())} 条安全探索候选）。
- 控制 run 和四个 child run 在数据库仍标记 `running`；结构/最终组合/replay 未完成，所以本文是 provisional 序列阶段分析。

![运行漏斗与每轮去重产率](01_run_overview.png)

## 2. 分数是否理想

{metric_table_markdown(metric_summary)}

判断：mature core 的确定性改善主要来自 ToxinPred3 风险、MACREL 溶血风险和 LLAMP MIC；AMP-READ MIC 与 MACREL AMP probability 没有同步改善。这不是 bug，而是多模型目标确实不一致。净电荷和疏水比例不是单调目标；核心整体更克制，说明筛选没有简单追逐“越正、越疏水越好”。Guruprasad instability 有大量短肽域外标记，只能观察，不能据其数值淘汰候选。

![所有候选与成熟核心的分数分布](02_metric_distributions.png)

标签硬门的具体效果：

- ToxinPred3 Non-Toxin：{int(frame["toxinpred3_label"].eq("Non-Toxin").sum()):,}/{total:,}（{frame["toxinpred3_label"].eq("Non-Toxin").mean():.1%}）。
- MACREL low hemolysis：{int(frame["macrel_hemolysis_label"].eq("low").sum()):,}/{total:,}（{frame["macrel_hemolysis_label"].eq("low").mean():.1%}）。
- mature core：两类标签均 100% 通过；这证明规则执行有效，但不等于实验安全。

## 3. 目标冲突关系

相关热图已把每个有方向的指标转换为“数值越高越有利”的秩，因此负相关可直接读作冲突。

![目标效用相关热图](03_metric_correlations.png)

![四个具体目标关系](04_objective_conflicts.png)

最重要的量化结果：

- 两个 MIC 模型：效用 Spearman ρ={mic_conflict["spearman_utility_rho"]:.3f}；两者各自前 25% 的 Jaccard 仅 {mic_conflict["top_quartile_jaccard"]:.3f}，直接对立率 {mic_conflict["direct_opposition_pct"]:.1f}%。二者不能互相替代。
- MACREL 活性 vs MACREL 溶血风险：效用 ρ={activity_hemo["spearman_utility_rho"]:.3f}，直接对立率 {activity_hemo["direct_opposition_pct"]:.1f}%。活性提高伴随溶血风险上升的趋势是当前最实际的 trade-off 之一。
- MACREL 活性 vs ToxinPred3 风险：效用 ρ={activity_toxin["spearman_utility_rho"]:.3f}，直接对立率 {activity_toxin["direct_opposition_pct"]:.1f}%。这是另一条活性—安全张力。
- 净电荷/疏水比例对安全风险呈明显非线性，单看相关系数不足；散点图显示极端区风险更集中，new run 不应继续无边界提高电荷或疏水性。

筛选失败原因（同一候选可有多项）：MACREL 溶血标签失败 {reasons["label_gate_failed:macrel_hemolysis_label"]:,}；ToxinPred3 标签失败 {reasons["label_gate_failed:toxinpred3_label"]:,}；rank instability {reasons["rank_instability"]:,}；超出结构预算 {reasons["outside_frozen_structure_budget"]:,}。

## 4. 四轮与生成器表现

![生成器逐轮产率](05_generator_yield.png)

| generator | 唯一序列 | 硬安全通过 | 硬安全率 | mature core | 结构入选 |
|---|---:|---:|---:|---:|---:|
"""
    for generator, row in generator_rollup.sort_index().iterrows():
        report += f"| {generator} | {int(row['unique_sequences']):,} | {int(row['hard_safety_pass']):,} | {row['hard_safety_pct']:.1f}% | {int(row['mature_core'])} | {int(row['structure_selected'])} |\n"

    report += f"""

实用解释：比较 generator 时应同时看唯一产率、硬安全产率和核心/结构贡献，不能只看 raw 行数。四轮没有出现唯一产率崩塌，但总去重损耗 {(raw - total) / raw:.1%}，说明继续增加完全相同策略的轮次会出现边际收益下降；new run 更值得扩展低覆盖序列家族和安全邻域，而不是简单重复 seed。

## 5. mature core 的具体情况

下图把 39 条核心在 7 个主要目标上换成相对于全体 6,182 条的百分位。它不是加权总分；每一行都保留自己的优势与短板。

![成熟核心多目标权衡图](06_mature_core_tradeoffs.png)

具体应优先人工复核的尾部：

- 核心中 `maximum_hydrophobic_run` 最大者：`{core.loc[core["maximum_hydrophobic_run"].astype(float).idxmax(), "sequence"]}`，连续疏水段 {float(core["maximum_hydrophobic_run"].max()):.0f}；虽然标签门通过，仍需结构/聚集与膜损伤风险复核。
- 核心中 MACREL 溶血概率最大者：`{core.loc[core["macrel_hemolysis_probability"].astype(float).idxmax(), "sequence"]}`，概率 {float(core["macrel_hemolysis_probability"].max()):.3f}；接近标签边界的候选不应仅凭 low 标签视为安全。
- 核心中 AMP-READ MIC 最差者：`{core.loc[core["amp_read_log10_mic_um"].astype(float).idxmax(), "sequence"]}`，log10 MIC {float(core["amp_read_log10_mic_um"].max()):.3f}（约 {10 ** float(core["amp_read_log10_mic_um"].max()):.1f} µM）。它能进入核心是因为其他轴保持非支配优势，而不是 AMP-READ 预测优秀。
- 核心中 LLAMP MIC 最差者：`{core.loc[core["llamp_log10_mic_um"].astype(float).idxmax(), "sequence"]}`，log10 MIC {float(core["llamp_log10_mic_um"].max()):.3f}（约 {10 ** float(core["llamp_log10_mic_um"].max()):.1f} µM）。

## 6. 证据不足与不能证明的内容

1. 没有实验 MIC、杀菌曲线、细胞毒性或人 RBC 溶血；所有活性/安全结论都是模型预测。
2. 当前 MIC 模型不是病原菌株、培养基、暴露时间条件化的实验端点，log10 MIC 只能同模型内排序。
3. 两个硬安全标签并非独立湿实验；MACREL 与已用数据族的独立性/校准限制仍然存在。
4. Guruprasad instability 对短肽大量 OOD；没有 serum/protease、溶解度、聚集或货架期证据。
5. 双靶点 native/wrong-pocket 结构、跨 seed 稳定性、Rosetta 相对能量和最终 replay 尚未完成；不能声称 GyrA/PBP2a 结合、亲和力或选择性。
6. 未提供 sequence-family key/聚类证据，无法严谨判断 6,182 条序列覆盖了多少独立家族，也无法证明搜索饱和。
7. 只有一个多轮 schedule；没有同合同独立重复、对照策略或 paired ablation，不能把分布位移归因于某个 Agent 改动。

## 附录 A：逐打分器完整统计

数值顺序固定为 `min / P10 / P25 / mean / median / P75 / P90 / max / SD`。MIC 为 log10(µM)：例如 1、2、3 分别约等于 10、100、1,000 µM；越低越有利。极值仅描述当前计算证据，不是新阈值。

{detailed_metric_appendix(metric_summary)}

{detailed_label_appendix(label_summary)}

## 7. new run 建议（按实用优先级）

1. **先闭合当前 48 条结构证据，不重跑本轮。** 完成双靶点 × native/wrong-pocket × 3 seed × 16 Rosetta，报告每候选跨 seed 一致性与 native-control 差；否则再生成更多序列不会解决最关键的不确定性。
2. **下一序列 run 改为冲突定向探索。** 分层覆盖：低/中净电荷、低/中疏水比例、不同长度和 scaffold；对“MACREL 活性高但溶血/毒性风险高”“LLAMP 好但 AMP-READ 差”分别建立 challenger cells，保留 parent/control，不做单向增正电。
3. **补 family 与 novelty 证据。** 每条 Candidate 持久化 `sequence_family_key`，用预注册的 identity/coverage 规则计算 family yield、历史新颖率和每轮新增 family；连续两轮 family/Pareto extension 停滞才换策略。
4. **模型冲突用独立证据解决，不用加权平均掩盖。** 为双 MIC 模型增加菌株/条件化外部校准或独立模型；报告 rank agreement、top-k overlap 和校准误差。没有校准前保持两个 Pareto 轴。
5. **新增真实开发性端点后再设硬门。** 优先小规模 reference-pilot 锁定人 RBC 溶血、原代皮肤细胞毒性、serum/protease stability、solubility/aggregation；当前 39 条只作盲测，不参与阈值拟合。
6. **实验预算有限时采用分层组合，而非冠军。** 从 39 条核心中按“活性偏强 / 安全偏强 / 理化平衡 / 模型冲突代表”各保留若干条，并加入已知对照；最终数量由实验预算决定，不从当前预测强行产生唯一 winner。

## 8. 可复现产物

- `candidate_metrics.csv`：6,182 条候选、12 指标、决策状态与来源。
- `metric_summary.csv` / `label_summary.csv`：全体、硬安全池、mature core、结构池的完整统计。
- `conflict_summary.csv`：目标对的秩相关、前四分位重叠、直接对立率。
- `generator_round_summary.csv`：round × generator 产率。
- `mature_core_candidates.csv`：39 条核心明细。
- `data_quality.csv`：逐打分器完整性、失败与 OOD。
- `analysis_manifest.json`：输入 run、决策 SHA 和全部输出 SHA-256。

科学边界：本报告是同协议的计算探索诊断，不是实验活性、安全、靶点结合、亲和力或临床结论。
"""
    return report


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_plotting()

    frame, evaluations, decision, occurrence_summary = load_evidence(
        args.database_url, args.controller_run_id
    )
    if len(frame) != frame["sequence_sha256"].nunique():
        raise RuntimeError("Cross-round candidate frame is not exact-sequence unique")
    observed_metrics = set(evaluations["metric_name"].unique())
    if observed_metrics != set(REQUIRED_METRICS):
        raise RuntimeError(
            f"Metric contract drift: expected {sorted(REQUIRED_METRICS)}, observed {sorted(observed_metrics)}"
        )
    per_candidate_counts = evaluations.groupby("candidate_id")["metric_name"].nunique()
    if not per_candidate_counts.eq(len(REQUIRED_METRICS)).all():
        raise RuntimeError("At least one candidate lacks complete 12-metric score-all evidence")

    metric_summary = build_metric_summary(frame, evaluations)
    label_summary = build_label_summary(frame, evaluations)
    conflict_summary = build_conflict_summary(frame)
    flow = build_flow(frame, occurrence_summary)
    generator_summary = build_generator_summary(frame)
    data_quality = (
        evaluations.groupby("metric_name")
        .agg(
            rows=("candidate_id", "size"),
            unique_candidates=("candidate_id", "nunique"),
            failed=("evaluation_status", lambda values: int((values != "succeeded").sum())),
            ood=("out_of_domain", lambda values: int(values.fillna(False).sum())),
            numeric_missing=("numeric_value", lambda values: int(values.isna().sum())),
            text_missing=("text_value", lambda values: int(values.isna().sum())),
        )
        .reset_index()
    )
    data_quality["expected_value_type"] = np.where(
        data_quality["metric_name"].isin(LABEL_METRICS), "text", "numeric"
    )
    data_quality["applicable_value_missing"] = np.where(
        data_quality["expected_value_type"].eq("text"),
        data_quality["text_missing"],
        data_quality["numeric_missing"],
    )

    export_frame = frame.copy()
    export_frame["reasons"] = export_frame["reasons"].apply(
        lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
    )
    outputs: dict[str, Path] = {
        "candidate_metrics": output_dir / "candidate_metrics.csv",
        "metric_summary": output_dir / "metric_summary.csv",
        "label_summary": output_dir / "label_summary.csv",
        "conflict_summary": output_dir / "conflict_summary.csv",
        "cohort_flow": output_dir / "cohort_flow.csv",
        "generator_round_summary": output_dir / "generator_round_summary.csv",
        "mature_core_candidates": output_dir / "mature_core_candidates.csv",
        "data_quality": output_dir / "data_quality.csv",
    }
    csv_options = {
        "index": False,
        "encoding": "utf-8-sig",
        "lineterminator": "\n",
    }
    export_frame.to_csv(outputs["candidate_metrics"], **csv_options)
    metric_summary.to_csv(outputs["metric_summary"], **csv_options)
    label_summary.to_csv(outputs["label_summary"], **csv_options)
    conflict_summary.to_csv(outputs["conflict_summary"], **csv_options)
    flow.to_csv(outputs["cohort_flow"], **csv_options)
    generator_summary.to_csv(outputs["generator_round_summary"], **csv_options)
    export_frame.loc[export_frame["status"].eq("mature_core")].to_csv(
        outputs["mature_core_candidates"], **csv_options
    )
    data_quality.to_csv(outputs["data_quality"], **csv_options)

    figures = {
        "run_overview": output_dir / "01_run_overview.png",
        "metric_distributions": output_dir / "02_metric_distributions.png",
        "metric_correlations": output_dir / "03_metric_correlations.png",
        "objective_conflicts": output_dir / "04_objective_conflicts.png",
        "generator_yield": output_dir / "05_generator_yield.png",
        "mature_core_tradeoffs": output_dir / "06_mature_core_tradeoffs.png",
    }
    plot_overview(flow, occurrence_summary, frame, figures["run_overview"])
    plot_metric_distributions(frame, figures["metric_distributions"])
    plot_correlation(frame, figures["metric_correlations"])
    plot_conflicts(frame, figures["objective_conflicts"])
    plot_generator_yield(generator_summary, figures["generator_yield"])
    plot_mature_core(frame, figures["mature_core_tradeoffs"])

    report = build_report(
        frame,
        evaluations,
        decision,
        occurrence_summary,
        metric_summary,
        label_summary,
        conflict_summary,
        generator_summary,
        output_dir,
    )
    report_path = output_dir / "REPORT.zh-CN.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")

    artifact_paths = [*outputs.values(), *figures.values(), report_path]
    manifest = {
        "schema_version": "v39.pragmatic-run-analysis.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "controller_run_id": args.controller_run_id,
        "source_run_ids": decision["source_run_ids"],
        "cross_round_decision_sha256": decision["_response_sha256"],
        "candidate_count": len(frame),
        "evaluation_count": len(evaluations),
        "metric_count": len(REQUIRED_METRICS),
        "mature_core_count": int(frame["status"].eq("mature_core").sum()),
        "structure_selected_count": int(frame["structure_selected"].sum()),
        "artifacts": {
            path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifact_paths
        },
        "limitations": [
            "computational_predictions_and_descriptors_only",
            "structure_and_final_replay_pending",
            "guruprasad_instability_short_peptide_ood_non_gating",
            "no_experimental_activity_or_safety_claim",
        ],
    }
    manifest_path = output_dir / "analysis_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
