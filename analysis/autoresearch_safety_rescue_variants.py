from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select, text

from pepagent.autoresearch_closed_loop import MaskedSubstitutionAction, ResidueSubstitution
from pepagent.autoresearch_planner import _hydrophobic_fraction, _sequence_prescreen
from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.model_workers.sequence_metrics_cli import evaluate
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text

SEARCH_PLUGINS = (
    "physicochemical_developability",
    "hemolysis_risk",
    "toxicity_risk",
)
EDITABLE = frozenset("AVILMFWYKR")
REPLACEMENTS = "AGSTNQDEH"
OPERATOR_RELEASE_SHA256 = sha256_json(
    {
        "operator_id": "autoresearch-safety-rescue-substitution-v2",
        "editable_residues": sorted(EDITABLE),
        "replacement_residues": list(REPLACEMENTS),
        "quality_gate": {
            "guruprasad_instability_index": "<50",
            "maximum_hydrophobic_run": "<=2",
            "hydrophobic_fraction": "<=0.45",
            "net_charge_ph7_4": ">=3",
        },
    }
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
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


async def _historical_sequence_sha256s() -> set[str]:
    async with SessionFactory() as session:
        candidate_hashes = set(
            await session.scalars(select(Candidate.sequence_sha256).distinct())
        )
        operational_hash_rows = list(
            await session.scalars(
                text(
                    """
                    SELECT DISTINCT candidate ->> 'sequence_sha256'
                    FROM lifecycle_events AS event
                    CROSS JOIN LATERAL jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(
                                event.payload_json -> 'output' -> 'candidates'
                            ) = 'array'
                            THEN event.payload_json -> 'output' -> 'candidates'
                            ELSE '[]'::jsonb
                        END
                    ) AS candidate
                    WHERE event.event_type = 'operational.call.succeeded'
                      AND event.payload_json ->> 'purpose' = 'score_all'
                      AND event.payload_json ->> 'status' = 'succeeded'
                    """
                )
            )
        )
    if any(not isinstance(item, str) for item in operational_hash_rows):
        raise ValueError("PostgreSQL operational score history is malformed")
    operational_hashes = set(operational_hash_rows)
    combined = candidate_hashes | operational_hashes
    if any(len(item) != 64 or set(item) - set("0123456789abcdef") for item in combined):
        raise ValueError("PostgreSQL rescue history contains an invalid sequence SHA-256")
    return combined


def _is_non_toxin(value: Any) -> bool:
    return str(value).strip().lower().replace("_", "-") == "non-toxin"


def _is_low_hemolysis(value: Any) -> bool:
    return str(value).strip().lower() == "low"


def _select_unsafe_parents(
    rows: list[dict[str, str]],
    *,
    family_keys: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    selected = []
    family_filter = set(family_keys)
    for row in rows:
        support = int(
            row.get("activity_model_support_count_calibrated")
            or row["activity_model_support_count"]
        )
        if (
            support >= 2
            and row["display_eligible"].lower() == "false"
            and (
                not family_filter
                or (row.get("source_family_key_80_80") or row.get("family_key_80_80"))
                in family_filter
            )
        ):
            selected.append(row)
    return selected


def _single_branch_key(rows: list[dict[str, str]]) -> str:
    branch_keys = {row["branch_key"] for row in rows}
    if len(branch_keys) != 1:
        raise ValueError("safety rescue requires exactly one source branch")
    return branch_keys.pop()


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
        for position, old_residue in enumerate(parent_sequence):
            if old_residue not in EDITABLE:
                continue
            for new_residue in REPLACEMENTS:
                if new_residue == old_residue:
                    continue
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
                generation = int(parent.get("generation") or 0) + 1
                action = MaskedSubstitutionAction(
                    action_type="masked_substitution",
                    branch_key=parent["branch_key"],
                    generation=generation,
                    seed=int(digest[:8], 16),
                    operator_id="autoresearch-safety-rescue-substitution-v2",
                    operator_release_sha256=OPERATOR_RELEASE_SHA256,
                    expected_improvement_metrics=(
                        "macrel_hemolysis_probability",
                        "toxinpred3_hybrid_score",
                    ),
                    protected_metrics=(
                        "amp_read_log10_mic_um",
                        "llamp_log10_mic_um",
                        "macrel_amp_probability",
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
                action_payload = action.model_dump(mode="json")
                actions.append(action_payload)
                generated.append(
                    {
                        "branch_key": parent["branch_key"],
                        "generation": generation,
                        "action_type": action.action_type,
                        "operator_id": "autoresearch-safety-rescue-substitution-v2",
                        "action_sha256": action.action_sha256,
                        "parent_candidate_id": parent["sequence_sha256"],
                        "parent_sequence_sha256": parent["sequence_sha256"],
                        "parent_sequence": parent_sequence,
                        "family_key_80_80": parent["family_key_80_80"],
                        "family_representative_sequence": parent.get(
                            "family_representative_sequence", parent_sequence
                        ),
                        "new_family_relative_to_all_references": "false",
                        "diversity_qualified": "false",
                        "edit_position_1based": position + 1,
                        "edit": f"{old_residue}{position + 1}{new_residue}",
                        "sequence": sequence,
                        "sequence_sha256": digest,
                        "candidate_id": f"rescue-{digest[:20]}",
                        "guruprasad_instability_index": instability,
                        "maximum_hydrophobic_run": maximum_hydrophobic_run,
                        "hydrophobic_fraction": hydrophobic_fraction,
                        "net_charge_ph7_4": net_charge,
                        "historical_exact_replay": "false",
                        "score_all_status": "search_prefilter",
                    }
                )
    return generated, actions


def run(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with args.parent_scores.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    family_keys = tuple(args.family_key or ())
    parents = _select_unsafe_parents(source_rows, family_keys=family_keys)
    if not parents:
        raise ValueError("no active unsafe parents found")
    historical_sha256s = asyncio.run(_historical_sequence_sha256s())
    historical_source_hashes: list[str] = []
    for path in args.historical_csv:
        historical_source_hashes.append(sha256_file(path))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                sequence = row["sequence"]
                digest = row.get("sequence_sha256") or sha256_text(sequence)
                if digest != sha256_text(sequence):
                    raise ValueError("historical rescue exclusion sequence/hash drifted")
                historical_sha256s.add(digest)
    parent_score_sha256 = sha256_file(args.parent_scores)
    generated, actions = _generate(parents, historical_sha256s, parent_score_sha256)
    if not generated:
        raise ValueError("no novel strict rescue variants generated")
    branch_key = _single_branch_key(parents)
    plans_path = output_dir / "plans.json"
    plans_payload = {
        "schema_version": "ampgent.autoresearch-multibranch-plan.1",
        "plans": {
            branch_key: {
                "schema_version": "ampgent.autoresearch-rule-plan.1",
                "branch_key": branch_key,
                "generation": max(int(row["generation"]) for row in generated),
                "operator_id": "autoresearch-safety-rescue-substitution-v2",
                "actions": actions,
            }
        },
    }
    plans_path.write_text(
        json.dumps(plans_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

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
                "run_id": f"autoresearch-safety-rescue-{plugin_name}",
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
    safety_pass_rows = [row for row in rows if row["safety_hard_gate_pass"] == "true"]
    _write_csv(output_dir / "all_search_scores.csv", rows)
    if safety_pass_rows:
        _write_csv(output_dir / "safety_pass_candidates.csv", safety_pass_rows)
    if shortlist:
        _write_csv(output_dir / "full_score_shortlist.csv", shortlist)
    receipt = {
        "schema_version": "ampgent.autoresearch-safety-rescue-search.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "parent_score_sha256": parent_score_sha256,
        "plans_sha256": sha256_file(plans_path),
        "historical_sequence_exclusion_count": len(historical_sha256s),
        "historical_source_sha256s": historical_source_hashes,
        "parent_count": len(parents),
        "source_family_filter": list(family_keys),
        "generated_novel_strict_count": len(rows),
        "safety_hard_gate_pass_count": len(safety_pass_rows),
        "full_score_shortlist_count": len(shortlist),
        "plugin_status": statuses,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
        "all_search_scores_sha256": sha256_file(output_dir / "all_search_scores.csv"),
    }
    if safety_pass_rows:
        receipt["safety_pass_candidates_sha256"] = sha256_file(
            output_dir / "safety_pass_candidates.csv"
        )
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
    parser.add_argument("--family-key", action="append")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
