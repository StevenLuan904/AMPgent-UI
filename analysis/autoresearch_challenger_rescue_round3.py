from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import subprocess
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

from pepagent.autoresearch_challenger_review import (
    HEMOPI2_CLASSIFIER_SHA256,
    HEMOPI2_REGRESSOR_SHA256,
)
from pepagent.autoresearch_planner import _hydrophobic_fraction, _sequence_prescreen
from pepagent.hemopi2_v27_worker import REQUIRED_ENVIRONMENT
from pepagent.model_workers.sequence_metrics_cli import evaluate
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _calibrated_probability(raw_score: float, coefficient: float, intercept: float) -> float:
    clipped = min(max(raw_score, 1e-12), 1.0 - 1e-12)
    calibrated_logit = coefficient * math.log(clipped / (1.0 - clipped)) + intercept
    if calibrated_logit >= 0:
        return 1.0 / (1.0 + math.exp(-calibrated_logit))
    exp_value = math.exp(calibrated_logit)
    return exp_value / (1.0 + exp_value)


def _generate(
    parents: list[dict[str, str]],
    historical_sha256s: set[str],
    *,
    generation: int,
    evidence_sha256: str,
    operator_release_sha256: str,
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
                parent_sha256 = parent["sequence_sha256"]
                action_seed = int(
                    sha256_json(
                        {
                            "generation": generation,
                            "parent_sequence_sha256": parent_sha256,
                            "position_zero_based": position,
                            "from_residue": old_residue,
                            "to_residue": new_residue,
                        }
                    )[:8],
                    16,
                )
                action = {
                    "schema_version": "ampgent.autoresearch-action.1",
                    "action_type": "masked_substitution",
                    "branch_key": parent["branch_key"],
                    "generation": generation,
                    "seed": action_seed,
                    "operator_id": "autoresearch-hemopi2-rescue-substitution-v2",
                    "operator_release_sha256": operator_release_sha256,
                    "expected_improvement_metrics": [
                        "challenger_hemopi2_hemolysis_risk"
                    ],
                    "protected_metrics": [
                        "amp_read_log10_mic_um",
                        "guruprasad_instability_index",
                        "llamp_log10_mic_um",
                        "macrel_amp_probability",
                        "macrel_hemolysis_probability",
                        "maximum_hydrophobic_run",
                        "toxinpred3_hybrid_score",
                    ],
                    "evidence_sha256s": [evidence_sha256],
                    "parent_candidate_id": parent_sha256,
                    "parent_sequence_sha256": parent_sha256,
                    "substitutions": [
                        {
                            "position_zero_based": position,
                            "from_residue": old_residue,
                            "to_residue": new_residue,
                        }
                    ],
                }
                action["action_sha256"] = sha256_json(action)
                seen.add(digest)
                actions.append(action)
                generated.append(
                    {
                        "branch_key": parent["branch_key"],
                        "generation": generation,
                        "action_type": "masked_substitution",
                        "operator_id": action["operator_id"],
                        "action_seed": action_seed,
                        "action_sha256": action["action_sha256"],
                        "parent_candidate_id": parent_sha256,
                        "parent_sequence_sha256": parent_sha256,
                        "parent_sequence": parent_sequence,
                        "edit_position_1based": position + 1,
                        "edit": f"{old_residue}{position + 1}{new_residue}",
                        "sequence": sequence,
                        "sequence_sha256": digest,
                        "candidate_id": f"rescue3-{digest[:20]}",
                        "guruprasad_instability_index": instability,
                        "maximum_hydrophobic_run": maximum_hydrophobic_run,
                        "hydrophobic_fraction": hydrophobic_fraction,
                        "net_charge_ph7_4": net_charge,
                        "historical_exact_replay": "false",
                        "score_all_status": "search_prefilter",
                    }
                )
    return generated, actions


def _run_hemopi2(
    *,
    rows: list[dict[str, Any]],
    repo_root: Path,
    output_dir: Path,
    runtime_python: Path,
    worker: Path,
    model_root: Path,
    calibration_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if sha256_file(model_root / "hemopi2_ml_clf.sav") != HEMOPI2_CLASSIFIER_SHA256:
        raise ValueError("HemoPI2 classifier identity drifted")
    if sha256_file(model_root / "HemoPI2_reg.sav") != HEMOPI2_REGRESSOR_SHA256:
        raise ValueError("HemoPI2 regressor identity drifted")
    input_payload = {
        "schema_version": "ampgent.autoresearch-challenger-worker-input.1",
        "candidates": [
            {
                "candidate_id": row["candidate_id"],
                "sequence": row["sequence"],
                "sequence_sha256": row["sequence_sha256"],
                "target_key": row["branch_key"],
            }
            for row in rows
        ],
    }
    input_bytes = _canonical_json_bytes(input_payload)
    input_path = output_dir / "hemopi2_challenger_input.json"
    input_path.write_bytes(input_bytes)
    input_sha256 = hashlib.sha256(input_bytes).hexdigest()
    environment = os.environ.copy()
    environment.update(REQUIRED_ENVIRONMENT)
    environment["PYTHONPATH"] = str((repo_root / "src").resolve())
    completed = subprocess.run(
        [
            str(runtime_python.resolve()),
            str(worker.resolve()),
            "--input",
            str(input_path.resolve()),
            "--input-sha256",
            input_sha256,
            "--model-root",
            str(model_root.resolve()),
        ],
        cwd=repo_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError(
            f"HemoPI2 worker failed: {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    raw_path = output_dir / "hemopi2_challenger_raw.json"
    raw_path.write_bytes(completed.stdout)
    payload = json.loads(completed.stdout.decode("utf-8"))
    records = payload["records"]
    if len(records) != len(rows):
        raise ValueError("HemoPI2 challenger coverage drifted")
    calibration = json.loads(calibration_path.read_text(encoding="utf-8-sig"))
    coefficient = float(calibration["calibrator"]["coefficient"])
    intercept = float(calibration["calibrator"]["intercept"])
    threshold = float(calibration["threshold_policy"]["calibrated_probability_threshold"])
    review: list[dict[str, Any]] = []
    for expected, record in zip(rows, records, strict=True):
        if any(
            record[key] != expected[source_key]
            for key, source_key in (
                ("candidate_id", "candidate_id"),
                ("sequence", "sequence"),
                ("sequence_sha256", "sequence_sha256"),
                ("target_key", "branch_key"),
            )
        ):
            raise ValueError("HemoPI2 challenger identity drifted")
        probability = _calibrated_probability(
            float(record["hemopi2_classification_score"]), coefficient, intercept
        )
        raw_risk = int(record["hemopi2_classification_label"]) == 1
        calibrated_risk = probability >= threshold
        hc50_risk = float(record["hemopi2_hc50_um"]) < 100.0
        conflict = raw_risk or calibrated_risk or hc50_risk
        review.append(
            {
                **expected,
                **record,
                "calibrated_hemolysis_probability": probability,
                "calibration_risk_threshold": threshold,
                "calibration_threshold_exceeded": calibrated_risk,
                "reported_hc50_below_100_um": hc50_risk,
                "challenger_conflict_status": (
                    "cross_model_disagreement_retained" if conflict else "no_conflict"
                ),
                "candidate_hard_gate_allowed": False,
                "missing_verified_runtimes": "apex;peptiverse",
            }
        )
    return review, {
        "worker_input_sha256": input_sha256,
        "worker_output_sha256": sha256_file(raw_path),
        "calibration_sha256": sha256_file(calibration_path),
    }


def run(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with args.parent_scores.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    parents = [
        row for row in source_rows if row["excellent_sequence_stage_calibrated"].lower() == "true"
    ]
    if args.conflict_only:
        parents = [
            row
            for row in parents
            if row.get("challenger_conflict_status")
            == "cross_model_disagreement_retained"
        ]
    if not parents:
        raise ValueError("no calibrated excellent parents found")
    historical_source_sha256s: list[str] = []
    if args.history_mode == "postgresql":
        historical_sha256s = asyncio.run(_historical_sequence_sha256s())
        history_check_status = "postgresql_complete"
    else:
        if not args.historical_csv:
            raise ValueError("provided_csv_only history mode requires --historical-csv")
        historical_sha256s: set[str] = set()
        for path in args.historical_csv:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    digest = row.get("sequence_sha256")
                    if not digest and row.get("sequence"):
                        digest = sha256_text(row["sequence"])
                    if digest:
                        historical_sha256s.add(digest)
            historical_source_sha256s.append(sha256_file(path))
        history_check_status = "deferred_to_postgresql_materialization_gate"
    generation = max(int(row.get("generation") or 0) for row in parents) + 1
    parent_scores_sha256 = sha256_file(args.parent_scores)
    operator_release_sha256 = sha256_file(Path(__file__).resolve())
    generated, actions = _generate(
        parents,
        historical_sha256s,
        generation=generation,
        evidence_sha256=parent_scores_sha256,
        operator_release_sha256=operator_release_sha256,
    )
    if args.history_mode == "provided_csv_only":
        for row in generated:
            row["historical_exact_replay"] = "unchecked"
    if not generated:
        raise ValueError("no novel strict round-3 variants generated")
    plan_payload = {
        "schema_version": "ampgent.autoresearch-multibranch-plan.1",
        "plans": {
            branch_key: {
                "schema_version": "ampgent.autoresearch-rule-plan.1",
                "branch_key": branch_key,
                "generation": generation,
                "operator_id": "autoresearch-hemopi2-rescue-substitution-v2",
                "actions": [
                    action for action in actions if action["branch_key"] == branch_key
                ],
            }
            for branch_key in sorted({row["branch_key"] for row in generated})
        },
    }
    plans_path = output_dir / "plans.json"
    plans_path.write_text(
        json.dumps(plan_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
                "run_id": f"autoresearch-challenger-rescue-round3-{plugin_name}",
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
            row["safety_hard_gate_pass"] != "true",
            -float(row.get("macrel_amp_probability", 0.0)),
            float(row.get("macrel_hemolysis_probability", 1.0)),
            float(row.get("toxinpred3_hybrid_score", 1.0)),
            row["sequence"],
        )
    )
    _write_csv(output_dir / "all_primary_scores.csv", rows)
    if not primary_safe:
        raise ValueError("round-3 search produced no primary-safety survivors")
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
    receipt = {
        "schema_version": "ampgent.autoresearch-challenger-rescue-round3.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "parent_scores_sha256": parent_scores_sha256,
        "generation": generation,
        "operator_release_sha256": operator_release_sha256,
        "plans_sha256": sha256_file(plans_path),
        "history_mode": args.history_mode,
        "history_check_status": history_check_status,
        "historical_source_sha256s": historical_source_sha256s,
        "display_or_promotion_allowed": args.history_mode == "postgresql",
        "conflict_only": args.conflict_only,
        "historical_sequence_exclusion_count": len(historical_sha256s),
        "parent_count": len(parents),
        "generated_novel_strict_count": len(rows),
        "primary_safety_pass_count": len(primary_safe),
        "challenger_no_conflict_count": len(no_conflict),
        "challenger_conflict_count": len(review) - len(no_conflict),
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
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--hemopi2-runtime", type=Path, required=True)
    parser.add_argument("--hemopi2-worker", type=Path, required=True)
    parser.add_argument("--hemopi2-model-root", type=Path, required=True)
    parser.add_argument("--hemopi2-calibration", type=Path, required=True)
    parser.add_argument(
        "--history-mode",
        choices=("postgresql", "provided_csv_only"),
        default="postgresql",
    )
    parser.add_argument("--historical-csv", type=Path, action="append", default=[])
    parser.add_argument("--conflict-only", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
