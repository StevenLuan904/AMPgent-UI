from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.autoresearch_closed_loop import (
    CandidateEvidence,
    ContinuationPolicy,
    MetricObservation,
    MultiFrontArchivePolicy,
    compute_parent_child_delta,
    parse_evolution_action,
    parse_persisted_archive_snapshot,
    update_multi_front_archive,
)
from pepagent.provenance.hashing import sha256_file, sha256_json

BRANCHES = ("acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa")
COMPARABLE_METRICS = {
    "amp_read_log10_mic_um": ("minimize", "log10(uM)"),
    "llamp_log10_mic_um": ("minimize", "log10(uM)"),
    "macrel_amp_probability": ("maximize", "probability"),
    "macrel_hemolysis_probability": ("minimize", "probability"),
    "toxinpred3_hybrid_score": ("minimize", "dimensionless"),
    "guruprasad_instability_index": ("minimize", "index"),
    "maximum_hydrophobic_run": ("minimize", "residues"),
}
AUDIT_NUMERIC_METRICS = {
    "hydrophobic_moment_eisenberg": "dimensionless",
    "hydrophobic_ratio_modlamp": "fraction",
    "net_charge_ph7_4": "elementary_charge",
}
CATEGORICAL_METRICS = ("macrel_hemolysis_label", "toxinpred3_label")
FORMAL_METRICS = tuple(
    sorted(
        set(COMPARABLE_METRICS)
        | set(AUDIT_NUMERIC_METRICS)
        | set(CATEGORICAL_METRICS)
    )
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _evidence(row: dict[str, str], *, archive_eligible: bool) -> CandidateEvidence:
    metrics = {
        metric_name: MetricObservation(
            numeric_value=float(row[metric_name]),
            direction=direction,  # type: ignore[arg-type]
            unit=unit,
            version="frozen-score-all-registry-v1",
            out_of_domain=(
                metric_name == "guruprasad_instability_index"
                and len(row["sequence"]) < 20
            ),
        )
        for metric_name, (direction, unit) in COMPARABLE_METRICS.items()
    }
    return CandidateEvidence(
        candidate_id=row["sequence_sha256"],
        sequence=row["sequence"],
        sequence_sha256=row["sequence_sha256"],
        family_key=row["family_key_80_80"],
        metrics=metrics,
        archive_eligible=archive_eligible,
    )


def _flat_metric_delta(
    *,
    action_sha256: str,
    action_type: str,
    child: dict[str, str],
    baseline_role: str,
    parent: dict[str, str] | None,
    metric_name: str,
) -> dict[str, Any]:
    base = {
        "branch_key": child["branch_key"],
        "action_sha256": action_sha256,
        "action_type": action_type,
        "child_sequence_sha256": child["sequence_sha256"],
        "baseline_role": baseline_role,
        "parent_sequence_sha256": "" if parent is None else parent["sequence_sha256"],
        "metric_name": metric_name,
    }
    if parent is None:
        result = {
            **base,
            "metric_kind": "numeric" if metric_name not in CATEGORICAL_METRICS else "categorical",
            "comparable": "false",
            "reason": "de_novo_has_no_biological_parent",
            "direction": "",
            "unit": "",
            "parent_value": "",
            "child_value": child[metric_name],
            "raw_delta_child_minus_parent": "",
            "improvement_delta": "",
            "changed": "",
        }
    elif metric_name in CATEGORICAL_METRICS:
        result = {
            **base,
            "metric_kind": "categorical",
            "comparable": "true",
            "reason": "same_frozen_label_contract",
            "direction": "audit",
            "unit": "label",
            "parent_value": parent[metric_name],
            "child_value": child[metric_name],
            "raw_delta_child_minus_parent": "",
            "improvement_delta": "",
            "changed": str(parent[metric_name] != child[metric_name]).lower(),
        }
    else:
        parent_value = float(parent[metric_name])
        child_value = float(child[metric_name])
        raw_delta = child_value - parent_value
        if metric_name in COMPARABLE_METRICS:
            direction, unit = COMPARABLE_METRICS[metric_name]
            improvement = raw_delta if direction == "maximize" else -raw_delta
            comparable = "true"
            reason = "same_frozen_numeric_contract"
        else:
            direction = "audit"
            unit = AUDIT_NUMERIC_METRICS[metric_name]
            improvement = ""
            comparable = "false"
            reason = "audit_metric_has_no_monotonic_improvement_direction"
        result = {
            **base,
            "metric_kind": "numeric",
            "comparable": comparable,
            "reason": reason,
            "direction": direction,
            "unit": unit,
            "parent_value": parent_value,
            "child_value": child_value,
            "raw_delta_child_minus_parent": raw_delta,
            "improvement_delta": improvement,
            "changed": str(raw_delta != 0.0).lower(),
        }
    result["delta_sha256"] = sha256_json(result)
    return result


def run(args: argparse.Namespace) -> None:
    with args.parent_csv.open(encoding="utf-8-sig", newline="") as stream:
        parent_rows = list(csv.DictReader(stream))
    with args.child_csv.open(encoding="utf-8-sig", newline="") as stream:
        child_rows = list(csv.DictReader(stream))
    with args.challenger_csv.open(encoding="utf-8-sig", newline="") as stream:
        challenger_rows = list(csv.DictReader(stream))
    plans_payload = json.loads(args.plans_json.read_text(encoding="utf-8"))
    archives_payload = json.loads(args.archive_before_json.read_text(encoding="utf-8"))
    no_conflict_hashes = {row["sequence_sha256"] for row in challenger_rows}
    parents_by_id = {row["sequence_sha256"]: row for row in parent_rows}
    children_by_action = {row["action_sha256"]: row for row in child_rows}
    if len(children_by_action) != len(child_rows):
        raise ValueError("lineage close child action identities are not unique")
    child_generations = {int(row["generation"]) for row in child_rows}
    if len(child_generations) != 1:
        raise ValueError("lineage close requires exactly one child generation")
    generation = child_generations.pop()

    flat_deltas: list[dict[str, Any]] = []
    parent_child_receipts: list[dict[str, Any]] = []
    archive_updates: dict[str, Any] = {}
    replay_branches: dict[str, Any] = {}
    eligible_children: set[str] = set()
    for branch_key in BRANCHES:
        branch_parents = [row for row in parent_rows if row["branch_key"] == branch_key]
        branch_children = [row for row in child_rows if row["branch_key"] == branch_key]
        policy = MultiFrontArchivePolicy(
            known_family_keys=tuple(
                sorted({row["family_key_80_80"] for row in branch_parents})
            )
        )
        previous = parse_persisted_archive_snapshot(
            archives_payload["branches"][branch_key]
        )
        parent_evidence = [_evidence(row, archive_eligible=True) for row in branch_parents]
        evidence_by_id = {item.candidate_id: item for item in parent_evidence}
        action_receipts: list[dict[str, Any]] = []
        plan = plans_payload["plans"][branch_key]
        for action_payload in plan["actions"]:
            action = parse_evolution_action(action_payload)
            child_row = children_by_action[action.action_sha256]
            child_allowed = (
                child_row["display_eligible"].lower() == "true"
                and int(child_row["activity_model_support_count_calibrated"]) >= 2
                and child_row["sequence_sha256"] in no_conflict_hashes
            )
            if child_allowed:
                eligible_children.add(child_row["sequence_sha256"])
            child_evidence = _evidence(child_row, archive_eligible=child_allowed)
            delta = compute_parent_child_delta(action, child_evidence, evidence_by_id)
            delta_payload = delta.model_dump(mode="json")
            action_receipts.append(delta_payload)
            parent_child_receipts.append(delta_payload)
            baseline_specs: list[tuple[str, dict[str, str] | None]] = []
            parent_id = getattr(action, "parent_candidate_id", None)
            donor_id = getattr(action, "donor_candidate_id", None)
            if parent_id is not None:
                baseline_specs.append(("primary_parent", parents_by_id[parent_id]))
            if donor_id is not None:
                baseline_specs.append(("donor_parent", parents_by_id[donor_id]))
            if not baseline_specs:
                baseline_specs.append(("none", None))
            for baseline_role, parent_row in baseline_specs:
                for metric_name in FORMAL_METRICS:
                    flat_deltas.append(
                        _flat_metric_delta(
                            action_sha256=action.action_sha256,
                            action_type=action.action_type,
                            child=child_row,
                            baseline_role=baseline_role,
                            parent=parent_row,
                            metric_name=metric_name,
                        )
                    )
        child_evidence_rows = [
            _evidence(
                row,
                archive_eligible=row["sequence_sha256"] in eligible_children,
            )
            for row in branch_children
        ]
        update = update_multi_front_archive(
            previous,
            [*parent_evidence, *child_evidence_rows],
            policy,
            ContinuationPolicy(
                maximum_generations_per_run=5,
                minimum_high_quality_candidates=50,
                stagnation_patience_generations=2,
            ),
            generation=generation,
        )
        update_payload = update.model_dump(mode="json")
        archive_updates[branch_key] = update_payload
        replay_branches[branch_key] = {
            "archive_before_sha256": previous.archive_sha256,
            "archive_after_sha256": update.current.archive_sha256,
            "plan": plan,
            "parent_child_deltas": action_receipts,
            "archive_update": update_payload,
        }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    deltas_csv = args.output_dir / "formal_12_parent_child_deltas.csv"
    _write_csv(deltas_csv, flat_deltas)
    delta_receipts_path = args.output_dir / "parent_child_delta_receipts.json"
    delta_receipts_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-parent-child-delta-bundle.1",
                "receipts": parent_child_receipts,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    archive_updates_path = args.output_dir / "archive_updates.json"
    archive_updates_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-multibranch-archive-update.1",
                "branches": archive_updates,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    replay_path = args.output_dir / "replay_bundle.json"
    replay_payload = {
        "schema_version": "ampgent.autoresearch-multibranch-replay.1",
        "source_hashes": {
            "parent_csv_sha256": sha256_file(args.parent_csv),
            "child_csv_sha256": sha256_file(args.child_csv),
            "challenger_csv_sha256": sha256_file(args.challenger_csv),
            "plans_sha256": sha256_file(args.plans_json),
            "archive_before_sha256": sha256_file(args.archive_before_json),
        },
        "branches": replay_branches,
    }
    replay_payload["replay_payload_sha256"] = sha256_json(replay_payload)
    replay_path.write_text(
        json.dumps(replay_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "ampgent.autoresearch-lineage-close.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "branch_count": len(BRANCHES),
        "child_count": len(child_rows),
        "formal_metric_count": len(FORMAL_METRICS),
        "parent_child_delta_receipt_count": len(parent_child_receipts),
        "flat_metric_delta_count": len(flat_deltas),
        "challenger_no_conflict_child_count": len(eligible_children),
        "archive_gain_branch_count": sum(
            payload["continuation"]["archive_gain"]
            for payload in archive_updates.values()
        ),
        "formal_12_parent_child_deltas_sha256": sha256_file(deltas_csv),
        "parent_child_delta_receipts_sha256": sha256_file(delta_receipts_path),
        "archive_updates_sha256": sha256_file(archive_updates_path),
        "replay_bundle_sha256": sha256_file(replay_path),
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (args.output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-csv", type=Path, required=True)
    parser.add_argument("--child-csv", type=Path, required=True)
    parser.add_argument("--challenger-csv", type=Path, required=True)
    parser.add_argument("--plans-json", type=Path, required=True)
    parser.add_argument("--archive-before-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
