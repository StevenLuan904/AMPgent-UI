from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

import yaml

from pepagent.generator_benchmark import GeneratorChallengerManifest
from pepagent.model_workers.sequence_metrics_cli import evaluate

ESSENTIAL_PLUGINS = (
    "physicochemical_developability",
    "hemolysis_risk",
    "toxicity_risk",
    "mic_potency",
    "mic_potency_amp_read",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _metric_values(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for record in result.get("records", []):
        candidate = values.setdefault(record["candidate_id"], {})
        for observation in record.get("observations", []):
            value = observation["numeric_value"]
            if value is None:
                value = observation["text_value"]
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"non-finite metric for {record['candidate_id']}")
            candidate[observation["metric_name"]] = value
    return values


def _validate_frozen_inputs(
    manifest: GeneratorChallengerManifest,
    freeze_manifest_path: Path,
    selected_path: Path,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    freeze = json.loads(freeze_manifest_path.read_text(encoding="utf-8"))
    if freeze.get("benchmark_id") != manifest.benchmark_id:
        raise ValueError("freeze benchmark identity mismatch")
    if freeze.get("metrics_started") is not False:
        raise ValueError("freeze manifest must precede metric execution")
    selected_provenance = freeze.get("selected_candidates", {})
    if selected_provenance.get("size_bytes") != selected_path.stat().st_size:
        raise ValueError("selected cohort size drifted after freeze")
    if selected_provenance.get("sha256") != _sha256(selected_path):
        raise ValueError("selected cohort SHA-256 drifted after freeze")
    rows = _read_csv(selected_path)
    expected = len(manifest.seeds) * manifest.selected_valid_unique_per_seed
    if len(rows) != expected:
        raise ValueError(f"expected {expected} frozen candidates, got {len(rows)}")
    candidate_ids = [row["candidate_id"] for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("frozen candidate IDs must be unique")
    for seed in manifest.seeds:
        if sum(int(row["seed"]) == seed for row in rows) != (
            manifest.selected_valid_unique_per_seed
        ):
            raise ValueError(f"frozen selected count mismatch for seed {seed}")
    return freeze, rows


def run(args: argparse.Namespace) -> None:
    manifest = GeneratorChallengerManifest.model_validate(
        yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    )
    freeze, selected_rows = _validate_frozen_inputs(
        manifest,
        args.freeze_manifest,
        args.selected_candidates,
    )
    candidates = [
        {"id": row["candidate_id"], "sequence": row["sequence"]}
        for row in selected_rows
    ]
    wide: dict[str, dict[str, Any]] = {
        row["candidate_id"]: dict(row) for row in selected_rows
    }
    status_rows: list[dict[str, Any]] = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in ESSENTIAL_PLUGINS:
        result = evaluate(
            {
                "run_id": f"{manifest.benchmark_id}-{plugin_name}",
                "plugin": {"name": plugin_name, "parameters": {}},
                "candidates": candidates,
            },
            args.output_dir / "work" / plugin_name,
            args.registry,
        )
        metrics_dir = args.output_dir / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        result_path = metrics_dir / f"{plugin_name}.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        status_rows.append(
            {
                "plugin": plugin_name,
                "status": result["status"],
                "candidate_count": result["candidate_count"],
                "adapter_version": result.get("adapter_version"),
                "reason": result.get("reason"),
                "result_sha256": _sha256(result_path),
            }
        )
        if result["status"] != "complete":
            raise RuntimeError(f"essential metric failed closed: {plugin_name}")
        values = _metric_values(result)
        if set(values) != set(wide):
            raise ValueError(f"essential metric candidate coverage mismatch: {plugin_name}")
        for candidate_id, observations in values.items():
            wide[candidate_id].update(observations)
    metric_rows = list(wide.values())
    _write_csv(args.output_dir / "metric_status.csv", status_rows)
    _write_csv(args.output_dir / "candidate_metrics.csv", metric_rows)
    numeric_keys = sorted(
        {
            key
            for row in metric_rows
            for key, value in row.items()
            if isinstance(value, (int, float)) and math.isfinite(float(value))
        }
    )
    summary_rows: list[dict[str, Any]] = []
    audit_by_seed = {int(row["seed"]): row for row in _read_csv(args.cohort_audit)}
    for seed in manifest.seeds:
        cohort = [row for row in metric_rows if int(row["seed"]) == seed]
        summary_rows.append(
            {
                "generator_id": manifest.generator.generator_id,
                "seed": seed,
                "selected_count": len(cohort),
                "valid_unique_yield": float(audit_by_seed[seed]["valid_unique_yield"]),
                **{
                    f"median_{key}": statistics.median(
                        float(row[key]) for row in cohort if isinstance(row.get(key), (int, float))
                    )
                    for key in numeric_keys
                },
            }
        )
    _write_csv(args.output_dir / "generator_seed_summary.csv", summary_rows)
    completion = {
        "benchmark_id": manifest.benchmark_id,
        "generator_id": manifest.generator.generator_id,
        "freeze_manifest_sha256": _sha256(args.freeze_manifest),
        "selected_candidates_sha256": freeze["selected_candidates"]["sha256"],
        "registry_sha256": _sha256(args.registry),
        "candidate_count": len(metric_rows),
        "essential_plugins": list(ESSENTIAL_PLUGINS),
        "all_essential_complete": True,
        "candidate_metrics_sha256": _sha256(args.output_dir / "candidate_metrics.csv"),
        "generator_seed_summary_sha256": _sha256(
            args.output_dir / "generator_seed_summary.csv"
        ),
    }
    (args.output_dir / "completion_manifest.json").write_text(
        json.dumps(completion, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(completion, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--freeze-manifest", type=Path, required=True)
    parser.add_argument("--selected-candidates", type=Path, required=True)
    parser.add_argument("--cohort-audit", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
