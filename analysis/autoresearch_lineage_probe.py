from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from pepagent.autoresearch_closed_loop import (
    CandidateEvidence,
    MetricObservation,
    MultiFrontArchivePolicy,
    apply_evolution_action,
    build_multi_front_archive,
    parse_evolution_action,
)
from pepagent.autoresearch_planner import (
    _hydrophobic_fraction,
    _sequence_prescreen,
    build_multifront_rule_action_plan,
)
from pepagent.autoresearch_quality_diversity import (
    build_quality_diversity_archive,
    candidate_from_score_row,
)
from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text
from pepagent.sequence_family import cluster_sequence_families

BRANCHES = ("acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa")
ARCHIVE_METRICS = {
    "amp_read_log10_mic_um": ("minimize", "log10(uM)"),
    "llamp_log10_mic_um": ("minimize", "log10(uM)"),
    "macrel_amp_probability": ("maximize", "probability"),
    "macrel_hemolysis_probability": ("minimize", "probability"),
    "toxinpred3_hybrid_score": ("minimize", "dimensionless"),
    "guruprasad_instability_index": ("minimize", "index"),
}


def _operational_score_sequence_hashes(
    payloads: Iterable[dict[str, Any]],
) -> set[str]:
    sequence_hashes: set[str] = set()
    for payload in payloads:
        if payload.get("purpose") != "score_all" or payload.get("status") != "succeeded":
            continue
        output = payload.get("output")
        if not isinstance(output, dict):
            continue
        candidates = output.get("candidates")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("PostgreSQL operational score history is malformed")
            sequence_sha256 = candidate.get("sequence_sha256")
            if not isinstance(sequence_sha256, str):
                raise ValueError(
                    "PostgreSQL operational score history is missing a sequence SHA-256"
                )
            sequence_hashes.add(sequence_sha256)
    return sequence_hashes


async def _postgresql_sequence_hash_sources() -> tuple[set[str], set[str]]:
    async with SessionFactory() as session:
        candidate_hashes = set(
            await session.scalars(select(Candidate.sequence_sha256))
        )
        operational_hash_rows = list(
            await session.scalars(
                text(
                    """
                    SELECT DISTINCT candidate ->> 'sequence_sha256'
                    FROM lifecycle_events AS event
                    CROSS JOIN LATERAL jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(
                                event.payload_json -> 'output' -> 'candidates'
                            ) = 'array'
                            THEN event.payload_json -> 'output' -> 'candidates'
                            ELSE '[]'::jsonb
                        END
                    ) AS candidate
                    WHERE event.event_type = 'operational.call.succeeded'
                      AND event.payload_json ->> 'purpose' = 'score_all'
                      AND event.payload_json ->> 'status' = 'succeeded'
                    """
                )
            )
        )
    if any(not isinstance(item, str) for item in operational_hash_rows):
        raise ValueError("PostgreSQL operational score history is malformed")
    return candidate_hashes, set(operational_hash_rows)


def _prefer_full_support_within_families(
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    full_support_families = {
        row["family_key_80_80"]
        for row in rows
        if int(row.get("activity_model_support_count_calibrated", 0)) == 3
    }
    return [
        row
        for row in rows
        if row["family_key_80_80"] not in full_support_families
        or int(row.get("activity_model_support_count_calibrated", 0)) == 3
    ]


def _unresolved_family_parent_ids(
    rows: list[dict[str, str]],
) -> dict[str, tuple[str, ...]]:
    full_support_families = {
        row["family_key_80_80"]
        for row in rows
        if int(row.get("activity_model_support_count_calibrated", 0)) == 3
    }
    parent_ids: dict[str, list[str]] = {}
    for row in sorted(
        rows,
        key=lambda item: (item["family_key_80_80"], item["sequence_sha256"]),
    ):
        family_key = row["family_key_80_80"]
        if family_key not in full_support_families:
            parent_ids.setdefault(family_key, []).append(row["sequence_sha256"])
    return {key: tuple(values) for key, values in parent_ids.items()}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _evidence(row: dict[str, str]) -> CandidateEvidence:
    metrics = {
        metric_name: MetricObservation(
            numeric_value=float(row[metric_name]),
            direction=direction,  # type: ignore[arg-type]
            unit=unit,
            version="frozen-score-all-source",
            out_of_domain=(
                metric_name == "guruprasad_instability_index" and len(row["sequence"]) < 20
            ),
        )
        for metric_name, (direction, unit) in ARCHIVE_METRICS.items()
    }
    return CandidateEvidence(
        candidate_id=row["sequence_sha256"],
        sequence=row["sequence"],
        sequence_sha256=row["sequence_sha256"],
        family_key=row["family_key_80_80"],
        metrics=metrics,
        archive_eligible=True,
    )


def run(args: argparse.Namespace) -> None:
    if args.generation < 2:
        raise ValueError("lineage probe generation must be at least 2")
    if args.replicates < 1:
        raise ValueError("lineage probe replicates must be positive")
    rows: list[dict[str, str]] = []
    source_csv_sha256s: list[str] = []
    for path in args.input_csv:
        source_csv_sha256s.append(sha256_file(path))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows.extend(csv.DictReader(stream))
    if not rows:
        raise ValueError("lineage probe input is empty")
    input_sequences = {row["sequence"] for row in rows}
    input_sequence_hashes = {sha256_text(sequence) for sequence in input_sequences}
    if any(row.get("sequence_sha256") != sha256_text(row["sequence"]) for row in rows):
        raise ValueError("lineage probe input has a sequence/hash mismatch")
    active_branches = tuple(args.branch or BRANCHES)
    if len(set(active_branches)) != len(active_branches):
        raise ValueError("lineage probe branch selection contains duplicates")
    if set(active_branches) - set(BRANCHES):
        raise ValueError("lineage probe branch selection is invalid")
    rows = [
        row
        for row in rows
        if row["branch_key"] in active_branches
        and int(row.get("activity_model_support_count_calibrated", 0))
        >= args.minimum_calibrated_support
    ]
    if args.prefer_full_support_within_families:
        rows = _prefer_full_support_within_families(rows)
    if not rows:
        raise ValueError("lineage probe selected source cohort is empty")
    source_branches = {row["branch_key"] for row in rows}
    if source_branches != set(active_branches):
        raise ValueError("lineage probe source branches must exactly match the selected branches")
    sequences = {row["sequence"] for row in rows}
    selected_sequence_hashes = {row["sequence_sha256"] for row in rows}
    if len(sequences) != len(rows) or len(selected_sequence_hashes) != len(rows):
        raise ValueError("lineage probe source is not globally sequence-unique")
    sequence_hashes = set(input_sequence_hashes)
    historical_rows: list[dict[str, str]] = []
    historical_source_hashes: list[str] = []
    for path in args.historical_csv:
        historical_source_hashes.append(sha256_file(path))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            historical_rows.extend(csv.DictReader(stream))
    # Selection may discard weaker rows from a family, but those sequences remain
    # historical observations and must never become "novel" children later.
    historical_sequences = set(input_sequences)
    for row in historical_rows:
        sequence = row["sequence"]
        sequence_sha256 = row.get("sequence_sha256") or sha256_text(sequence)
        if sequence_sha256 != sha256_text(sequence):
            raise ValueError("historical lineage exclusion has a sequence/hash mismatch")
        historical_sequences.add(sequence)
        sequence_hashes.add(sequence_sha256)
    postgresql_candidate_history_count = 0
    postgresql_operational_history_count = 0
    postgresql_history_count = 0
    history_check_status = "deferred_to_postgresql_materialization_gate"
    display_or_promotion_allowed = False
    if args.include_postgresql_history:
        candidate_hashes, operational_hashes = asyncio.run(
            _postgresql_sequence_hash_sources()
        )
        postgresql_hashes = candidate_hashes | operational_hashes
        if any(
            len(item) != 64 or set(item) - set("0123456789abcdef") for item in postgresql_hashes
        ):
            raise ValueError("PostgreSQL history contains an invalid sequence SHA-256")
        postgresql_candidate_history_count = len(candidate_hashes)
        postgresql_operational_history_count = len(operational_hashes)
        postgresql_history_count = len(postgresql_hashes)
        sequence_hashes.update(postgresql_hashes)
        history_check_status = "checked_against_postgresql_candidate_and_operational_history"
        display_or_promotion_allowed = True
    operator_release_sha256 = sha256_file(
        Path(__file__).resolve().parents[1] / "src" / "pepagent" / "autoresearch_planner.py"
    )

    branch_archives: dict[str, dict[str, Any]] = {}
    quality_diversity_archives: dict[str, dict[str, Any]] = {}
    plans: dict[str, dict[str, Any]] = {}
    action_records: list[dict[str, Any]] = []
    child_records: list[dict[str, Any]] = []
    child_sequences: set[str] = set()
    excluded_sequence_hashes = set(sequence_hashes)
    family_reference_sequences = set(historical_sequences)
    for branch_index, branch_key in enumerate(active_branches):
        branch_rows = [row for row in rows if row["branch_key"] == branch_key]
        evidence = [_evidence(row) for row in branch_rows]
        evidence_by_id = {item.candidate_id: item for item in evidence}
        source_row_by_id = {row["sequence_sha256"]: row for row in branch_rows}
        unresolved_family_parents = (
            _unresolved_family_parent_ids(branch_rows)
            if args.force_unresolved_family_coverage
            else {}
        )
        unresolved_family_keys = tuple(sorted(unresolved_family_parents))
        policy = MultiFrontArchivePolicy(
            known_family_keys=tuple(sorted({item.family_key for item in evidence}))
        )
        archive = build_multi_front_archive(
            evidence,
            policy,
            generation=args.generation - 1,
        )
        branch_archives[branch_key] = {
            **archive.model_dump(mode="json"),
            "archive_sha256": archive.archive_sha256,
        }
        quality_diversity = build_quality_diversity_archive(
            [candidate_from_score_row(row) for row in branch_rows],
            (),
        )
        quality_diversity_archives[branch_key] = quality_diversity.model_dump(
            mode="json"
        )
        quality_diversity_parent_ids = tuple(
            elite.candidate_id for elite in quality_diversity.elites
        )
        branch_plans: list[dict[str, Any]] = []
        branch_rank = 0
        for replicate in range(args.replicates):
            required_parent_ids_list: list[str] = []
            if quality_diversity_parent_ids:
                required_parent_ids_list.append(
                    quality_diversity_parent_ids[
                        replicate % len(quality_diversity_parent_ids)
                    ]
                )
            if unresolved_family_keys:
                family_index = replicate % len(unresolved_family_keys)
                family_key = unresolved_family_keys[family_index]
                family_parents = unresolved_family_parents[family_key]
                parent_index = replicate // len(unresolved_family_keys)
                required_parent_ids_list.append(
                    family_parents[parent_index % len(family_parents)]
                )
            required_parent_ids = tuple(dict.fromkeys(required_parent_ids_list))
            plan = build_multifront_rule_action_plan(
                candidates=evidence,
                snapshot=archive,
                branch_key=branch_key,
                generation=args.generation,
                seed=args.seed + branch_index * 10_000 + replicate * 1_000,
                operator_release_sha256=operator_release_sha256,
                target_sequence_sha256=sha256_text(f"unused-cpu-target:{branch_key}"),
                historical_sequence_sha256s=excluded_sequence_hashes,
                historical_family_representatives=family_reference_sequences,
                de_novo_quota=args.de_novo_quota,
                pepmlm_targeted_enabled=False,
                required_parent_candidate_ids=required_parent_ids,
                quality_diversity_elite_candidate_ids=quality_diversity_parent_ids,
            )
            if plan["requires_generator_gpu"]:
                raise ValueError("CPU lineage probe unexpectedly requires a generator GPU")
            branch_plans.append(plan)
            forced_action_sha256s = set(plan["forced_parent_action_sha256s"].values())
            for payload in plan["actions"]:
                branch_rank += 1
                action = parse_evolution_action(payload)
                child = apply_evolution_action(action, evidence_by_id)
                if child in historical_sequences or child in child_sequences:
                    raise ValueError("lineage action produced an exact replay")
                child_sequences.add(child)
                instability, maximum_hydrophobic_run, net_charge = _sequence_prescreen(child)
                child_sha256 = sha256_text(child)
                excluded_sequence_hashes.add(child_sha256)
                family_reference_sequences.add(child)
                action_record = {
                    "branch_key": branch_key,
                    "generation": args.generation,
                    "proposal_rank": branch_rank,
                    "replicate": replicate + 1,
                    "action_type": action.action_type,
                    "action_sha256": action.action_sha256,
                    "operator_id": action.operator_id,
                    "operator_release_sha256": action.operator_release_sha256,
                    "seed": action.seed,
                    "parent_candidate_id": getattr(action, "parent_candidate_id", None),
                    "donor_candidate_id": getattr(action, "donor_candidate_id", None),
                    "child_candidate_id": f"lineage-{child_sha256[:20]}",
                    "sequence": child,
                    "sequence_sha256": child_sha256,
                    "expected_improvement_metrics": list(action.expected_improvement_metrics),
                    "protected_metrics": list(action.protected_metrics),
                    "evidence_sha256s": list(action.evidence_sha256s),
                    "forced_family_coverage": action.action_sha256 in forced_action_sha256s,
                    "source_family_key_80_80": (
                        source_row_by_id[action.parent_candidate_id]["family_key_80_80"]
                        if getattr(action, "parent_candidate_id", None) in source_row_by_id
                        else ""
                    ),
                }
                action_records.append(action_record)
                child_records.append(
                    {
                        "branch_key": branch_key,
                        "generation": args.generation,
                        "proposal_rank": branch_rank,
                        "replicate": replicate + 1,
                        "seed": action.seed,
                        "operator_id": action.operator_id,
                        "action_type": action.action_type,
                        "action_sha256": action.action_sha256,
                        "forced_family_coverage": str(
                            action_record["forced_family_coverage"]
                        ).lower(),
                        "source_family_key_80_80": action_record["source_family_key_80_80"],
                        "parent_candidate_id": action_record["parent_candidate_id"] or "",
                        "donor_candidate_id": action_record["donor_candidate_id"] or "",
                        "candidate_id": action_record["child_candidate_id"],
                        "sequence": child,
                        "sequence_sha256": child_sha256,
                        "guruprasad_instability_index": f"{instability:.6f}",
                        "maximum_hydrophobic_run": maximum_hydrophobic_run,
                        "hydrophobic_fraction": f"{_hydrophobic_fraction(child):.6f}",
                        "net_charge_ph7_4": f"{net_charge:.6f}",
                        "historical_exact_replay": (
                            "false" if display_or_promotion_allowed else "unchecked"
                        ),
                        "history_check_status": history_check_status,
                        "display_or_promotion_allowed": str(
                            display_or_promotion_allowed
                        ).lower(),
                        "score_all_status": "pending",
                    }
                )
        combined_plan = {
            **branch_plans[0],
            "replicate_count": args.replicates,
            "replicate_plan_sha256s": [sha256_json(plan) for plan in branch_plans],
            "strategies": [strategy for plan in branch_plans for strategy in plan["strategies"]],
            "rationale_by_action_sha256": {
                key: value
                for plan in branch_plans
                for key, value in plan["rationale_by_action_sha256"].items()
            },
            "actions": [action for plan in branch_plans for action in plan["actions"]],
            "de_novo_action_count": sum(plan["de_novo_action_count"] for plan in branch_plans),
            "required_de_novo_action_count": sum(
                plan["required_de_novo_action_count"] for plan in branch_plans
            ),
            "forced_family_coverage_enabled": args.force_unresolved_family_coverage,
            "unresolved_family_keys": list(unresolved_family_keys),
            "forced_parent_action_count": sum(
                plan["forced_parent_action_count"] for plan in branch_plans
            ),
            "unmaterialized_forced_parent_candidate_ids": [
                candidate_id
                for plan in branch_plans
                for candidate_id in plan["unmaterialized_forced_parent_candidate_ids"]
            ],
            "quality_diversity_context": {
                "policy_id": quality_diversity.policy.policy_id,
                "covered_cell_count": len(quality_diversity.covered_cell_ids),
                "empty_cell_count": len(quality_diversity.empty_cell_ids),
                "valid_cell_coverage": quality_diversity.valid_cell_coverage,
                "archive_qd_score": quality_diversity.archive_qd_score,
                "elite_parent_count": len(quality_diversity_parent_ids),
                "quality_and_diversity_are_not_weighted": True,
            },
        }
        plans[branch_key] = combined_plan

    assignments = {
        item.sequence: item
        for item in cluster_sequence_families(historical_sequences | child_sequences)
    }
    reference_family_keys = {assignments[sequence].family_key for sequence in historical_sequences}
    for row in child_records:
        assignment = assignments[row["sequence"]]
        row.update(
            {
                "family_key_80_80": assignment.family_key,
                "family_representative_sequence": assignment.representative_sequence,
                "combined_family_size": assignment.family_size,
                "new_family_relative_to_all_references": str(
                    assignment.family_key not in reference_family_keys
                ).lower(),
                "diversity_qualified": str(
                    assignment.family_key not in reference_family_keys
                ).lower(),
            }
        )
    child_records.sort(key=lambda row: (row["branch_key"], row["proposal_rank"]))
    action_records.sort(key=lambda row: (row["branch_key"], row["proposal_rank"]))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_cohort_path = args.output_dir / "source_cohort.csv"
    _write_csv(source_cohort_path, rows)
    _write_csv(args.output_dir / "children.csv", child_records)
    actions_path = args.output_dir / "actions.json"
    actions_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-lineage-actions.1",
                "actions": action_records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    archives_path = args.output_dir / "archive_before.json"
    archives_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-multibranch-archive.1",
                "branches": branch_archives,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    quality_diversity_path = args.output_dir / "quality_diversity_before.json"
    quality_diversity_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.multibranch-quality-diversity-archive.1",
                "branches": quality_diversity_archives,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    plans_path = args.output_dir / "plans.json"
    plans_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-multibranch-plan.1",
                "plans": plans,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    children_path = args.output_dir / "children.csv"
    forced_family_counts = {
        family_key: sum(
            row["forced_family_coverage"] == "true" and row["source_family_key_80_80"] == family_key
            for row in child_records
        )
        for family_key in sorted(
            {family_key for plan in plans.values() for family_key in plan["unresolved_family_keys"]}
        )
    }
    receipt = {
        "schema_version": "ampgent.autoresearch-lineage-probe.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256s": source_csv_sha256s,
        "source_candidate_count": len(rows),
        "source_cohort_sha256": sha256_file(source_cohort_path),
        "generation": args.generation,
        "prefer_full_support_within_families": (args.prefer_full_support_within_families),
        "historical_source_sha256s": historical_source_hashes,
        "unfiltered_input_sequence_exclusion_count": len(input_sequences),
        "historical_exclusion_sequence_count": len(historical_sequences),
        "postgresql_history_exclusion_enabled": args.include_postgresql_history,
        "history_check_status": history_check_status,
        "display_or_promotion_allowed": display_or_promotion_allowed,
        "postgresql_candidate_sequence_sha256_count": postgresql_candidate_history_count,
        "postgresql_operational_sequence_sha256_count": (
            postgresql_operational_history_count
        ),
        "postgresql_history_sequence_sha256_count": postgresql_history_count,
        "operator_release_sha256": operator_release_sha256,
        "branch_count": len(active_branches),
        "branches": list(active_branches),
        "replicate_count_per_branch": args.replicates,
        "action_count": len(action_records),
        "forced_family_coverage_enabled": args.force_unresolved_family_coverage,
        "forced_family_count": len(forced_family_counts),
        "forced_family_keys": list(forced_family_counts),
        "forced_family_materialized_child_counts": forced_family_counts,
        "uncovered_forced_family_keys": [
            family_key for family_key, count in forced_family_counts.items() if count == 0
        ],
        "action_type_counts": {
            action_type: sum(row["action_type"] == action_type for row in action_records)
            for action_type in (
                "masked_substitution",
                "controlled_crossover",
                "de_novo",
            )
        },
        "child_count": len(child_records),
        "exact_historical_replay_count": 0,
        "new_family_child_count": sum(
            row["diversity_qualified"] == "true" for row in child_records
        ),
        "children_csv_sha256": sha256_file(children_path),
        "actions_sha256": sha256_file(actions_path),
        "archive_before_sha256": sha256_file(archives_path),
        "quality_diversity_before_sha256": sha256_file(quality_diversity_path),
        "quality_diversity_parent_selection": True,
        "plans_sha256": sha256_file(plans_path),
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
        "target_sequence_identity_consumed": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (args.output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, action="append", required=True)
    parser.add_argument("--historical-csv", type=Path, action="append", default=[])
    parser.add_argument("--branch", action="append", choices=BRANCHES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--generation", type=int, default=2)
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument("--de-novo-quota", type=float, default=0.25)
    parser.add_argument("--minimum-calibrated-support", type=int, default=0)
    parser.add_argument("--prefer-full-support-within-families", action="store_true")
    parser.add_argument("--force-unresolved-family-coverage", action="store_true")
    parser.add_argument("--include-postgresql-history", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
