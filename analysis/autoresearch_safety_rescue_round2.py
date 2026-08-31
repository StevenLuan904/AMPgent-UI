from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from autoresearch_safety_rescue_variants import (
    EDITABLE,
    REPLACEMENTS,
    SEARCH_PLUGINS,
    _historical_sequence_sha256s,
    _is_low_hemolysis,
    _is_non_toxin,
    _metric_values,
    _normalize_registry_paths,
    _write_csv,
)

from pepagent.autoresearch_planner import _hydrophobic_fraction, _sequence_prescreen
from pepagent.model_workers.sequence_metrics_cli import evaluate
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text


def _dominates(left: dict[str, str], right: dict[str, str]) -> bool:
    left_vector = (
        float(left["macrel_hemolysis_probability"]),
        float(left["toxinpred3_hybrid_score"]),
        -float(left["macrel_amp_probability"]),
    )
    right_vector = (
        float(right["macrel_hemolysis_probability"]),
        float(right["toxinpred3_hybrid_score"]),
        -float(right["macrel_amp_probability"]),
    )
    return all(a <= b for a, b in zip(left_vector, right_vector, strict=True)) and any(
        a < b for a, b in zip(left_vector, right_vector, strict=True)
    )


def _select_beam(rows: list[dict[str, str]], beam_size: int) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for branch_key in sorted({row["branch_key"] for row in rows}):
        cohort = [row for row in rows if row["branch_key"] == branch_key]
        front = [
            row
            for row in cohort
            if not any(_dominates(other, row) for other in cohort if other is not row)
        ]
        front.sort(
            key=lambda row: (
                -sum(
                    (
                        _is_non_toxin(row["toxinpred3_label"]),
                        _is_low_hemolysis(row["macrel_hemolysis_label"]),
                    )
                ),
                float(row["macrel_hemolysis_probability"]),
                float(row["toxinpred3_hybrid_score"]),
                -float(row["macrel_amp_probability"]),
                row["sequence"],
            )
        )
        selected.extend(front[:beam_size])
    return selected


def _generate(beam: list[dict[str, str]], historical_sha256s: set[str]) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for parent in beam:
        sequence = parent["sequence"]
        first_position = int(parent["edit_position_1based"]) - 1
        for position, old_residue in enumerate(sequence):
            if position == first_position or old_residue not in EDITABLE:
                continue
            for new_residue in REPLACEMENTS:
                if new_residue == old_residue:
                    continue
                child = sequence[:position] + new_residue + sequence[position + 1 :]
                digest = sha256_text(child)
                if digest in historical_sha256s or digest in seen:
                    continue
                instability, maximum_hydrophobic_run, net_charge = _sequence_prescreen(child)
                hydrophobic_fraction = _hydrophobic_fraction(child)
                if not (
                    instability < 50.0
                    and maximum_hydrophobic_run <= 2
                    and hydrophobic_fraction <= 0.45
                    and net_charge >= 3.0
                ):
                    continue
                seen.add(digest)
                generated.append(
                    {
                        "branch_key": parent["branch_key"],
                        "action_type": "safety_rescue_double_substitution",
                        "operator_id": "autoresearch-safety-rescue-substitution-v2",
                        "parent_sequence": parent["parent_sequence"],
                        "intermediate_sequence": sequence,
                        "edit_position_1based": parent["edit_position_1based"],
                        "edit": parent["edit"],
                        "edit_2_position_1based": position + 1,
                        "edit_2": f"{old_residue}{position + 1}{new_residue}",
                        "sequence": child,
                        "sequence_sha256": digest,
                        "candidate_id": f"rescue2-{digest[:20]}",
                        "guruprasad_instability_index": instability,
                        "maximum_hydrophobic_run": maximum_hydrophobic_run,
                        "hydrophobic_fraction": hydrophobic_fraction,
                        "net_charge_ph7_4": net_charge,
                        "historical_exact_replay": "false",
                        "score_all_status": "search_prefilter",
                    }
                )
    return generated


def run(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with args.round1_scores.open(encoding="utf-8-sig", newline="") as stream:
        round1_rows = list(csv.DictReader(stream))
    beam = _select_beam(round1_rows, args.beam_size)
    historical_sha256s = asyncio.run(_historical_sequence_sha256s())
    generated = _generate(beam, historical_sha256s)
    if not generated:
        raise ValueError("no novel strict double-substitution variants generated")

    registry_payload = yaml.safe_load(args.registry.read_text(encoding="utf-8"))
    normalized_registry = _normalize_registry_paths(registry_payload, repo_root)
    normalized_registry_path = output_dir / "runtime.normalized.yaml"
    normalized_registry_path.write_text(
        yaml.safe_dump(normalized_registry, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    candidates = [{"id": row["candidate_id"], "sequence": row["sequence"]} for row in generated]
    wide = {row["candidate_id"]: dict(row) for row in generated}
    statuses: list[dict[str, Any]] = []
    metrics_dir = output_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in SEARCH_PLUGINS:
        result = evaluate(
            {
                "run_id": f"autoresearch-safety-rescue-round2-{plugin_name}",
                "plugin": {"name": plugin_name, "parameters": {}},
                "candidates": candidates,
            },
            output_dir / "work" / plugin_name,
            normalized_registry_path,
        )
        result_path = metrics_dir / f"{plugin_name}.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        statuses.append(
            {
                "plugin": plugin_name,
                "status": result["status"],
                "candidate_count": result["candidate_count"],
                "adapter_version": result.get("adapter_version"),
                "result_sha256": sha256_file(result_path),
            }
        )
        if result["status"] != "complete":
            raise RuntimeError(f"search plugin unavailable: {plugin_name}")
        for candidate_id, values in _metric_values(result).items():
            wide[candidate_id].update(values)

    rows = list(wide.values())
    for row in rows:
        safety_pass = _is_non_toxin(row.get("toxinpred3_label")) and _is_low_hemolysis(
            row.get("macrel_hemolysis_label")
        )
        activity_prefilter = float(row.get("macrel_amp_probability", 0.0)) >= 0.45
        row["safety_hard_gate_pass"] = str(safety_pass).lower()
        row["activity_prefilter_pass"] = str(activity_prefilter).lower()
        row["full_score_shortlist"] = str(safety_pass and activity_prefilter).lower()
    rows.sort(
        key=lambda row: (
            row["full_score_shortlist"] != "true",
            -float(row.get("macrel_amp_probability", 0.0)),
            float(row.get("macrel_hemolysis_probability", 1.0)),
            float(row.get("toxinpred3_hybrid_score", 1.0)),
            row["sequence"],
        )
    )
    shortlist = [row for row in rows if row["full_score_shortlist"] == "true"]
    _write_csv(output_dir / "all_search_scores.csv", rows)
    if shortlist:
        _write_csv(output_dir / "full_score_shortlist.csv", shortlist)
    receipt = {
        "schema_version": "ampgent.autoresearch-safety-rescue-search.2",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "round1_scores_sha256": sha256_file(args.round1_scores),
        "historical_sequence_exclusion_count": len(historical_sha256s),
        "beam_size_per_branch": args.beam_size,
        "beam_candidate_count": len(beam),
        "generated_novel_strict_count": len(rows),
        "safety_hard_gate_pass_count": sum(row["safety_hard_gate_pass"] == "true" for row in rows),
        "full_score_shortlist_count": len(shortlist),
        "plugin_status": statuses,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
        "all_search_scores_sha256": sha256_file(output_dir / "all_search_scores.csv"),
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round1-scores", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--beam-size", type=int, default=6)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
