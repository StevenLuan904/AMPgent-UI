from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seven_branch_live_distribution import METRICS
from sqlalchemy import text

from pepagent.db.session import SessionFactory
from pepagent.sequence_family import cluster_sequence_families


def _decision_cohorts(decisions: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    cohorts: dict[str, dict[str, str]] = {}
    for row in decisions:
        admission = json.loads(row["response_text"])["admission"]
        run_cohorts = cohorts.setdefault(str(row["run_id"]), {})
        for candidate_id in admission["mature_core_candidate_ids"]:
            run_cohorts[str(candidate_id)] = "mature_core"
        for candidate_id in admission["exploration_candidate_ids"]:
            run_cohorts[str(candidate_id)] = "selected_exploration"
    return cohorts


def _target_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    targets = {str(item["target_key"]): dict(item) for item in manifest["targets"]}
    targets["target_agnostic_amp"] = {
        "display_name": "Target-agnostic antimicrobial peptide",
        "organism": None,
        "protein_accession": None,
        "sequence_sha256": None,
    }
    return targets


def assemble_rows(
    *,
    candidates: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    target_manifest: dict[str, Any],
    cohort: str,
) -> list[dict[str, Any]]:
    cohort_by_run = _decision_cohorts(decisions)
    targets = _target_index(target_manifest)
    evaluation_index = {
        (str(item["candidate_id"]), str(item["metric_name"])): item
        for item in evaluations
    }
    output: list[dict[str, Any]] = []
    for item in candidates:
        run_id = str(item["run_id"])
        candidate_id = str(item["candidate_id"])
        selection = cohort_by_run.get(run_id, {}).get(candidate_id, "unselected")
        if cohort == "qualified" and selection == "unselected":
            continue
        branch_key = str(item["branch_key"])
        target = targets.get(branch_key, {})
        sequence = str(item["sequence"])
        row: dict[str, Any] = {
            "run_id": run_id,
            "branch_key": branch_key,
            "target_display_name": target.get("display_name"),
            "target_organism": target.get("organism"),
            "target_accession": target.get("protein_accession"),
            "target_sequence_sha256": target.get("sequence_sha256"),
            "candidate_id": candidate_id,
            "sequence": sequence,
            "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
            "sequence_length": len(sequence),
            "selection_cohort": selection,
            "generator_tool": item["generator_tool"],
            "generator_version": item["generator_version"],
            "generator_seed": item["generator_seed"],
            "evidence_scope": "computational_prediction_or_descriptor",
        }
        for metric in METRICS:
            evidence = evaluation_index.get((candidate_id, metric))
            row[metric] = (
                evidence["text_value"]
                if evidence and evidence["text_value"] is not None
                else evidence["numeric_value"]
                if evidence
                else None
            )
            row[f"{metric}__status"] = evidence["status"] if evidence else "missing"
            row[f"{metric}__ood"] = bool(evidence["out_of_domain"]) if evidence else None
        output.append(row)
    return sorted(
        output,
        key=lambda row: (
            row["branch_key"],
            0 if row["selection_cohort"] == "mature_core" else 1,
            row["candidate_id"],
        ),
    )


def annotate_family_novelty(
    rows: list[dict[str, Any]], *, historical_sequences: list[str]
) -> dict[str, Any]:
    current_sequences = [str(row["sequence"]) for row in rows]
    current_assignments = {
        item.sequence: item for item in cluster_sequence_families(current_sequences)
    }
    historical_set = set(historical_sequences)
    combined_assignments = {
        item.sequence: item
        for item in cluster_sequence_families(historical_sequences + current_sequences)
    }
    historical_family_keys = {
        combined_assignments[sequence].family_key for sequence in historical_set
    }
    for row in rows:
        sequence = str(row["sequence"])
        current = current_assignments[sequence]
        combined = combined_assignments[sequence]
        row["sequence_family_key"] = current.family_key
        row["sequence_family_size"] = current.family_size
        row["combined_historical_family_key"] = combined.family_key
        row["historical_exact_duplicate"] = sequence in historical_set
        row["historical_family_overlap_80_80"] = (
            combined.family_key in historical_family_keys
        )
    current_family_keys = {
        item.family_key for item in current_assignments.values()
    }
    combined_current_family_keys = {
        combined_assignments[sequence].family_key for sequence in current_sequences
    }
    return {
        "family_method": "ungapped_identity_0.8_coverage_0.8_connected_components",
        "historical_sequence_count": len(historical_set),
        "current_row_count": len(rows),
        "current_unique_sequence_count": len(set(current_sequences)),
        "current_family_count": len(current_family_keys),
        "current_singleton_family_count": len(
            {
                item.family_key
                for item in current_assignments.values()
                if item.family_size == 1
            }
        ),
        "historical_exact_duplicate_count": sum(
            sequence in historical_set for sequence in current_sequences
        ),
        "current_family_overlapping_historical_count": len(
            combined_current_family_keys & historical_family_keys
        ),
        "current_new_family_count": len(
            combined_current_family_keys - historical_family_keys
        ),
        "current_sequences_in_historical_families": sum(
            combined_assignments[sequence].family_key in historical_family_keys
            for sequence in current_sequences
        ),
    }


WINNER_COMPARISON_METRICS = {
    "amp_read_log10_mic_um": "min",
    "llamp_log10_mic_um": "min",
    "macrel_amp_probability": "max",
    "toxinpred3_hybrid_score": "min",
    "macrel_hemolysis_probability": "min",
    "hydrophobic_moment_eisenberg": "max",
    "maximum_hydrophobic_run": "min",
}


def compare_historical_winner(
    rows: list[dict[str, Any]],
    *,
    historical_rows: list[dict[str, Any]],
    winner_sequence: str | None,
) -> dict[str, Any] | None:
    if winner_sequence is None:
        return None
    winner = next(
        (item for item in historical_rows if item.get("sequence") == winner_sequence),
        None,
    )
    if winner is None:
        raise ValueError("historical winner sequence is absent from historical candidates")
    comparisons: list[dict[str, Any]] = []
    for metric, direction in WINNER_COMPARISON_METRICS.items():
        winner_value = float(winner[metric])
        values = [
            float(row[metric]) for row in rows if row.get(metric) not in (None, "")
        ]
        if direction == "min":
            better = sum(value < winner_value for value in values)
        else:
            better = sum(value > winner_value for value in values)
        equal = sum(value == winner_value for value in values)
        comparisons.append(
            {
                "metric": metric,
                "favorable_direction": direction,
                "winner_value": winner_value,
                "current_valid_n": len(values),
                "current_better_count": better,
                "current_equal_count": equal,
                "current_worse_count": len(values) - better - equal,
                "winner_rank_among_current_plus_winner": better + 1,
                "winner_rank_fraction": (better + 1) / (len(values) + 1),
            }
        )
    return {
        "schema_version": "ampgent.historical-winner-current-cohort-comparison.1",
        "diagnostic_only": True,
        "winner_sequence": winner_sequence,
        "winner_candidate_id": winner.get("candidate_id"),
        "current_cohort_n": len(rows),
        "comparisons": comparisons,
    }


async def load_rows(controller_run_id: uuid.UUID) -> tuple[list[dict[str, Any]], ...]:
    async with SessionFactory() as session:
        candidates = (
            (
                await session.execute(
                    text(
                        """select r.id::text run_id, r.spec_json->>'branch_key' branch_key,
                        c.id::text candidate_id, c.sequence,
                        t.tool_name generator_tool, t.tool_version generator_version,
                        t.random_seed generator_seed
                        from experiment_runs r
                        join candidates c on c.run_id=r.id
                        join tool_calls t on t.id=c.generator_call_id
                        where r.parent_run_id=:controller
                        order by r.created_at, c.id"""
                    ),
                    {"controller": controller_run_id},
                )
            )
            .mappings()
            .all()
        )
        evaluations = (
            (
                await session.execute(
                    text(
                        """select distinct on (e.candidate_id, e.metric_name)
                        e.candidate_id::text candidate_id, e.metric_name,
                        e.numeric_value, e.text_value, e.status, e.out_of_domain
                        from experiment_runs r
                        join candidates c on c.run_id=r.id
                        join evaluations e on e.candidate_id=c.id
                        where r.parent_run_id=:controller
                        and e.metric_name=any(:metrics)
                        order by e.candidate_id, e.metric_name, e.created_at desc, e.id desc"""
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
                        from agent_decisions d
                        join experiment_runs r on r.id=d.run_id
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
    return (
        [dict(item) for item in candidates],
        [dict(item) for item in evaluations],
        [dict(item) for item in decisions],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller-run-id", required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--cohort", choices=("qualified", "all"), default="qualified")
    parser.add_argument("--historical-candidates", type=Path)
    parser.add_argument("--historical-winner-sequence")
    args = parser.parse_args()
    candidates, evaluations, decisions = asyncio.run(
        load_rows(uuid.UUID(args.controller_run_id))
    )
    target_manifest = json.loads(args.target_manifest.read_text(encoding="utf-8"))
    rows = assemble_rows(
        candidates=candidates,
        evaluations=evaluations,
        decisions=decisions,
        target_manifest=target_manifest,
        cohort=args.cohort,
    )
    historical_rows: list[dict[str, Any]] = []
    if args.historical_candidates:
        with args.historical_candidates.open(newline="", encoding="utf-8-sig") as handle:
            historical_rows = [dict(item) for item in csv.DictReader(handle)]
    historical_sequences = [str(item["sequence"]) for item in historical_rows]
    family_summary = annotate_family_novelty(
        rows, historical_sequences=historical_sequences
    )
    winner_comparison = compare_historical_winner(
        rows,
        historical_rows=historical_rows,
        winner_sequence=args.historical_winner_sequence,
    )
    csv_path = args.output_prefix.with_suffix(".csv")
    json_path = args.output_prefix.with_suffix(".json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(rows)
    payload = {
        "schema_version": "ampgent.seven-branch-candidate-handoff.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "controller_run_id": args.controller_run_id,
        "cohort": args.cohort,
        "candidate_count": len(rows),
        "metric_contract": METRICS,
        "family_summary": family_summary,
        "historical_winner_comparison": winner_comparison,
        "scope_note": "computational predictions/descriptors; no wet-lab measurements",
        "rows": rows,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"csv": str(csv_path), "json": str(json_path), "rows": len(rows)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
