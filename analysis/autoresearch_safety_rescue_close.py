from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoresearch_lineage_close import FORMAL_METRICS, _evidence, _flat_metric_delta, _write_csv

from pepagent.autoresearch_closed_loop import (
    ContinuationPolicy,
    MultiFrontArchivePolicy,
    compute_parent_child_delta,
    parse_evolution_action,
    parse_persisted_archive_snapshot,
    update_multi_front_archive,
)
from pepagent.provenance.hashing import sha256_file, sha256_json


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def run(args: argparse.Namespace) -> None:
    baseline_rows = _read_csv(args.baseline_parent_csv)
    qualified_rows = _read_csv(args.qualified_archive_csv)
    prior_child_rows = [
        row for path in args.prior_child_csv for row in _read_csv(path)
    ]
    prior_no_conflict = {
        row["sequence_sha256"]
        for path in args.prior_challenger_csv
        for row in _read_csv(path)
    }
    child_rows = _read_csv(args.child_csv)
    no_conflict = {row["sequence_sha256"] for row in _read_csv(args.challenger_csv)}
    plans = json.loads(args.plans_json.read_text(encoding="utf-8"))["plans"]
    previous_payload = json.loads(args.archive_updates_json.read_text(encoding="utf-8"))[
        "branches"
    ]["acea"]["current"]
    previous = parse_persisted_archive_snapshot(previous_payload)
    previous_ids = set(previous.source_candidate_ids)

    old_candidates: dict[str, Any] = {}
    for row in qualified_rows:
        if row["branch_key"] == "acea" and row["sequence_sha256"] in previous_ids:
            old_candidates[row["sequence_sha256"]] = _evidence(row, archive_eligible=True)
    for row in prior_child_rows:
        digest = row["sequence_sha256"]
        if row["branch_key"] != "acea" or digest not in previous_ids:
            continue
        allowed = (
            row["display_eligible"].lower() == "true"
            and int(row["activity_model_support_count_calibrated"]) >= 2
            and digest in prior_no_conflict
        )
        old_candidates[digest] = _evidence(row, archive_eligible=allowed)
    if set(old_candidates) != previous_ids:
        missing = sorted(previous_ids - set(old_candidates))
        extra = sorted(set(old_candidates) - previous_ids)
        raise ValueError(f"previous archive evidence mismatch: missing={missing}, extra={extra}")

    baseline_by_id = {row["sequence_sha256"]: row for row in baseline_rows}
    baseline_evidence = {
        digest: _evidence(row, archive_eligible=False)
        for digest, row in baseline_by_id.items()
    }
    child_by_action = {row["action_sha256"]: row for row in child_rows}
    if len(child_by_action) != len(child_rows):
        raise ValueError("safety rescue child action identities are not unique")
    action_payloads = [
        payload
        for payload in plans["acea"]["actions"]
        if payload["action_sha256"] in child_by_action
    ]
    if {payload["action_sha256"] for payload in action_payloads} != set(child_by_action):
        raise ValueError("safety rescue plan/child action coverage drifted")

    flat_deltas: list[dict[str, Any]] = []
    delta_receipts: list[dict[str, Any]] = []
    current_evidence = []
    eligible_children: set[str] = set()
    for action_payload in action_payloads:
        action = parse_evolution_action(action_payload)
        child = child_by_action[action.action_sha256]
        parent = baseline_by_id[action.parent_candidate_id]
        allowed = (
            child["display_eligible"].lower() == "true"
            and int(child["activity_model_support_count_calibrated"]) >= 2
            and child["sequence_sha256"] in no_conflict
        )
        if allowed:
            eligible_children.add(child["sequence_sha256"])
        child_evidence = _evidence(child, archive_eligible=allowed)
        current_evidence.append(child_evidence)
        delta = compute_parent_child_delta(action, child_evidence, baseline_evidence)
        delta_receipts.append(delta.model_dump(mode="json"))
        for metric_name in FORMAL_METRICS:
            flat_deltas.append(
                _flat_metric_delta(
                    action_sha256=action.action_sha256,
                    action_type=action.action_type,
                    child=child,
                    baseline_role="primary_parent",
                    parent=parent,
                    metric_name=metric_name,
                )
            )

    policy = MultiFrontArchivePolicy(known_family_keys=previous.known_family_keys)
    generation = max(int(row["generation"]) for row in child_rows)
    update = update_multi_front_archive(
        previous,
        [*old_candidates.values(), *current_evidence],
        policy,
        ContinuationPolicy(
            maximum_generations_per_run=5,
            minimum_high_quality_candidates=50,
            stagnation_patience_generations=2,
        ),
        generation=generation,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    deltas_path = args.output_dir / "formal_12_parent_child_deltas.csv"
    receipts_path = args.output_dir / "parent_child_delta_receipts.json"
    archive_path = args.output_dir / "archive_updates.json"
    replay_path = args.output_dir / "replay_bundle.json"
    _write_csv(deltas_path, flat_deltas)
    receipts_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-parent-child-delta-bundle.1",
                "receipts": delta_receipts,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    update_payload = update.model_dump(mode="json")
    archive_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-multibranch-archive-update.1",
                "branches": {"acea": update_payload},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    replay = {
        "schema_version": "ampgent.autoresearch-safety-rescue-replay.1",
        "source_hashes": {
            "baseline_parent_csv_sha256": sha256_file(args.baseline_parent_csv),
            "qualified_archive_csv_sha256": sha256_file(args.qualified_archive_csv),
            "prior_child_csv_sha256s": [
                sha256_file(path) for path in args.prior_child_csv
            ],
            "prior_challenger_csv_sha256s": [
                sha256_file(path) for path in args.prior_challenger_csv
            ],
            "child_csv_sha256": sha256_file(args.child_csv),
            "challenger_csv_sha256": sha256_file(args.challenger_csv),
            "plans_sha256": sha256_file(args.plans_json),
            "archive_updates_sha256": sha256_file(args.archive_updates_json),
        },
        "previous_archive_sha256": previous.archive_sha256,
        "current_archive_sha256": update.current.archive_sha256,
        "actions": action_payloads,
        "parent_child_deltas": delta_receipts,
        "archive_update": update_payload,
    }
    replay["replay_payload_sha256"] = sha256_json(replay)
    replay_path.write_text(
        json.dumps(replay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    receipt = {
        "schema_version": "ampgent.autoresearch-safety-rescue-close.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "child_count": len(child_rows),
        "formal_metric_count": len(FORMAL_METRICS),
        "flat_metric_delta_count": len(flat_deltas),
        "challenger_no_conflict_child_count": len(eligible_children),
        "archive_gain": update.continuation.archive_gain,
        "new_family_count": update.new_family_count,
        "high_quality_candidate_count": update.continuation.high_quality_candidate_count,
        "formal_12_parent_child_deltas_sha256": sha256_file(deltas_path),
        "parent_child_delta_receipts_sha256": sha256_file(receipts_path),
        "archive_updates_sha256": sha256_file(archive_path),
        "replay_bundle_sha256": sha256_file(replay_path),
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (args.output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-parent-csv", type=Path, required=True)
    parser.add_argument("--qualified-archive-csv", type=Path, required=True)
    parser.add_argument(
        "--prior-child-csv", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--prior-challenger-csv", type=Path, action="append", required=True
    )
    parser.add_argument("--child-csv", type=Path, required=True)
    parser.add_argument("--challenger-csv", type=Path, required=True)
    parser.add_argument("--plans-json", type=Path, required=True)
    parser.add_argument("--archive-updates-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
