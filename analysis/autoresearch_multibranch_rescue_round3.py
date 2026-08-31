from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from autoresearch_challenger_rescue_round3 import _run_hemopi2
from autoresearch_safety_rescue_round2 import _select_beam
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

EXCLUDED_BRANCHES = frozenset({"acea"})


def _generate(beam: list[dict[str, str]], historical_sha256s: set[str]) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for parent in beam:
        sequence = parent["sequence"]
        used_positions = {
            int(parent["edit_position_1based"]) - 1,
            int(parent["edit_2_position_1based"]) - 1,
        }
        for position, old_residue in enumerate(sequence):
            if position in used_positions or old_residue not in EDITABLE:
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
                        "action_type": "safety_rescue_triple_substitution",
                        "operator_id": "autoresearch-safety-rescue-substitution-v3",
                        "root_parent_sequence": parent["parent_sequence"],
                        "round2_parent_sequence": sequence,
                        "edit": parent["edit"],
                        "edit_2": parent["edit_2"],
                        "edit_3_position_1based": position + 1,
                        "edit_3": f"{old_residue}{position + 1}{new_residue}",
                        "sequence": child,
                        "sequence_sha256": digest,
                        "candidate_id": f"rescue3m-{digest[:20]}",
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
    with args.round2_scores.open(encoding="utf-8-sig", newline="") as stream:
        round2_rows = [
            row for row in csv.DictReader(stream) if row["branch_key"] not in EXCLUDED_BRANCHES
        ]
    if not round2_rows:
        raise ValueError("no non-AceA round-2 rows found")
    beam = _select_beam(round2_rows, args.beam_size)
    represented = {row["branch_key"] for row in beam}
    expected = {row["branch_key"] for row in round2_rows}
    if represented != expected:
        raise ValueError("round-3 beam lost one or more target branches")
    historical_sha256s = asyncio.run(_historical_sequence_sha256s())
    generated = _generate(beam, historical_sha256s)
    if not generated:
        raise ValueError("no novel strict multibranch round-3 variants generated")

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
                "run_id": f"autoresearch-multibranch-rescue-round3-{plugin_name}",
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
            raise RuntimeError(f"round-3 search plugin unavailable: {plugin_name}")
        for candidate_id, values in _metric_values(result).items():
            wide[candidate_id].update(values)
    rows = list(wide.values())
    for row in rows:
        row["safety_hard_gate_pass"] = str(
            _is_non_toxin(row.get("toxinpred3_label"))
            and _is_low_hemolysis(row.get("macrel_hemolysis_label"))
        ).lower()
    primary_safe = [row for row in rows if row["safety_hard_gate_pass"] == "true"]
    rows.sort(
        key=lambda row: (
            row["branch_key"],
            row["safety_hard_gate_pass"] != "true",
            -float(row.get("macrel_amp_probability", 0.0)),
            float(row.get("macrel_hemolysis_probability", 1.0)),
            float(row.get("toxinpred3_hybrid_score", 1.0)),
            row["sequence"],
        )
    )
    _write_csv(output_dir / "all_primary_scores.csv", rows)
    if not primary_safe:
        raise ValueError("multibranch round-3 search produced no primary-safety survivors")
    _write_csv(output_dir / "primary_safe_for_fullscore.csv", primary_safe)

    review, challenger_hashes = _run_hemopi2(
        rows=primary_safe,
        repo_root=repo_root,
        output_dir=output_dir,
        runtime_python=args.hemopi2_runtime,
        worker=args.hemopi2_worker,
        model_root=args.hemopi2_model_root,
        calibration_path=args.hemopi2_calibration,
    )
    review.sort(
        key=lambda row: (
            row["branch_key"],
            row["challenger_conflict_status"] != "no_conflict",
            float(row["calibrated_hemolysis_probability"]),
            -float(row["macrel_amp_probability"]),
            row["sequence"],
        )
    )
    _write_csv(output_dir / "challenger_review.csv", review)
    no_conflict = [row for row in review if row["challenger_conflict_status"] == "no_conflict"]
    if no_conflict:
        _write_csv(output_dir / "challenger_no_conflict.csv", no_conflict)
    branch_summary = []
    for branch_key in sorted(expected):
        branch_rows = [row for row in rows if row["branch_key"] == branch_key]
        branch_safe = [row for row in primary_safe if row["branch_key"] == branch_key]
        branch_no_conflict = [row for row in no_conflict if row["branch_key"] == branch_key]
        branch_summary.append(
            {
                "branch_key": branch_key,
                "beam_count": sum(row["branch_key"] == branch_key for row in beam),
                "generated_novel_strict_count": len(branch_rows),
                "primary_safety_pass_count": len(branch_safe),
                "challenger_no_conflict_count": len(branch_no_conflict),
                "challenger_conflict_count": len(branch_safe) - len(branch_no_conflict),
            }
        )
    _write_csv(output_dir / "branch_summary.csv", branch_summary)
    receipt = {
        "schema_version": "ampgent.autoresearch-multibranch-rescue-round3.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "round2_scores_sha256": sha256_file(args.round2_scores),
        "historical_sequence_exclusion_count": len(historical_sha256s),
        "beam_size_per_branch": args.beam_size,
        "beam_candidate_count": len(beam),
        "generated_novel_strict_count": len(rows),
        "primary_safety_pass_count": len(primary_safe),
        "challenger_no_conflict_count": len(no_conflict),
        "challenger_conflict_count": len(review) - len(no_conflict),
        "branch_summary": branch_summary,
        "plugin_status": statuses,
        **challenger_hashes,
        "primary_safe_csv_sha256": sha256_file(output_dir / "primary_safe_for_fullscore.csv"),
        "challenger_review_csv_sha256": sha256_file(output_dir / "challenger_review.csv"),
        "challenger_is_not_a_primary_hard_gate": True,
        "missing_verified_runtimes": ["apex", "peptiverse"],
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round2-scores", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--beam-size", type=int, default=8)
    parser.add_argument("--hemopi2-runtime", type=Path, required=True)
    parser.add_argument("--hemopi2-worker", type=Path, required=True)
    parser.add_argument("--hemopi2-model-root", type=Path, required=True)
    parser.add_argument("--hemopi2-calibration", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
