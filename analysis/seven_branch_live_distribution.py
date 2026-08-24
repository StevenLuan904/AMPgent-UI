from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import statistics
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import text

from pepagent.db.session import SessionFactory


METRICS: dict[str, dict[str, str]] = {
    "amp_read_log10_mic_um": {"direction": "min", "kind": "model_prediction"},
    "llamp_log10_mic_um": {"direction": "min", "kind": "model_prediction"},
    "macrel_amp_probability": {"direction": "max", "kind": "model_prediction"},
    "toxinpred3_label": {"direction": "Non-Toxin", "kind": "model_prediction"},
    "toxinpred3_hybrid_score": {"direction": "min", "kind": "model_prediction"},
    "macrel_hemolysis_label": {"direction": "low", "kind": "model_prediction"},
    "macrel_hemolysis_probability": {"direction": "min", "kind": "model_prediction"},
    "net_charge_ph7_4": {"direction": "descriptive", "kind": "descriptor"},
    "hydrophobic_ratio_modlamp": {"direction": "descriptive", "kind": "descriptor"},
    "hydrophobic_moment_eisenberg": {"direction": "max", "kind": "descriptor"},
    "maximum_hydrophobic_run": {"direction": "min", "kind": "descriptor"},
    "guruprasad_instability_index": {
        "direction": "min_non_gating",
        "kind": "descriptor",
    },
    "conditional_nll": {
        "direction": "min",
        "kind": "target_sequence_model_prediction",
    },
    "conditional_ppl": {
        "direction": "min",
        "kind": "target_sequence_model_prediction",
    },
}
LABEL_METRICS = {"toxinpred3_label", "macrel_hemolysis_label"}


def _quantile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q))


def _numeric_summary(
    rows: list[dict[str, Any]], *, direction: str
) -> dict[str, Any]:
    values = [float(row["numeric_value"]) for row in rows]
    if not values:
        return {}
    if direction.startswith("min"):
        best = min(rows, key=lambda row: (float(row["numeric_value"]), row["candidate_id"]))
        worst = max(rows, key=lambda row: (float(row["numeric_value"]), row["candidate_id"]))
    elif direction == "max":
        best = max(rows, key=lambda row: (float(row["numeric_value"]), row["candidate_id"]))
        worst = min(rows, key=lambda row: (float(row["numeric_value"]), row["candidate_id"]))
    else:
        best = worst = None
    return {
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "standard_deviation": statistics.pstdev(values),
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "best_candidate_id": best["candidate_id"] if best else None,
        "best_sequence": best["sequence"] if best else None,
        "best_value": float(best["numeric_value"]) if best else None,
        "worst_candidate_id": worst["candidate_id"] if worst else None,
        "worst_sequence": worst["sequence"] if worst else None,
        "worst_value": float(worst["numeric_value"]) if worst else None,
    }


async def build_report(controller_run_id: uuid.UUID) -> dict[str, Any]:
    async with SessionFactory() as session:
        runs = (
            await session.execute(
                text(
                    """select id::text, spec_json->>'branch_key' branch_key, status
                    from experiment_runs where parent_run_id=:controller
                    order by created_at, id"""
                ),
                {"controller": controller_run_id},
            )
        ).mappings().all()
        rows = (
            await session.execute(
                text(
                    """select r.id::text run_id, r.spec_json->>'branch_key' branch_key,
                    c.id::text candidate_id, c.sequence, e.metric_name,
                    e.numeric_value, e.text_value, e.unit, e.status, e.out_of_domain
                    from experiment_runs r
                    join candidates c on c.run_id=r.id
                    join evaluations e on e.candidate_id=c.id
                    where r.parent_run_id=:controller
                    and e.metric_name=any(:metrics)
                    order by r.created_at, c.id, e.metric_name"""
                ),
                {"controller": controller_run_id, "metrics": list(METRICS)},
            )
        ).mappings().all()
        decisions = (
            await session.execute(
                text(
                    """select distinct on (d.run_id) d.run_id::text, d.response_text
                    from agent_decisions d join experiment_runs r on r.id=d.run_id
                    where r.parent_run_id=:controller
                    and d.decision_type='v38_sequence_maturity_admission'
                    order by d.run_id, d.created_at desc"""
                ),
                {"controller": controller_run_id},
            )
        ).mappings().all()

    cohort_ids: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in rows:
        cohort_ids[row["branch_key"]]["all"].add(row["candidate_id"])
    for row in decisions:
        payload = json.loads(row["response_text"])["admission"]
        branch = next(item["branch_key"] for item in runs if item["id"] == row["run_id"])
        cohort_ids[branch]["mature_core"] = {
            str(item) for item in payload["mature_core_candidate_ids"]
        }
        cohort_ids[branch]["selected_exploration"] = {
            str(item) for item in payload["exploration_candidate_ids"]
        }
        cohort_ids[branch]["qualified"] = (
            cohort_ids[branch]["mature_core"]
            | cohort_ids[branch]["selected_exploration"]
        )

    by_branch_metric: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        by_branch_metric[(raw["branch_key"], raw["metric_name"])].append(dict(raw))

    summaries: list[dict[str, Any]] = []
    for run in runs:
        branch = run["branch_key"]
        for cohort in ("all", "mature_core", "selected_exploration", "qualified"):
            ids = cohort_ids[branch].get(cohort, set())
            for metric, contract in METRICS.items():
                metric_rows = [
                    row
                    for row in by_branch_metric.get((branch, metric), [])
                    if row["candidate_id"] in ids
                ]
                succeeded = [row for row in metric_rows if row["status"] == "succeeded"]
                valid = [
                    row
                    for row in succeeded
                    if (row["text_value"] is not None if metric in LABEL_METRICS else row["numeric_value"] is not None)
                ]
                item: dict[str, Any] = {
                    "branch_key": branch,
                    "run_status": run["status"],
                    "cohort": cohort,
                    "cohort_n": len(ids),
                    "metric": metric,
                    "evidence_kind": contract["kind"],
                    "favorable_direction": contract["direction"],
                    "valid_n": len(valid),
                    "missing_n": max(0, len(ids) - len(valid)),
                    "failed_n": sum(row["status"] != "succeeded" for row in metric_rows),
                    "ood_n": sum(bool(row["out_of_domain"]) for row in valid),
                    "unit": next((row["unit"] for row in valid if row["unit"]), None),
                }
                if metric in LABEL_METRICS:
                    counts: dict[str, int] = defaultdict(int)
                    for row in valid:
                        counts[str(row["text_value"])] += 1
                    item["categories"] = {
                        key: {
                            "count": value,
                            "percentage": (100.0 * value / len(valid)) if valid else math.nan,
                        }
                        for key, value in sorted(counts.items())
                    }
                else:
                    item.update(
                        _numeric_summary(valid, direction=contract["direction"])
                    )
                summaries.append(item)
    return {
        "schema_version": "ampgent.seven-branch-live-score-distribution.1",
        "controller_run_id": str(controller_run_id),
        "scope_note": "computational predictions/descriptors only; no wet-lab measurements",
        "runs": [dict(item) for item in runs],
        "empty_downstream_cohorts": ["structure_pool", "final_portfolio"],
        "summaries": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller-run-id", required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(build_report(uuid.UUID(args.controller_run_id)))
    json_path = args.output_prefix.with_suffix(".json")
    csv_path = args.output_prefix.with_suffix(".csv")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    scalar_keys = sorted(
        {key for item in report["summaries"] for key in item if key != "categories"}
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_keys + ["categories_json"])
        writer.writeheader()
        for item in report["summaries"]:
            writer.writerow(
                {
                    **{key: item.get(key) for key in scalar_keys},
                    "categories_json": json.dumps(
                        item.get("categories"), ensure_ascii=False, sort_keys=True
                    )
                    if "categories" in item
                    else "",
                }
            )
    print(json.dumps({"json": str(json_path), "csv": str(csv_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
