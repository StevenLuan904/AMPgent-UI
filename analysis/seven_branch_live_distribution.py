from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import statistics
import uuid
from collections import defaultdict
from datetime import UTC, datetime
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


def _numeric_summary(rows: list[dict[str, Any]], *, direction: str) -> dict[str, Any]:
    values = [float(row["numeric_value"]) for row in rows]
    if not values:
        return {}
    minimum = min(rows, key=lambda row: (float(row["numeric_value"]), row["candidate_id"]))
    maximum = max(rows, key=lambda row: (float(row["numeric_value"]), row["candidate_id"]))
    if direction.startswith("min"):
        best = minimum
        worst = maximum
    elif direction == "max":
        best = maximum
        worst = minimum
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
        "min_candidate_id": minimum["candidate_id"],
        "min_sequence": minimum["sequence"],
        "max_candidate_id": maximum["candidate_id"],
        "max_sequence": maximum["sequence"],
        "best_candidate_id": best["candidate_id"] if best else None,
        "best_sequence": best["sequence"] if best else None,
        "best_value": float(best["numeric_value"]) if best else None,
        "worst_candidate_id": worst["candidate_id"] if worst else None,
        "worst_sequence": worst["sequence"] if worst else None,
        "worst_value": float(worst["numeric_value"]) if worst else None,
    }


def _metric_summary_item(
    *,
    branch_key: str,
    run_status: str,
    cohort: str,
    candidate_ids: set[str],
    metric: str,
    contract: dict[str, str],
    metric_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    succeeded = [row for row in metric_rows if row["status"] == "succeeded"]
    valid = [
        row
        for row in succeeded
        if (
            row["text_value"] is not None
            if metric in LABEL_METRICS
            else row["numeric_value"] is not None
        )
    ]
    item: dict[str, Any] = {
        "branch_key": branch_key,
        "run_status": run_status,
        "cohort": cohort,
        "cohort_n": len(candidate_ids),
        "metric": metric,
        "evidence_kind": contract["kind"],
        "favorable_direction": contract["direction"],
        "valid_n": len(valid),
        "missing_n": max(0, len(candidate_ids) - len(valid)),
        "failed_n": sum(row["status"] != "succeeded" for row in metric_rows),
        "ood_n": sum(bool(row["out_of_domain"]) for row in valid),
        "unit": next((row["unit"] for row in valid if row["unit"]), None),
        "tool_names": sorted({str(row["tool_name"]) for row in valid}),
        "tool_versions": sorted({str(row["tool_version"]) for row in valid}),
        "model_uris": sorted(
            {str(row["model_uri"]) for row in valid if row["model_uri"]}
        ),
        "weights_sha256": sorted(
            {
                str(row["weights_sha256"])
                for row in valid
                if row["weights_sha256"]
            }
        ),
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
        item.update(_numeric_summary(valid, direction=contract["direction"]))
    return item


async def build_report(controller_run_id: uuid.UUID) -> dict[str, Any]:
    async with SessionFactory() as session:
        runs = (
            (
                await session.execute(
                    text(
                        """select id::text, spec_json->>'branch_key' branch_key, status,
                    (select count(*) from candidate_occurrences o where o.run_id=r.id)
                        raw_occurrence_count,
                    (select count(*) from candidates c where c.run_id=r.id) candidate_count,
                    (select count(*) from evaluations e join candidates c on c.id=e.candidate_id
                        where c.run_id=r.id) evaluation_count,
                    (select count(*) from tool_calls t where t.run_id=r.id) tool_call_count,
                    (select count(*) from agent_decisions d where d.run_id=r.id) decision_count
                    from experiment_runs r where parent_run_id=:controller
                    order by created_at, id"""
                    ),
                    {"controller": controller_run_id},
                )
            )
            .mappings()
            .all()
        )
        rows = (
            (
                await session.execute(
                    text(
                        """select r.id::text run_id, r.spec_json->>'branch_key' branch_key,
                    c.id::text candidate_id, c.sequence, e.metric_name,
                    e.numeric_value, e.text_value, e.unit, e.status, e.out_of_domain,
                    t.tool_name, t.tool_version, t.model_uri, t.weights_sha256
                    from experiment_runs r
                    join candidates c on c.run_id=r.id
                    join evaluations e on e.candidate_id=c.id
                    join tool_calls t on t.id=e.tool_call_id
                    where r.parent_run_id=:controller
                    and e.metric_name=any(:metrics)
                    order by r.created_at, c.id, e.metric_name"""
                    ),
                    {"controller": controller_run_id, "metrics": list(METRICS)},
                )
            )
            .mappings()
            .all()
        )
        decisions = (
            (
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
            )
            .mappings()
            .all()
        )
        generation_cells = (
            (
                await session.execute(
                    text(
                        """select r.spec_json->>'branch_key' branch_key,
                    o.opaque_arm_label, t.tool_name, t.tool_version, t.random_seed,
                    count(*) raw_occurrence_count,
                    count(o.candidate_id) assigned_occurrence_count,
                    count(distinct o.candidate_id) distinct_candidate_count,
                    count(distinct o.sequence_sha256) distinct_sequence_count
                    from experiment_runs r
                    join candidate_occurrences o on o.run_id=r.id
                    join tool_calls t on t.id=o.tool_call_id
                    where r.parent_run_id=:controller
                    group by r.spec_json->>'branch_key', o.opaque_arm_label,
                        t.tool_name, t.tool_version, t.random_seed
                    order by r.spec_json->>'branch_key', o.opaque_arm_label,
                        t.tool_name, t.random_seed"""
                    ),
                    {"controller": controller_run_id},
                )
            )
            .mappings()
            .all()
        )
        generation_arms = (
            (
                await session.execute(
                    text(
                        """select r.spec_json->>'branch_key' branch_key,
                    t.tool_name, t.tool_version,
                    count(*) raw_occurrence_count,
                    count(o.candidate_id) assigned_occurrence_count,
                    count(distinct o.candidate_id) distinct_candidate_count,
                    count(distinct o.sequence_sha256) distinct_sequence_count
                    from experiment_runs r
                    join candidate_occurrences o on o.run_id=r.id
                    join tool_calls t on t.id=o.tool_call_id
                    where r.parent_run_id=:controller
                    group by r.spec_json->>'branch_key', t.tool_name, t.tool_version
                    order by r.spec_json->>'branch_key', t.tool_name"""
                    ),
                    {"controller": controller_run_id},
                )
            )
            .mappings()
            .all()
        )
        generation_arm_overlaps = (
            (
                await session.execute(
                    text(
                        """with arm_sequences as (
                    select distinct r.spec_json->>'branch_key' branch_key,
                        t.tool_name, o.sequence_sha256
                    from experiment_runs r
                    join candidate_occurrences o on o.run_id=r.id
                    join tool_calls t on t.id=o.tool_call_id
                    where r.parent_run_id=:controller
                    )
                    select a.branch_key, a.tool_name left_tool_name,
                        b.tool_name right_tool_name, count(*) overlap_sequence_count
                    from arm_sequences a
                    join arm_sequences b on b.branch_key=a.branch_key
                        and b.sequence_sha256=a.sequence_sha256
                        and b.tool_name>a.tool_name
                    group by a.branch_key, a.tool_name, b.tool_name
                    order by a.branch_key, a.tool_name, b.tool_name"""
                    ),
                    {"controller": controller_run_id},
                )
            )
            .mappings()
            .all()
        )
        candidate_sources = (
            (
                await session.execute(
                    text(
                        """select r.spec_json->>'branch_key' branch_key,
                    c.id::text candidate_id, t.tool_name, t.tool_version
                    from experiment_runs r
                    join candidates c on c.run_id=r.id
                    join tool_calls t on t.id=c.generator_call_id
                    where r.parent_run_id=:controller
                    order by r.spec_json->>'branch_key', t.tool_name, c.id"""
                    ),
                    {"controller": controller_run_id},
                )
            )
            .mappings()
            .all()
        )

    cohort_ids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
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
            cohort_ids[branch]["mature_core"] | cohort_ids[branch]["selected_exploration"]
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
                summaries.append(
                    _metric_summary_item(
                        branch_key=branch,
                        run_status=run["status"],
                        cohort=cohort,
                        candidate_ids=ids,
                        metric=metric,
                        contract=contract,
                        metric_rows=metric_rows,
                    )
                )

    # Give reviews an exact controller-wide distribution over all target-specific
    # branches. The target-agnostic pool stays separate because conditional target
    # scores are intentionally not applicable there.
    target_branches = {
        run["branch_key"]
        for run in runs
        if run["branch_key"] != "target_agnostic_amp"
    }
    for cohort in ("all", "mature_core", "selected_exploration", "qualified"):
        aggregate_ids = set().union(
            *(cohort_ids[branch].get(cohort, set()) for branch in target_branches)
        )
        for metric, contract in METRICS.items():
            metric_rows = [
                dict(row)
                for row in rows
                if row["branch_key"] in target_branches
                and row["metric_name"] == metric
                and row["candidate_id"] in aggregate_ids
            ]
            summaries.append(
                _metric_summary_item(
                    branch_key="all_target_branches",
                    run_status="mixed",
                    cohort=cohort,
                    candidate_ids=aggregate_ids,
                    metric=metric,
                    contract=contract,
                    metric_rows=metric_rows,
                )
            )
    source_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for item in candidate_sources:
        source_groups[
            (item["branch_key"], item["tool_name"], item["tool_version"])
        ].append(item["candidate_id"])
    raw_by_source = {
        (item["branch_key"], item["tool_name"], item["tool_version"]): int(
            item["raw_occurrence_count"]
        )
        for item in generation_arms
    }
    generator_cohort_yields: list[dict[str, Any]] = []
    for (branch, tool_name, tool_version), candidate_ids in sorted(source_groups.items()):
        source_ids = set(candidate_ids)
        raw_occurrence_n = raw_by_source[(branch, tool_name, tool_version)]
        for cohort in ("all", "mature_core", "selected_exploration", "qualified"):
            if cohort != "all" and cohort not in cohort_ids[branch]:
                continue
            selected_n = len(source_ids & cohort_ids[branch].get(cohort, set()))
            generator_cohort_yields.append(
                {
                    "branch_key": branch,
                    "tool_name": tool_name,
                    "tool_version": tool_version,
                    "cohort": cohort,
                    "raw_occurrence_n": raw_occurrence_n,
                    "generator_candidate_n": len(source_ids),
                    "cohort_candidate_n": selected_n,
                    "valid_candidate_per_raw_occurrence": (
                        len(source_ids) / raw_occurrence_n
                        if raw_occurrence_n
                        else None
                    ),
                    "cohort_candidate_per_raw_occurrence": (
                        selected_n / raw_occurrence_n if raw_occurrence_n else None
                    ),
                    "cohort_yield": selected_n / len(source_ids) if source_ids else None,
                }
            )
    pooled_generator_efficiency: list[dict[str, Any]] = []
    qualified_by_generator: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for item in generator_cohort_yields:
        if item["cohort"] == "qualified":
            qualified_by_generator[(item["tool_name"], item["tool_version"])].append(
                item
            )
    for (tool_name, tool_version), items in sorted(qualified_by_generator.items()):
        raw_n = sum(int(item["raw_occurrence_n"]) for item in items)
        valid_n = sum(int(item["generator_candidate_n"]) for item in items)
        qualified_n = sum(int(item["cohort_candidate_n"]) for item in items)
        pooled_generator_efficiency.append(
            {
                "tool_name": tool_name,
                "tool_version": tool_version,
                "completed_branch_n": len(items),
                "raw_occurrence_n": raw_n,
                "valid_candidate_n": valid_n,
                "qualified_candidate_n": qualified_n,
                "valid_candidate_per_raw_occurrence": (
                    valid_n / raw_n if raw_n else None
                ),
                "qualified_candidate_per_raw_occurrence": (
                    qualified_n / raw_n if raw_n else None
                ),
                "expected_qualified_per_1000_raw": (
                    1000.0 * qualified_n / raw_n if raw_n else None
                ),
            }
        )
    return {
        "schema_version": "ampgent.seven-branch-live-score-distribution.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "controller_run_id": str(controller_run_id),
        "scope_note": "computational predictions/descriptors only; no wet-lab measurements",
        "cohort_definitions": {
            "all": "all candidates persisted in the branch child run",
            "mature_core": "candidates selected as mature_core by the frozen admission policy",
            "selected_exploration": (
                "promising candidates selected within the frozen exploration budget"
            ),
            "qualified": "union of mature_core and selected_exploration",
        },
        "runs": [dict(item) for item in runs],
        "generation_cells": [
            {
                **dict(item),
                "within_cell_duplicate_occurrence_count": (
                    item["raw_occurrence_count"] - item["distinct_sequence_count"]
                ),
                "invalid_or_unassigned_occurrence_count": (
                    item["raw_occurrence_count"] - item["assigned_occurrence_count"]
                ),
                "within_cell_unique_sequence_yield": (
                    item["distinct_sequence_count"] / item["raw_occurrence_count"]
                    if item["raw_occurrence_count"]
                    else None
                ),
                "valid_candidate_yield": (
                    item["assigned_occurrence_count"] / item["raw_occurrence_count"]
                    if item["raw_occurrence_count"]
                    else None
                ),
            }
            for item in generation_cells
        ],
        "generation_arms": [
            {
                **dict(item),
                "within_arm_duplicate_occurrence_count": (
                    item["raw_occurrence_count"] - item["distinct_sequence_count"]
                ),
                "invalid_or_unassigned_occurrence_count": (
                    item["raw_occurrence_count"] - item["assigned_occurrence_count"]
                ),
                "within_arm_unique_sequence_yield": (
                    item["distinct_sequence_count"] / item["raw_occurrence_count"]
                    if item["raw_occurrence_count"]
                    else None
                ),
                "valid_candidate_yield": (
                    item["assigned_occurrence_count"] / item["raw_occurrence_count"]
                    if item["raw_occurrence_count"]
                    else None
                ),
            }
            for item in generation_arms
        ],
        "generation_arm_overlaps": [dict(item) for item in generation_arm_overlaps],
        "generator_cohort_yields": generator_cohort_yields,
        "pooled_generator_efficiency": pooled_generator_efficiency,
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
            scalar_values = {}
            for key in scalar_keys:
                value = item.get(key)
                scalar_values[key] = (
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (list, dict))
                    else value
                )
            writer.writerow(
                {
                    **scalar_values,
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
