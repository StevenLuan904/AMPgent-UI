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
    SEARCH_PLUGINS,
    _historical_sequence_sha256s,
    _is_low_hemolysis,
    _is_non_toxin,
    _metric_values,
    _normalize_registry_paths,
    _write_csv,
)

from pepagent.autoresearch_closed_loop import MaskedSubstitutionAction, ResidueSubstitution
from pepagent.autoresearch_planner import _hydrophobic_fraction, _sequence_prescreen
from pepagent.model_workers.sequence_metrics_cli import evaluate
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text

HYDROPHOBIC_REPLACEMENTS = "AILVFWY"
HYDROPHOBIC_RESIDUES = frozenset("AILMFWVY")
OPERATOR_RELEASE_SHA256 = sha256_json(
    {
        "operator_id": "autoresearch-macrel-endpoint-rescue-v1",
        "replacement_residues": list(HYDROPHOBIC_REPLACEMENTS),
        "parent_policy": "balanced_per_family_support2_macrel_below_parent_top_quartile",
        "quality_gate": {
            "guruprasad_instability_index": "<50",
            "maximum_hydrophobic_run": "<=2",
            "hydrophobic_fraction": "<=0.45",
            "net_charge_ph7_4": ">=3",
        },
    }
)


def _select_parents(rows: list[dict[str, str]], maximum_per_family: int) -> list[dict[str, str]]:
    eligible = [
        row
        for row in rows
        if row["display_eligible"].lower() == "true"
        and int(row["activity_model_support_count_calibrated"]) == 2
        and float(row["macrel_amp_probability__parent_benefit_percentile"]) < 0.75
    ]
    by_family: dict[str, list[dict[str, str]]] = {}
    for row in eligible:
        by_family.setdefault(row["family_key_80_80"], []).append(row)
    selected: list[dict[str, str]] = []
    for family_key in sorted(by_family):
        family_rows = sorted(
            by_family[family_key],
            key=lambda row: (
                -float(row["macrel_amp_probability"]),
                float(row["amp_read_log10_mic_um"]),
                float(row["llamp_log10_mic_um"]),
                row["sequence"],
            ),
        )
        selected.extend(family_rows[:maximum_per_family])
    if not selected:
        raise ValueError("no calibrated support-2 Macrel endpoint rescue parents")
    return selected


def _generate(
    parents: list[dict[str, str]],
    historical_sha256s: set[str],
    evidence_sha256: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    generated: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for parent in parents:
        parent_sequence = parent["sequence"]
        parent_macrel = float(parent["macrel_amp_probability"])
        for position, old_residue in enumerate(parent_sequence):
            if old_residue in HYDROPHOBIC_RESIDUES:
                continue
            for new_residue in HYDROPHOBIC_REPLACEMENTS:
                sequence = (
                    parent_sequence[:position] + new_residue + parent_sequence[position + 1 :]
                )
                digest = sha256_text(sequence)
                if digest in historical_sha256s or digest in seen:
                    continue
                instability, maximum_hydrophobic_run, net_charge = _sequence_prescreen(sequence)
                hydrophobic_fraction = _hydrophobic_fraction(sequence)
                if not (
                    instability < 50.0
                    and maximum_hydrophobic_run <= 2
                    and hydrophobic_fraction <= 0.45
                    and net_charge >= 3.0
                ):
                    continue
                seen.add(digest)
                generation = int(parent["generation"]) + 1
                action = MaskedSubstitutionAction(
                    branch_key=parent["branch_key"],
                    generation=generation,
                    seed=int(digest[:8], 16),
                    operator_id="autoresearch-macrel-endpoint-rescue-v1",
                    operator_release_sha256=OPERATOR_RELEASE_SHA256,
                    expected_improvement_metrics=("macrel_amp_probability",),
                    protected_metrics=(
                        "amp_read_log10_mic_um",
                        "guruprasad_instability_index",
                        "llamp_log10_mic_um",
                        "macrel_hemolysis_probability",
                        "maximum_hydrophobic_run",
                        "toxinpred3_hybrid_score",
                    ),
                    evidence_sha256s=(evidence_sha256,),
                    parent_candidate_id=parent["sequence_sha256"],
                    parent_sequence_sha256=parent["sequence_sha256"],
                    substitutions=(
                        ResidueSubstitution(
                            position_zero_based=position,
                            from_residue=old_residue,
                            to_residue=new_residue,
                        ),
                    ),
                )
                actions.append(action.model_dump(mode="json"))
                generated.append(
                    {
                        "branch_key": parent["branch_key"],
                        "generation": generation,
                        "action_type": action.action_type,
                        "operator_id": action.operator_id,
                        "action_sha256": action.action_sha256,
                        "parent_candidate_id": parent["sequence_sha256"],
                        "parent_sequence_sha256": parent["sequence_sha256"],
                        "parent_sequence": parent_sequence,
                        "parent_macrel_amp_probability": parent_macrel,
                        "family_key_80_80": parent["family_key_80_80"],
                        "family_representative_sequence": parent.get(
                            "family_representative_sequence", parent_sequence
                        ),
                        "new_family_relative_to_all_references": parent.get(
                            "new_family_relative_to_all_references", "false"
                        ),
                        "diversity_qualified": parent.get(
                            "diversity_qualified", "false"
                        ),
                        "edit_position_1based": position + 1,
                        "edit": f"{old_residue}{position + 1}{new_residue}",
                        "sequence": sequence,
                        "sequence_sha256": digest,
                        "candidate_id": f"activity-rescue-{digest[:20]}",
                        "guruprasad_instability_index": instability,
                        "maximum_hydrophobic_run": maximum_hydrophobic_run,
                        "hydrophobic_fraction": hydrophobic_fraction,
                        "net_charge_ph7_4": net_charge,
                        "historical_exact_replay": "false",
                        "score_all_status": "search_prefilter",
                    }
                )
    if not generated:
        raise ValueError("no novel strict Macrel endpoint rescue variants generated")
    return generated, actions


def run(args: argparse.Namespace) -> None:
    with args.parent_scores.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    parents = _select_parents(source_rows, args.maximum_parents_per_family)
    parent_score_sha256 = sha256_file(args.parent_scores)
    historical_sha256s = asyncio.run(_historical_sequence_sha256s())
    historical_source_hashes: list[str] = []
    for path in args.historical_csv:
        historical_source_hashes.append(sha256_file(path))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                sequence = row["sequence"]
                digest = row.get("sequence_sha256") or sha256_text(sequence)
                if digest != sha256_text(sequence):
                    raise ValueError("historical activity rescue sequence/hash drifted")
                historical_sha256s.add(digest)
    generated, actions = _generate(parents, historical_sha256s, parent_score_sha256)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plans_path = output_dir / "plans.json"
    plans_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-multibranch-plan.1",
                "plans": {
                    "acea": {
                        "schema_version": "ampgent.autoresearch-rule-plan.1",
                        "branch_key": "acea",
                        "generation": max(int(row["generation"]) for row in generated),
                        "operator_id": "autoresearch-macrel-endpoint-rescue-v1",
                        "actions": actions,
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    repo_root = args.repo_root.resolve()
    registry_payload = yaml.safe_load(args.registry.read_text(encoding="utf-8"))
    normalized_registry_path = output_dir / "runtime.normalized.yaml"
    normalized_registry_path.write_text(
        yaml.safe_dump(
            _normalize_registry_paths(registry_payload, repo_root),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    candidates = [{"id": row["candidate_id"], "sequence": row["sequence"]} for row in generated]
    wide = {row["candidate_id"]: dict(row) for row in generated}
    statuses = []
    metrics_dir = output_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in SEARCH_PLUGINS:
        result = evaluate(
            {
                "run_id": f"autoresearch-activity-rescue-{plugin_name}",
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
            raise RuntimeError(f"activity rescue search plugin unavailable: {plugin_name}")
        for candidate_id, values in _metric_values(result).items():
            wide[candidate_id].update(values)

    rows = list(wide.values())
    for row in rows:
        safety_pass = _is_non_toxin(row.get("toxinpred3_label")) and _is_low_hemolysis(
            row.get("macrel_hemolysis_label")
        )
        gain = float(row["macrel_amp_probability"]) - float(
            row["parent_macrel_amp_probability"]
        )
        row["macrel_amp_probability_gain"] = gain
        row["safety_hard_gate_pass"] = str(safety_pass).lower()
        row["macrel_gain_positive"] = str(gain > 0.0).lower()
        row["full_score_shortlist"] = str(safety_pass and gain >= 0.03).lower()
    rows.sort(
        key=lambda row: (
            row["full_score_shortlist"] != "true",
            -float(row["macrel_amp_probability_gain"]),
            -float(row["macrel_amp_probability"]),
            row["family_key_80_80"],
            row["sequence"],
        )
    )
    safety_rows = [row for row in rows if row["safety_hard_gate_pass"] == "true"]
    gain_rows = [
        row
        for row in safety_rows
        if row["macrel_gain_positive"] == "true"
    ]
    shortlist = [row for row in rows if row["full_score_shortlist"] == "true"]
    _write_csv(output_dir / "all_search_scores.csv", rows)
    if safety_rows:
        _write_csv(output_dir / "safety_pass_candidates.csv", safety_rows)
    if gain_rows:
        _write_csv(output_dir / "safety_gain_candidates.csv", gain_rows)
    if shortlist:
        _write_csv(output_dir / "full_score_shortlist.csv", shortlist)
    receipt = {
        "schema_version": "ampgent.autoresearch-activity-rescue-search.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "parent_score_sha256": parent_score_sha256,
        "plans_sha256": sha256_file(plans_path),
        "historical_sequence_exclusion_count": len(historical_sha256s),
        "historical_source_sha256s": historical_source_hashes,
        "parent_count": len(parents),
        "parent_family_count": len({row["family_key_80_80"] for row in parents}),
        "generated_novel_strict_count": len(rows),
        "safety_hard_gate_pass_count": len(safety_rows),
        "safety_positive_macrel_gain_count": len(gain_rows),
        "full_score_shortlist_count": len(shortlist),
        "plugin_status": statuses,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
        "all_search_scores_sha256": sha256_file(output_dir / "all_search_scores.csv"),
    }
    for name in (
        "safety_pass_candidates.csv",
        "safety_gain_candidates.csv",
        "full_score_shortlist.csv",
    ):
        path = output_dir / name
        if path.exists():
            receipt[f"{path.stem}_sha256"] = sha256_file(path)
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--historical-csv", type=Path, action="append", default=[])
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--maximum-parents-per-family", type=int, default=8)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
