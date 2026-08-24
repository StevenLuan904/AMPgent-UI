from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import text

from pepagent.db.session import SessionFactory
from pepagent.sequence_family import cluster_sequence_families

SEQUENCE_METRICS = (
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
)
TARGET_METRICS = ("conditional_nll", "conditional_ppl")
LABEL_METRICS = {"toxinpred3_label", "macrel_hemolysis_label"}
METRIC_DIRECTIONS = {
    "amp_read_log10_mic_um": "min",
    "llamp_log10_mic_um": "min",
    "macrel_amp_probability": "max",
    "toxinpred3_label": "Non-Toxin",
    "toxinpred3_hybrid_score": "min",
    "macrel_hemolysis_label": "low",
    "macrel_hemolysis_probability": "min",
    "net_charge_ph7_4": "descriptive",
    "hydrophobic_ratio_modlamp": "descriptive",
    "hydrophobic_moment_eisenberg": "max",
    "maximum_hydrophobic_run": "min",
    "guruprasad_instability_index": "min_non_gating",
    "conditional_nll": "min",
    "conditional_ppl": "min",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _load_delivery(
    controller_run_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    async with SessionFactory() as session:
        decisions = (
            (
                await session.execute(
                    text(
                        """select decision_type, structured_json, response_sha256
                        from agent_decisions
                        where run_id=:controller
                          and decision_type like 'seven_branch_delivery:%'
                          and status='succeeded'
                        order by created_at, decision_type"""
                    ),
                    {"controller": controller_run_id},
                )
            )
            .mappings()
            .all()
        )
        if len(decisions) != 7:
            raise ValueError(f"expected 7 delivery decisions, found {len(decisions)}")

        selected: list[dict[str, Any]] = []
        branch_evidence: dict[str, Any] = {}
        for record in decisions:
            payload = dict(record["structured_json"])
            branch = dict(payload["branch"])
            selection = dict(payload["selection"])
            progress = dict(payload["progress"])
            branch_key = str(branch["branch_key"])
            requested = int(branch["requested_delivery_count"])
            candidate_ids = [str(item) for item in selection["selected_candidate_ids"]]
            if not selection.get("quota_complete") or len(candidate_ids) != requested:
                raise ValueError(f"branch {branch_key} delivery quota is not complete")
            for rank, candidate_id in enumerate(candidate_ids, start=1):
                selected.append(
                    {
                        "branch_key": branch_key,
                        "delivery_rank": rank,
                        "candidate_id": candidate_id,
                    }
                )
            branch_evidence[branch_key] = {
                "branch": branch,
                "progress": progress,
                "selection_sha256": selection.get("selection_sha256"),
                "decision_response_sha256": record["response_sha256"],
                "source_run_ids": payload["source_run_ids"],
                "admission_sha256": payload["admission_sha256"],
                "top_up_plan": payload["top_up_plan"],
            }

        candidate_ids = [item["candidate_id"] for item in selected]
        candidates = (
            (
                await session.execute(
                    text(
                        """select c.id::text candidate_id, c.run_id::text source_run_id,
                            c.sequence, c.sequence_sha256, c.generation, c.proposal_rank,
                            r.spec_json->>'branch_key' source_branch_key,
                            r.spec_json->>'round_ordinal' source_round_ordinal,
                            g.tool_name generator_tool, g.tool_version generator_version,
                            g.random_seed generator_seed,
                            coalesce(string_agg(distinct o.opaque_arm_label, ';'
                                order by o.opaque_arm_label), '') generator_cells
                        from candidates c
                        join experiment_runs r on r.id=c.run_id
                        left join tool_calls g on g.id=c.generator_call_id
                        left join candidate_occurrences o on o.candidate_id=c.id
                        where c.id::text=any(:candidate_ids)
                        group by c.id, c.run_id, c.sequence, c.sequence_sha256,
                            c.generation, c.proposal_rank, r.spec_json,
                            g.tool_name, g.tool_version, g.random_seed"""
                    ),
                    {"candidate_ids": candidate_ids},
                )
            )
            .mappings()
            .all()
        )
        if len(candidates) != len(candidate_ids):
            raise ValueError("selected candidate lookup is incomplete")

        evaluations = (
            (
                await session.execute(
                    text(
                        """select e.candidate_id::text candidate_id, e.metric_name,
                            e.numeric_value, e.text_value, e.unit, e.status,
                            e.out_of_domain, e.limitations_json, e.raw_json,
                            t.tool_name, t.tool_version, t.model_uri, t.weights_sha256
                        from evaluations e
                        join tool_calls t on t.id=e.tool_call_id
                        where e.candidate_id::text=any(:candidate_ids)
                          and e.metric_name=any(:metric_names)
                        order by e.candidate_id, e.metric_name, e.created_at"""
                    ),
                    {
                        "candidate_ids": candidate_ids,
                        "metric_names": [*SEQUENCE_METRICS, *TARGET_METRICS],
                    },
                )
            )
            .mappings()
            .all()
        )
    candidate_by_id = {str(item["candidate_id"]): dict(item) for item in candidates}
    eval_by_candidate: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in evaluations:
        candidate_id = str(item["candidate_id"])
        metric_name = str(item["metric_name"])
        if metric_name in eval_by_candidate[candidate_id]:
            raise ValueError(f"duplicate selected evidence for {candidate_id}/{metric_name}")
        eval_by_candidate[candidate_id][metric_name] = dict(item)

    rows: list[dict[str, Any]] = []
    for item in selected:
        candidate = candidate_by_id[item["candidate_id"]]
        branch_key = item["branch_key"]
        evidence = eval_by_candidate[item["candidate_id"]]
        missing = [metric for metric in SEQUENCE_METRICS if metric not in evidence]
        if missing:
            raise ValueError(f"selected candidate {item['candidate_id']} lacks {missing}")
        target_specific = branch_evidence[branch_key]["branch"]["branch_kind"] == "target_specific"
        missing_target = [metric for metric in TARGET_METRICS if metric not in evidence]
        if target_specific and missing_target:
            raise ValueError(f"target candidate {item['candidate_id']} lacks {missing_target}")
        row: dict[str, Any] = {
            **item,
            **candidate,
            "peptide_length": len(str(candidate["sequence"])),
        }
        for metric in (*SEQUENCE_METRICS, *TARGET_METRICS):
            metric_row = evidence.get(metric)
            row[metric] = (
                metric_row["numeric_value"]
                if metric_row and metric_row["numeric_value"] is not None
                else metric_row["text_value"]
                if metric_row
                else None
            )
            row[f"{metric}__unit"] = metric_row["unit"] if metric_row else None
            row[f"{metric}__ood"] = bool(metric_row["out_of_domain"]) if metric_row else None
            row[f"{metric}__tool"] = metric_row["tool_name"] if metric_row else None
            row[f"{metric}__tool_version"] = metric_row["tool_version"] if metric_row else None
            if metric in TARGET_METRICS:
                row[f"{metric}__target_sequence_sha256"] = (
                    metric_row["raw_json"].get("target", {}).get("sequence_sha256")
                    if metric_row
                    else None
                )
        rows.append(row)
    return rows, branch_evidence


def _annotate_targets_and_families(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    targets = {str(item["target_key"]): item for item in manifest["targets"]}
    by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_branch[str(row["branch_key"])].append(row)
    for branch_key, branch_rows in by_branch.items():
        assignments = {
            item.sequence: item
            for item in cluster_sequence_families(row["sequence"] for row in branch_rows)
        }
        target = targets.get(branch_key)
        for row in branch_rows:
            family = assignments[row["sequence"]]
            row["family_key_80_80"] = family.family_key
            row["family_size_80_80"] = family.family_size
            row["target_key"] = target["target_key"] if target else None
            row["target_display_name"] = target["display_name"] if target else "target-agnostic AMP"
            row["target_organism"] = target["organism"] if target else None
            row["target_accession"] = target["protein_accession"] if target else None
            row["target_sequence_sha256"] = target["sequence_sha256"] if target else None
            row["target_source_uri"] = target["source_uri"] if target else None
            for metric in TARGET_METRICS:
                evidence_sha = row[f"{metric}__target_sequence_sha256"]
                if target and evidence_sha != target["sequence_sha256"]:
                    raise ValueError(
                        f"{branch_key}/{row['candidate_id']} target evidence SHA drifted"
                    )
                if not target and evidence_sha is not None:
                    raise ValueError("target-agnostic delivery unexpectedly has target evidence")


def _metric_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cohorts = {"all_delivery": rows}
    cohorts.update(
        {
            branch_key: [row for row in rows if row["branch_key"] == branch_key]
            for branch_key in sorted({str(row["branch_key"]) for row in rows})
        }
    )
    summaries: list[dict[str, Any]] = []
    for cohort, cohort_rows in cohorts.items():
        for metric in (*SEQUENCE_METRICS, *TARGET_METRICS):
            valid = [row for row in cohort_rows if row[metric] not in (None, "")]
            item: dict[str, Any] = {
                "cohort": cohort,
                "cohort_n": len(cohort_rows),
                "metric": metric,
                "valid_n": len(valid),
                "missing_n": len(cohort_rows) - len(valid),
                "ood_n": sum(bool(row[f"{metric}__ood"]) for row in valid),
                "unit": next(
                    (row[f"{metric}__unit"] for row in valid if row[f"{metric}__unit"]),
                    None,
                ),
                "favorable_direction": METRIC_DIRECTIONS[metric],
            }
            if metric in LABEL_METRICS:
                counts = Counter(str(row[metric]) for row in valid)
                item["categories"] = {
                    label: {
                        "count": count,
                        "percentage": 100.0 * count / len(valid) if valid else None,
                    }
                    for label, count in sorted(counts.items())
                }
            elif valid:
                values = np.asarray([float(row[metric]) for row in valid], dtype=float)
                minimum = min(valid, key=lambda row: (float(row[metric]), row["candidate_id"]))
                maximum = max(valid, key=lambda row: (float(row[metric]), row["candidate_id"]))
                direction = METRIC_DIRECTIONS[metric]
                best = (
                    minimum
                    if direction.startswith("min")
                    else maximum
                    if direction == "max"
                    else None
                )
                worst = (
                    maximum
                    if direction.startswith("min")
                    else minimum
                    if direction == "max"
                    else None
                )
                item.update(
                    {
                        "min": float(np.min(values)),
                        "max": float(np.max(values)),
                        "mean": float(np.mean(values)),
                        "median": float(np.median(values)),
                        "standard_deviation": float(np.std(values)),
                        "p10": float(np.quantile(values, 0.10)),
                        "p25": float(np.quantile(values, 0.25)),
                        "p75": float(np.quantile(values, 0.75)),
                        "p90": float(np.quantile(values, 0.90)),
                        "best_candidate_id": best["candidate_id"] if best else None,
                        "best_sequence": best["sequence"] if best else None,
                        "best_value": float(best[metric]) if best else None,
                        "worst_candidate_id": worst["candidate_id"] if worst else None,
                        "worst_sequence": worst["sequence"] if worst else None,
                        "worst_value": float(worst[metric]) if worst else None,
                    }
                )
            summaries.append(item)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller-run-id", required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    controller_run_id = uuid.UUID(args.controller_run_id)
    manifest = json.loads(args.target_manifest.read_text(encoding="utf-8"))
    rows, branch_evidence = asyncio.run(_load_delivery(controller_run_id))
    _annotate_targets_and_families(rows, manifest)
    rows.sort(key=lambda item: (item["branch_key"], item["delivery_rank"]))

    branch_counts = Counter(str(item["branch_key"]) for item in rows)
    if (
        len(rows) != 1900
        or branch_counts.get("target_agnostic_amp") != 1000
        or any(
            branch_counts.get(branch) != 150
            for branch in ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
        )
    ):
        raise ValueError(f"final delivery quota drifted: {dict(branch_counts)}")
    duplicate_counts = Counter(str(item["sequence_sha256"]) for item in rows)
    cross_branch_duplicates = {key: count for key, count in duplicate_counts.items() if count > 1}

    csv_path = args.output_prefix.with_suffix(".csv")
    json_path = args.output_prefix.with_suffix(".json")
    summary_path = args.output_prefix.with_name(
        args.output_prefix.name + "_metric_summary"
    ).with_suffix(".csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    metric_summaries = _metric_summaries(rows)
    payload = {
        "schema_version": "ampgent.seven-branch-final-delivery.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "controller_run_id": str(controller_run_id),
        "row_count": len(rows),
        "branch_counts": dict(sorted(branch_counts.items())),
        "unique_sequence_count_global": len(duplicate_counts),
        "cross_branch_duplicate_sequence_count": len(cross_branch_duplicates),
        "cross_branch_duplicate_sequences": cross_branch_duplicates,
        "sequence_metric_contract": list(SEQUENCE_METRICS),
        "target_metric_contract": list(TARGET_METRICS),
        "scope_note": (
            "Computational predictions and sequence descriptors; no wet-lab measurements."
        ),
        "branch_evidence": branch_evidence,
        "metric_summaries": metric_summaries,
        "rows": rows,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary_fields = sorted(
        {key for item in metric_summaries for key in item if key != "categories"}
    )
    with summary_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*summary_fields, "categories_json"])
        writer.writeheader()
        for item in metric_summaries:
            writer.writerow(
                {
                    **{key: item.get(key) for key in summary_fields},
                    "categories_json": json.dumps(
                        item.get("categories"), ensure_ascii=False, sort_keys=True
                    )
                    if "categories" in item
                    else "",
                }
            )
    print(
        json.dumps(
            {
                "csv": str(csv_path),
                "csv_sha256": _sha256(csv_path),
                "json": str(json_path),
                "json_sha256": _sha256(json_path),
                "metric_summary_csv": str(summary_path),
                "metric_summary_csv_sha256": _sha256(summary_path),
                "rows": len(rows),
                "branch_counts": dict(sorted(branch_counts.items())),
                "unique_sequence_count_global": len(duplicate_counts),
                "cross_branch_duplicate_sequence_count": len(cross_branch_duplicates),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
