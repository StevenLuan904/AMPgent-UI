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
CHARGE_PATTERN_REPLACEMENTS = "KRHAGSTNQ"
CANONICAL_REPLACEMENTS = "ACDEFGHIKLMNPQRSTVWY"
HYBRID_PAIR_OFFSETS = (-3, 3)
RESCUE_ENDPOINTS = {
    "macrel": {
        "metric": "macrel_amp_probability",
        "percentile": "macrel_amp_probability__parent_benefit_percentile",
        "plugin": "hemolysis_risk",
        "direction": "maximize",
    },
    "llamp": {
        "metric": "llamp_log10_mic_um",
        "percentile": "llamp_log10_mic_um__parent_benefit_percentile",
        "plugin": "mic_potency",
        "direction": "minimize",
    },
    "amp-read": {
        "metric": "amp_read_log10_mic_um",
        "percentile": "amp_read_log10_mic_um__parent_benefit_percentile",
        "plugin": "mic_potency_amp_read",
        "direction": "minimize",
    },
}
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
CHARGE_PATTERN_OPERATOR_RELEASE_SHA256 = sha256_json(
    {
        "operator_id": "autoresearch-macrel-charge-pattern-rescue-v1",
        "replacement_residues": list(CHARGE_PATTERN_REPLACEMENTS),
        "parent_policy": "balanced_per_family_support2_without_existing_support3",
        "quality_gate": {
            "guruprasad_instability_index": "<50",
            "maximum_hydrophobic_run": "<=2",
            "hydrophobic_fraction": "<=0.45",
            "net_charge_ph7_4": ">=3",
        },
    }
)
CANONICAL_SCAN_OPERATOR_RELEASE_SHA256 = sha256_json(
    {
        "operator_id": "autoresearch-canonical-single-scan-rescue-v1",
        "replacement_residues": list(CANONICAL_REPLACEMENTS),
        "parent_policy": "support2_endpoint_gap_exhaustive_single_substitution",
        "quality_gate": {
            "guruprasad_instability_index": "<50",
            "maximum_hydrophobic_run": "<=2",
            "hydrophobic_fraction": "<=0.45",
            "net_charge_ph7_4": ">=3",
        },
    }
)
HYBRID_PAIR_OPERATOR_RELEASE_SHA256 = sha256_json(
    {
        "operator_id": "autoresearch-macrel-hybrid-pair-rescue-v1",
        "primary_replacement_residues": list(HYDROPHOBIC_REPLACEMENTS),
        "secondary_replacement_residues": list(CHARGE_PATTERN_REPLACEMENTS),
        "secondary_position_offsets": list(HYBRID_PAIR_OFFSETS),
        "parent_policy": "support2_macrel_gap_local_hydrophobic_charge_pair",
        "quality_gate": {
            "guruprasad_instability_index": "<50",
            "maximum_hydrophobic_run": "<=2",
            "hydrophobic_fraction": "<=0.45",
            "net_charge_ph7_4": ">=3",
        },
    }
)


def _single_branch_key(rows: list[dict[str, str]]) -> str:
    branch_keys = {row["branch_key"] for row in rows}
    if len(branch_keys) != 1:
        raise ValueError("activity rescue requires exactly one source branch")
    return branch_keys.pop()


def _validated_sequence_sha256s(rows: list[dict[str, str]]) -> set[str]:
    hashes: set[str] = set()
    for row in rows:
        sequence = row["sequence"]
        digest = row.get("sequence_sha256") or sha256_text(sequence)
        if digest != sha256_text(sequence):
            raise ValueError("activity rescue input sequence/hash drifted")
        hashes.add(digest)
    return hashes


def _filter_source_branch(
    rows: list[dict[str, str]], branch_key: str | None
) -> list[dict[str, str]]:
    if branch_key is None:
        return rows
    filtered = [row for row in rows if row["branch_key"] == branch_key]
    if not filtered:
        raise ValueError(f"source cohort has no rows for branch {branch_key}")
    return filtered


def _filter_source_families(
    rows: list[dict[str, str]], family_keys: tuple[str, ...]
) -> list[dict[str, str]]:
    if not family_keys:
        return rows
    requested = set(family_keys)
    filtered = [row for row in rows if row["family_key_80_80"] in requested]
    missing = sorted(requested - {row["family_key_80_80"] for row in filtered})
    if missing:
        raise ValueError(f"source cohort has no rows for families: {', '.join(missing)}")
    return filtered


def _select_parents(
    rows: list[dict[str, str]],
    maximum_per_family: int,
    *,
    exclude_families_with_support3: bool = False,
    rescue_endpoint: str = "macrel",
) -> list[dict[str, str]]:
    endpoint = RESCUE_ENDPOINTS[rescue_endpoint]
    full_support_families = (
        {
            row["family_key_80_80"]
            for row in rows
            if int(row["activity_model_support_count_calibrated"]) == 3
        }
        if exclude_families_with_support3
        else set()
    )
    eligible = [
        row
        for row in rows
        if row["display_eligible"].lower() == "true"
        and int(row["activity_model_support_count_calibrated"]) == 2
        and float(row[endpoint["percentile"]]) < 0.75
        and row["family_key_80_80"] not in full_support_families
    ]
    by_family: dict[str, list[dict[str, str]]] = {}
    for row in eligible:
        by_family.setdefault(row["family_key_80_80"], []).append(row)
    selected: list[dict[str, str]] = []
    for family_key in sorted(by_family):
        reverse_sign = -1.0 if endpoint["direction"] == "maximize" else 1.0
        family_rows = sorted(
            by_family[family_key],
            key=lambda row: (reverse_sign * float(row[endpoint["metric"]]), row["sequence"]),
        )
        selected.extend(family_rows[:maximum_per_family])
    if not selected:
        raise ValueError(f"no calibrated support-2 {rescue_endpoint} endpoint rescue parents")
    return selected


def _generate(
    parents: list[dict[str, str]],
    historical_sha256s: set[str],
    evidence_sha256: str,
    *,
    operator_mode: str = "hydrophobic",
    generation_floor: int | None = None,
    rescue_endpoint: str = "macrel",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    endpoint = RESCUE_ENDPOINTS[rescue_endpoint]
    generated: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_variant(
        *,
        parent: dict[str, str],
        parent_metric_value: float,
        sequence: str,
        substitutions: tuple[ResidueSubstitution, ...],
        operator_id: str,
        operator_release_sha256: str,
    ) -> None:
        substitutions = tuple(
            sorted(substitutions, key=lambda item: item.position_zero_based)
        )
        digest = sha256_text(sequence)
        if digest in historical_sha256s or digest in seen:
            return
        instability, maximum_hydrophobic_run, net_charge = _sequence_prescreen(sequence)
        hydrophobic_fraction = _hydrophobic_fraction(sequence)
        if not (
            instability < 50.0
            and maximum_hydrophobic_run <= 2
            and hydrophobic_fraction <= 0.45
            and net_charge >= 3.0
        ):
            return
        seen.add(digest)
        generation = max(
            int(parent["generation"]) + 1,
            generation_floor or 0,
        )
        action = MaskedSubstitutionAction(
            branch_key=parent["branch_key"],
            generation=generation,
            seed=int(digest[:8], 16),
            operator_id=operator_id,
            operator_release_sha256=operator_release_sha256,
            expected_improvement_metrics=(endpoint["metric"],),
            protected_metrics=tuple(
                metric
                for metric in (
                    "amp_read_log10_mic_um",
                    "guruprasad_instability_index",
                    "llamp_log10_mic_um",
                    "macrel_amp_probability",
                    "macrel_hemolysis_probability",
                    "maximum_hydrophobic_run",
                    "toxinpred3_hybrid_score",
                )
                if metric != endpoint["metric"]
            ),
            evidence_sha256s=(evidence_sha256,),
            parent_candidate_id=parent["sequence_sha256"],
            parent_sequence_sha256=parent["sequence_sha256"],
            substitutions=substitutions,
        )
        actions.append(action.model_dump(mode="json"))
        edit_positions = ",".join(
            str(substitution.position_zero_based + 1) for substitution in substitutions
        )
        edits = ";".join(
            f"{substitution.from_residue}{substitution.position_zero_based + 1}"
            f"{substitution.to_residue}"
            for substitution in substitutions
        )
        generated.append(
            {
                "branch_key": parent["branch_key"],
                "generation": generation,
                "action_type": action.action_type,
                "operator_id": action.operator_id,
                "action_sha256": action.action_sha256,
                "parent_candidate_id": parent["sequence_sha256"],
                "parent_sequence_sha256": parent["sequence_sha256"],
                "parent_sequence": parent["sequence"],
                "rescue_endpoint": rescue_endpoint,
                "rescue_metric": endpoint["metric"],
                "parent_rescue_metric_value": parent_metric_value,
                "family_key_80_80": parent["family_key_80_80"],
                "family_representative_sequence": parent.get(
                    "family_representative_sequence", parent["sequence"]
                ),
                "new_family_relative_to_all_references": parent.get(
                    "new_family_relative_to_all_references", "false"
                ),
                "diversity_qualified": parent.get("diversity_qualified", "false"),
                "edit_position_1based": edit_positions,
                "edit": edits,
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

    for parent in parents:
        parent_sequence = parent["sequence"]
        parent_metric_value = float(parent[endpoint["metric"]])
        if operator_mode == "hybrid-pair":
            operator_id = f"autoresearch-{rescue_endpoint}-hybrid-pair-rescue-v1"
            operator_release_sha256 = (
                HYBRID_PAIR_OPERATOR_RELEASE_SHA256
                if rescue_endpoint == "macrel"
                else sha256_json(
                    {
                        "operator_id": operator_id,
                        "primary_replacement_residues": list(HYDROPHOBIC_REPLACEMENTS),
                        "secondary_replacement_residues": list(
                            CHARGE_PATTERN_REPLACEMENTS
                        ),
                        "secondary_position_offsets": list(HYBRID_PAIR_OFFSETS),
                        "rescue_metric": endpoint["metric"],
                        "rescue_direction": endpoint["direction"],
                        "quality_gate": "strict-display-prefilter-v1",
                    }
                )
            )
            for primary_position, primary_old in enumerate(parent_sequence):
                if primary_old in HYDROPHOBIC_RESIDUES:
                    continue
                for primary_new in HYDROPHOBIC_REPLACEMENTS:
                    for offset in HYBRID_PAIR_OFFSETS:
                        secondary_position = primary_position + offset
                        if not 0 <= secondary_position < len(parent_sequence):
                            continue
                        secondary_old = parent_sequence[secondary_position]
                        if secondary_old in HYDROPHOBIC_RESIDUES:
                            continue
                        for secondary_new in CHARGE_PATTERN_REPLACEMENTS:
                            if secondary_new == secondary_old:
                                continue
                            sequence_chars = list(parent_sequence)
                            sequence_chars[primary_position] = primary_new
                            sequence_chars[secondary_position] = secondary_new
                            append_variant(
                                parent=parent,
                                parent_metric_value=parent_metric_value,
                                sequence="".join(sequence_chars),
                                substitutions=(
                                    ResidueSubstitution(
                                        position_zero_based=primary_position,
                                        from_residue=primary_old,
                                        to_residue=primary_new,
                                    ),
                                    ResidueSubstitution(
                                        position_zero_based=secondary_position,
                                        from_residue=secondary_old,
                                        to_residue=secondary_new,
                                    ),
                                ),
                                operator_id=operator_id,
                                operator_release_sha256=operator_release_sha256,
                            )
            continue
        for position, old_residue in enumerate(parent_sequence):
            if operator_mode == "hydrophobic" and old_residue in HYDROPHOBIC_RESIDUES:
                continue
            if operator_mode == "hydrophobic":
                replacements = HYDROPHOBIC_REPLACEMENTS
            elif operator_mode == "charge-pattern":
                replacements = CHARGE_PATTERN_REPLACEMENTS
            elif operator_mode == "canonical-scan":
                replacements = CANONICAL_REPLACEMENTS
            else:
                raise ValueError(f"unknown activity rescue operator mode: {operator_mode}")
            if rescue_endpoint == "macrel" and operator_mode == "hydrophobic":
                operator_id = "autoresearch-macrel-endpoint-rescue-v1"
                operator_release_sha256 = OPERATOR_RELEASE_SHA256
            elif rescue_endpoint == "macrel" and operator_mode == "charge-pattern":
                operator_id = "autoresearch-macrel-charge-pattern-rescue-v1"
                operator_release_sha256 = CHARGE_PATTERN_OPERATOR_RELEASE_SHA256
            elif operator_mode == "canonical-scan":
                operator_id = f"autoresearch-{rescue_endpoint}-canonical-single-scan-v1"
                operator_release_sha256 = CANONICAL_SCAN_OPERATOR_RELEASE_SHA256
            else:
                operator_id = f"autoresearch-{rescue_endpoint}-{operator_mode}-rescue-v1"
                operator_release_sha256 = sha256_json(
                    {
                        "operator_id": operator_id,
                        "replacement_residues": list(replacements),
                        "rescue_metric": endpoint["metric"],
                        "rescue_direction": endpoint["direction"],
                        "quality_gate": "strict-display-prefilter-v1",
                    }
                )
            for new_residue in replacements:
                if new_residue == old_residue:
                    continue
                sequence = (
                    parent_sequence[:position] + new_residue + parent_sequence[position + 1 :]
                )
                append_variant(
                    parent=parent,
                    parent_metric_value=parent_metric_value,
                    sequence=sequence,
                    substitutions=(
                        ResidueSubstitution(
                            position_zero_based=position,
                            from_residue=old_residue,
                            to_residue=new_residue,
                        ),
                    ),
                    operator_id=operator_id,
                    operator_release_sha256=operator_release_sha256,
                )
    if not generated:
        raise ValueError(f"no novel strict {rescue_endpoint} endpoint rescue variants generated")
    return generated, actions


def run(args: argparse.Namespace) -> None:
    source_rows: list[dict[str, str]] = []
    parent_score_sha256s: list[str] = []
    for path in args.parent_scores:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            source_rows.extend(csv.DictReader(stream))
        parent_score_sha256s.append(sha256_file(path))
    source_row_count_before_branch_filter = len(source_rows)
    input_sequence_sha256s = _validated_sequence_sha256s(source_rows)
    source_rows = _filter_source_branch(source_rows, args.branch)
    source_row_count_before_family_filter = len(source_rows)
    source_rows = _filter_source_families(source_rows, tuple(args.family_key))
    branch_key = _single_branch_key(source_rows)
    parents = _select_parents(
        source_rows,
        args.maximum_parents_per_family,
        exclude_families_with_support3=args.exclude_families_with_support3,
        rescue_endpoint=args.rescue_endpoint,
    )
    parent_score_sha256 = (
        parent_score_sha256s[0]
        if len(parent_score_sha256s) == 1
        else sha256_json({"parent_score_sha256s": parent_score_sha256s})
    )
    generation_floor = max(
        max(int(row["generation"]) for row in source_rows) + 1,
        args.generation_floor or 0,
    )
    historical_sha256s = asyncio.run(_historical_sequence_sha256s())
    historical_sha256s.update(input_sequence_sha256s)
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
    generated, actions = _generate(
        parents,
        historical_sha256s,
        parent_score_sha256,
        operator_mode=args.operator_mode,
        rescue_endpoint=args.rescue_endpoint,
        generation_floor=generation_floor,
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plans_path = output_dir / "plans.json"
    plans_path.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch-multibranch-plan.1",
                "plans": {
                    branch_key: {
                        "schema_version": "ampgent.autoresearch-rule-plan.1",
                        "branch_key": branch_key,
                        "generation": max(int(row["generation"]) for row in generated),
                        "operator_id": actions[0]["operator_id"],
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
    endpoint = RESCUE_ENDPOINTS[args.rescue_endpoint]
    search_plugins = tuple(dict.fromkeys((*SEARCH_PLUGINS, endpoint["plugin"])))
    for plugin_name in search_plugins:
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
        child_value = float(row[endpoint["metric"]])
        parent_value = float(row["parent_rescue_metric_value"])
        gain = (
            child_value - parent_value
            if endpoint["direction"] == "maximize"
            else parent_value - child_value
        )
        row["rescue_metric_improvement"] = gain
        row["safety_hard_gate_pass"] = str(safety_pass).lower()
        row["rescue_gain_positive"] = str(gain > 0.0).lower()
        row["full_score_shortlist"] = str(safety_pass and gain >= 0.03).lower()
    rows.sort(
        key=lambda row: (
            row["full_score_shortlist"] != "true",
            -float(row["rescue_metric_improvement"]),
            row["family_key_80_80"],
            row["sequence"],
        )
    )
    safety_rows = [row for row in rows if row["safety_hard_gate_pass"] == "true"]
    gain_rows = [row for row in safety_rows if row["rescue_gain_positive"] == "true"]
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
        "parent_score_sha256s": parent_score_sha256s,
        "source_branch_filter": args.branch,
        "source_row_count_before_branch_filter": source_row_count_before_branch_filter,
        "source_row_count_before_family_filter": source_row_count_before_family_filter,
        "source_row_count": len(source_rows),
        "source_family_filters": sorted(args.family_key),
        "operator_mode": args.operator_mode,
        "rescue_endpoint": args.rescue_endpoint,
        "rescue_metric": endpoint["metric"],
        "rescue_direction": endpoint["direction"],
        "generation_floor": generation_floor,
        "plans_sha256": sha256_file(plans_path),
        "historical_sequence_exclusion_count": len(historical_sha256s),
        "input_sequence_exclusion_count": len(input_sequence_sha256s),
        "historical_source_sha256s": historical_source_hashes,
        "parent_count": len(parents),
        "parent_family_count": len({row["family_key_80_80"] for row in parents}),
        "generated_novel_strict_count": len(rows),
        "safety_hard_gate_pass_count": len(safety_rows),
        "safety_positive_rescue_gain_count": len(gain_rows),
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
    parser.add_argument("--parent-scores", type=Path, action="append", required=True)
    parser.add_argument(
        "--branch",
        choices=("acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa"),
    )
    parser.add_argument("--family-key", action="append", default=[])
    parser.add_argument("--historical-csv", type=Path, action="append", default=[])
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--maximum-parents-per-family", type=int, default=8)
    parser.add_argument("--generation-floor", type=int)
    parser.add_argument("--exclude-families-with-support3", action="store_true")
    parser.add_argument(
        "--rescue-endpoint",
        choices=tuple(RESCUE_ENDPOINTS),
        default="macrel",
    )
    parser.add_argument(
        "--operator-mode",
        choices=("hydrophobic", "charge-pattern", "hybrid-pair", "canonical-scan"),
        default="hydrophobic",
    )
    run(parser.parse_args())


if __name__ == "__main__":
    main()
