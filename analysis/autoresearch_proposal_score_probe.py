from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pepagent.model_workers.physicochemical_runtime.cli import (
    METHOD_VERSION as PHYSICOCHEMICAL_METHOD_VERSION,
)
from pepagent.model_workers.physicochemical_runtime.cli import describe
from pepagent.model_workers.sequence_metrics_cli import evaluate
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text
from pepagent.seven_branch_design import SEQUENCE_METRICS

FORMAL_PLUGINS = (
    "physicochemical_developability",
    "hemolysis_risk",
    "toxicity_risk",
    "mic_potency",
    "mic_potency_amp_read",
)
AUXILIARY_PLUGINS = (
    "amp_likeness",
)


def _normalize_registry_paths(value: Any, repo_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_registry_paths(item, repo_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_registry_paths(item, repo_root) for item in value]
    if isinstance(value, str):
        marker = "\\agent-platform\\"
        marker_at = value.lower().find(marker)
        if marker_at >= 0:
            return str(repo_root) + value[marker_at + len("\\agent-platform") :]
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    fieldnames = list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _metric_values(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for record in result.get("records", []):
        row = values.setdefault(str(record["candidate_id"]), {})
        for observation in record.get("observations", []):
            row[str(observation["metric_name"])] = (
                observation["numeric_value"]
                if observation.get("numeric_value") is not None
                else observation.get("text_value")
            )
    return values


def _is_non_toxin(value: Any) -> bool:
    return str(value).strip().lower().replace("_", "-") == "non-toxin"


def _is_low_hemolysis(value: Any) -> bool:
    return str(value).strip().lower() == "low"


def _load_reusable_result(
    path: Path,
    *,
    candidates: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("candidate_count") != len(candidates):
        raise ValueError(f"reusable metric candidate count drifted: {path}")
    if result.get("status") == "complete":
        expected = [item["id"] for item in candidates]
        observed = [str(item.get("candidate_id", "")) for item in result.get("records", [])]
        if observed != expected:
            raise ValueError(f"reusable metric candidate identity/order drifted: {path}")
    return result


def run(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with args.input_csv.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    if args.require_safety_hard_gate_pass:
        source_rows = [
            row
            for row in source_rows
            if str(row.get("safety_hard_gate_pass", "")).lower() == "true"
        ]
    if not source_rows:
        raise ValueError("proposal CSV is empty")

    candidate_rows: list[dict[str, Any]] = []
    seen_sequences: set[str] = set()
    for row in source_rows:
        sequence = str(row["sequence"]).strip().upper()
        if sequence in seen_sequences:
            raise ValueError(f"duplicate proposal sequence: {sequence}")
        seen_sequences.add(sequence)
        candidate_rows.append(
            {
                **row,
                "candidate_id": f"proposal-{sha256_text(sequence)[:20]}",
                "sequence": sequence,
                **{
                    metric_name: metric_value
                    for metric_name, metric_value in describe(
                        sequence,
                        ph=7.4,
                        c_terminal_amidated=False,
                        hydrophobic_moment_angle=100,
                    ).items()
                    if metric_name
                    in {"maximum_hydrophobic_run", "guruprasad_instability_index"}
                },
            }
        )

    registry_payload = yaml.safe_load(args.registry.read_text(encoding="utf-8"))
    normalized_registry = _normalize_registry_paths(registry_payload, repo_root)
    normalized_registry_path = output_dir / "runtime.normalized.yaml"
    normalized_registry_path.write_text(
        yaml.safe_dump(normalized_registry, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    candidates = [
        {"id": row["candidate_id"], "sequence": row["sequence"]} for row in candidate_rows
    ]
    wide = {row["candidate_id"]: dict(row) for row in candidate_rows}
    statuses: list[dict[str, Any]] = []
    metrics_dir = output_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    plugins = FORMAL_PLUGINS + (() if args.skip_amplify else AUXILIARY_PLUGINS)
    for plugin_name in plugins:
        request = {
            "run_id": f"autoresearch-proposal-score-probe-{plugin_name}",
            "plugin": {"name": plugin_name, "parameters": {}},
            "candidates": candidates,
        }
        result_path = metrics_dir / f"{plugin_name}.json"
        result = (
            _load_reusable_result(result_path, candidates=candidates)
            if args.reuse_existing_metrics
            else None
        )
        reused = result is not None
        if result is None:
            result = evaluate(
                request,
                output_dir / "work" / plugin_name,
                normalized_registry_path,
            )
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        statuses.append(
            {
                "plugin": plugin_name,
                "status": result["status"],
                "candidate_count": result["candidate_count"],
                "adapter_version": result.get("adapter_version"),
                "result_sha256": sha256_file(result_path),
                "reused_verified_result": reused,
                "reason_category": None
                if result["status"] == "complete"
                else "runtime_unavailable",
            }
        )
        if result["status"] == "complete":
            for candidate_id, values in _metric_values(result).items():
                wide[candidate_id].update(values)

    scored_rows: list[dict[str, Any]] = []
    formal_names = set(SEQUENCE_METRICS)
    for row in wide.values():
        complete_names = sorted(name for name in formal_names if row.get(name) not in (None, ""))
        formal_complete = len(complete_names) == len(formal_names)
        display_eligible = bool(
            formal_complete
            and _is_non_toxin(row.get("toxinpred3_label"))
            and _is_low_hemolysis(row.get("macrel_hemolysis_label"))
            and float(row["guruprasad_instability_index"]) <= 50.0
        )
        activity_support_count = sum(
            (
                float(row.get("macrel_amp_probability", 0.0)) >= 0.5,
                float(row.get("llamp_log10_mic_um", 99.0)) <= 1.0,
                float(row.get("amp_read_log10_mic_um", 99.0)) <= 1.0,
            )
        )
        scored_rows.append(
            {
                **row,
                "formal_metric_count": len(complete_names),
                "formal_12_complete": str(formal_complete).lower(),
                "display_eligible": str(display_eligible).lower(),
                "activity_model_support_count": activity_support_count,
                "excellent_sequence_stage": str(
                    display_eligible and activity_support_count >= 2
                ).lower(),
                "structure_md_status": "not_started",
            }
        )
    scored_rows.sort(
        key=lambda row: (
            row["branch_key"],
            row["excellent_sequence_stage"] != "true",
            -int(row["activity_model_support_count"]),
            float(row.get("amp_read_log10_mic_um", 99.0)),
            float(row.get("llamp_log10_mic_um", 99.0)),
            row["sequence"],
        )
    )
    _write_csv(output_dir / "candidate_scores.csv", scored_rows)
    _write_csv(output_dir / "metric_status.csv", statuses)

    branch_summary = []
    for branch_key in sorted({row["branch_key"] for row in scored_rows}):
        cohort = [row for row in scored_rows if row["branch_key"] == branch_key]
        branch_summary.append(
            {
                "branch_key": branch_key,
                "proposal_count": len(cohort),
                "formal_12_complete_count": sum(
                    row["formal_12_complete"] == "true" for row in cohort
                ),
                "display_eligible_count": sum(row["display_eligible"] == "true" for row in cohort),
                "excellent_sequence_stage_count": sum(
                    row["excellent_sequence_stage"] == "true" for row in cohort
                ),
            }
        )
    _write_csv(output_dir / "branch_summary.csv", branch_summary)
    receipt = {
        "schema_version": "ampgent.autoresearch-proposal-score-probe.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256": sha256_file(args.input_csv),
        "registry_sha256": sha256_file(normalized_registry_path),
        "proposal_count": len(scored_rows),
        "formal_metric_names": sorted(formal_names),
        "deterministic_metric_supplement": {
            "method_version": PHYSICOCHEMICAL_METHOD_VERSION,
            "metric_names": [
                "guruprasad_instability_index",
                "maximum_hydrophobic_run",
            ],
            "parameters": {
                "ph": 7.4,
                "c_terminal_amidated": False,
                "hydrophobic_moment_angle": 100,
            },
        },
        "plugin_status": statuses,
        "formal_12_complete_count": sum(row["formal_12_complete"] == "true" for row in scored_rows),
        "display_eligible_count": sum(row["display_eligible"] == "true" for row in scored_rows),
        "excellent_sequence_stage_count": sum(
            row["excellent_sequence_stage"] == "true" for row in scored_rows
        ),
        "branch_summary": branch_summary,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
        "candidate_scores_sha256": sha256_file(output_dir / "candidate_scores.csv"),
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-safety-hard-gate-pass", action="store_true")
    parser.add_argument("--skip-amplify", action="store_true")
    parser.add_argument("--reuse-existing-metrics", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
