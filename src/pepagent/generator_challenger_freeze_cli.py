from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from pepagent.generator_benchmark import (
    GeneratorChallengerManifest,
    audit_raw_generator_cohort,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def freeze_raw_cohorts(
    manifest: GeneratorChallengerManifest,
    raw_dir: Path,
) -> dict[str, Any]:
    expected_weights = {
        item.path: (item.size_bytes, item.sha256) for item in manifest.generator.weights
    }
    audits: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    raw_files: list[dict[str, Any]] = []
    selected_sequence_seeds: dict[str, set[int]] = {}
    for seed in manifest.seeds:
        path = raw_dir / f"{manifest.generator.generator_id}_{seed}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("generator_id") != manifest.generator.generator_id:
            raise ValueError(f"raw generator identity mismatch: {path}")
        if raw.get("seed") != seed:
            raise ValueError(f"raw seed identity mismatch: {path}")
        if raw.get("raw_proposal_budget") != manifest.raw_proposal_budget_per_seed:
            raise ValueError(f"raw budget mismatch: {path}")
        if raw.get("adapter_version") != manifest.generator.adapter_version:
            raise ValueError(f"adapter version mismatch: {path}")
        if raw.get("internal_score_filtering_enabled") is not False:
            raise ValueError(f"internal score filtering flag mismatch: {path}")
        if raw.get("internal_regressors_loaded") is not False:
            raise ValueError(f"internal regressor flag mismatch: {path}")
        sampling = raw.get("sampling", {})
        expected_sampling = {
            "top_k": manifest.sampling.top_k,
            "top_p": manifest.sampling.top_p,
            "temperature": manifest.sampling.temperature,
            "decode_steps": manifest.sampling.decode_steps,
            "learned_prompt_tokens": manifest.sampling.learned_prompt_tokens,
            "batch_size": manifest.sampling.batch_size,
            "batches": manifest.sampling.batches_per_seed,
        }
        if sampling != expected_sampling:
            raise ValueError(f"sampling contract mismatch: {path}")
        artifacts = {item["path"]: item for item in raw.get("artifacts", [])}
        for artifact_path, (size_bytes, sha256) in expected_weights.items():
            observed = artifacts.get(artifact_path)
            if observed is None:
                raise ValueError(f"raw provenance missing weight {artifact_path}: {path}")
            if observed.get("size_bytes") != size_bytes or observed.get("sha256") != sha256:
                raise ValueError(f"raw weight provenance mismatch for {artifact_path}: {path}")
        audit = audit_raw_generator_cohort(
            raw["records"],
            raw_budget=manifest.raw_proposal_budget_per_seed,
            selected_k=manifest.selected_valid_unique_per_seed,
            minimum_length=manifest.minimum_length,
            maximum_length=manifest.maximum_length,
        )
        audits.append(
            {
                "benchmark_id": manifest.benchmark_id,
                "generator_id": manifest.generator.generator_id,
                "seed": seed,
                **{key: value for key, value in audit.items() if key != "selected"},
            }
        )
        for selected_rank, item in enumerate(audit["selected"], start=1):
            selected.append(
                {
                    "candidate_id": f"{manifest.generator.generator_id}-{seed}-{selected_rank:03d}",
                    "generator_id": manifest.generator.generator_id,
                    "seed": seed,
                    "selected_rank": selected_rank,
                    **item,
                }
            )
            selected_sequence_seeds.setdefault(item["sequence_sha256"], set()).add(seed)
        raw_files.append(
            {
                "path": path.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "seed": seed,
                "raw_records": len(raw["records"]),
            }
        )
    expected_selected = len(manifest.seeds) * manifest.selected_valid_unique_per_seed
    if len(selected) != expected_selected:
        raise ValueError(
            f"formal selected cohort is short: expected {expected_selected}, got {len(selected)}"
        )
    cross_seed_duplicates = sum(len(seeds) > 1 for seeds in selected_sequence_seeds.values())
    return {
        "audits": audits,
        "selected": selected,
        "freeze_manifest": {
            "benchmark_id": manifest.benchmark_id,
            "version": manifest.version,
            "reference_benchmark_id": manifest.reference_benchmark_id,
            "reference_benchmark_revision": manifest.reference_benchmark_revision,
            "generator_id": manifest.generator.generator_id,
            "adapter_version": manifest.generator.adapter_version,
            "raw_files": raw_files,
            "raw_file_count": len(raw_files),
            "raw_records": sum(item["raw_records"] for item in raw_files),
            "selected_occurrences": len(selected),
            "selected_unique_across_seeds": len(selected_sequence_seeds),
            "cross_seed_duplicate_sequences": cross_seed_duplicates,
            "raw_outputs_frozen_before_metrics": True,
            "metrics_started": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    payload = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    manifest = GeneratorChallengerManifest.model_validate(payload)
    result = freeze_raw_cohorts(manifest, args.raw_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cohort_audit_path = args.output_dir / "cohort_audit.csv"
    selected_path = args.output_dir / "selected_candidates.csv"
    _write_csv(cohort_audit_path, result["audits"])
    _write_csv(selected_path, result["selected"])
    result["freeze_manifest"]["cohort_audit"] = {
        "path": cohort_audit_path.as_posix(),
        "size_bytes": cohort_audit_path.stat().st_size,
        "sha256": _sha256(cohort_audit_path),
    }
    result["freeze_manifest"]["selected_candidates"] = {
        "path": selected_path.as_posix(),
        "size_bytes": selected_path.stat().st_size,
        "sha256": _sha256(selected_path),
    }
    (args.output_dir / "raw_freeze_manifest.json").write_text(
        json.dumps(result["freeze_manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["freeze_manifest"], ensure_ascii=False))


if __name__ == "__main__":
    main()
