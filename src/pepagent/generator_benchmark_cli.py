from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

import yaml

from pepagent.generator_benchmark import (
    GeneratorBenchmarkManifest,
    audit_raw_generator_cohort,
)
from pepagent.model_workers.sequence_metrics_cli import evaluate

PLUGINS = (
    "physicochemical_developability",
    "amp_likeness",
    "hemolysis_risk",
    "toxicity_risk",
    "mic_potency",
    "mic_potency_amp_read",
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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


def _metric_values(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for record in result.get("records", []):
        candidate = values.setdefault(record["candidate_id"], {})
        for observation in record.get("observations", []):
            metric_name = observation["metric_name"]
            candidate[metric_name] = (
                observation["numeric_value"]
                if observation["numeric_value"] is not None
                else observation["text_value"]
            )
    return values


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    return statistics.median(values) if values else None


def run(args: argparse.Namespace) -> None:
    manifest_payload = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    manifest = GeneratorBenchmarkManifest.model_validate(manifest_payload)
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    audit_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    for generator in manifest.generators:
        for seed in manifest.seeds:
            raw_path = args.raw_dir / f"{generator.generator_id}_{seed}.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            if raw.get("generator_id") != generator.generator_id or raw.get("seed") != seed:
                raise ValueError(f"raw cohort identity mismatch: {raw_path}")
            audit = audit_raw_generator_cohort(
                raw["records"],
                raw_budget=manifest.raw_proposal_budget_per_seed,
                selected_k=manifest.selected_valid_unique_per_seed,
                minimum_length=manifest.minimum_length,
                maximum_length=manifest.maximum_length,
            )
            audit_rows.append(
                {
                    "benchmark_id": manifest.benchmark_id,
                    "generator_id": generator.generator_id,
                    "seed": seed,
                    **{key: value for key, value in audit.items() if key != "selected"},
                }
            )
            for selected_rank, item in enumerate(audit["selected"], start=1):
                selected_rows.append(
                    {
                        "candidate_id": (
                            f"{generator.generator_id}-{seed}-{selected_rank:03d}"
                        ),
                        "generator_id": generator.generator_id,
                        "seed": seed,
                        "selected_rank": selected_rank,
                        **item,
                    }
                )

    _write_csv(output_dir / "cohort_audit.csv", audit_rows)
    _write_csv(output_dir / "selected_candidates.csv", selected_rows)

    registry_payload = yaml.safe_load(args.registry.read_text(encoding="utf-8"))
    normalized_registry = _normalize_registry_paths(registry_payload, args.repo_root)
    normalized_registry_path = output_dir / "runtime.normalized.yaml"
    normalized_registry_path.write_text(
        yaml.safe_dump(normalized_registry, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    wide = {row["candidate_id"]: dict(row) for row in selected_rows}
    candidates = [
        {"id": row["candidate_id"], "sequence": row["sequence"]}
        for row in selected_rows
    ]
    metric_status_rows: list[dict[str, Any]] = []
    for plugin_name in PLUGINS:
        request = {
            "run_id": f"{manifest.benchmark_id}-{plugin_name}",
            "plugin": {"name": plugin_name, "parameters": {}},
            "candidates": candidates,
        }
        result = evaluate(
            request,
            output_dir / "work" / plugin_name,
            normalized_registry_path,
        )
        (output_dir / "metrics").mkdir(parents=True, exist_ok=True)
        (output_dir / "metrics" / f"{plugin_name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        metric_status_rows.append(
            {
                "plugin": plugin_name,
                "status": result["status"],
                "candidate_count": result["candidate_count"],
                "adapter_version": result.get("adapter_version"),
                "reason": result.get("reason"),
            }
        )
        if result["status"] != "complete":
            continue
        for candidate_id, values in _metric_values(result).items():
            wide[candidate_id].update(values)

    _write_csv(output_dir / "metric_status.csv", metric_status_rows)
    metric_rows = list(wide.values())
    _write_csv(output_dir / "candidate_metrics.csv", metric_rows)

    numeric_metrics = sorted(
        {
            key
            for row in metric_rows
            for key, value in row.items()
            if key not in selected_rows[0] and isinstance(value, (int, float))
        }
    )
    summary_rows: list[dict[str, Any]] = []
    for generator in manifest.generators:
        for seed in manifest.seeds:
            cohort = [
                row
                for row in metric_rows
                if row["generator_id"] == generator.generator_id and row["seed"] == seed
            ]
            audit = next(
                row
                for row in audit_rows
                if row["generator_id"] == generator.generator_id and row["seed"] == seed
            )
            summary_rows.append(
                {
                    "generator_id": generator.generator_id,
                    "seed": seed,
                    "selected_count": len(cohort),
                    "valid_unique_yield": audit["valid_unique_yield"],
                    **{f"median_{key}": _median(cohort, key) for key in numeric_metrics},
                }
            )
    _write_csv(output_dir / "generator_seed_summary.csv", summary_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
