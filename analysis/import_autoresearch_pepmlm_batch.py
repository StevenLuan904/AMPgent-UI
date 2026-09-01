from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text

CANONICAL_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
BRANCHES = frozenset(("acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa"))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write an empty PepMLM import")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def import_branch(
    *,
    output_json: Path,
    completion_receipt: Path,
    branch_key: str,
    generation: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if branch_key not in BRANCHES:
        raise ValueError(f"unsupported AMPgent branch: {branch_key}")
    if generation < 1:
        raise ValueError("generation must be positive")
    output_sha256 = sha256_file(output_json)
    completion_sha256 = sha256_file(completion_receipt)
    completion = json.loads(completion_receipt.read_text(encoding="utf-8"))
    if completion.get("schema_version") != "ampgent.autoresearch.pepmlm-gap-completion.v1":
        raise ValueError("unexpected PepMLM completion receipt schema")
    branch_receipt = (completion.get("branch_outputs") or {}).get(branch_key)
    if not isinstance(branch_receipt, dict):
        raise ValueError("completion receipt omits the requested branch")
    if branch_receipt.get("output_sha256") != output_sha256:
        raise ValueError("PepMLM branch output SHA-256 differs from completion receipt")

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("PepMLM branch output has no candidates")
    if payload.get("generated_count") != len(candidates):
        raise ValueError("PepMLM generated count differs from candidate rows")
    if branch_receipt.get("candidate_count") != len(candidates):
        raise ValueError("completion receipt candidate count drifted")

    seen_sequences: set[str] = set()
    seen_action_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for rank, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError("PepMLM candidate row is not an object")
        sequence = str(candidate.get("sequence", "")).strip().upper()
        if not 10 <= len(sequence) <= 30 or set(sequence) - CANONICAL_AA:
            raise ValueError(f"invalid AMPgent peptide at rank {rank}")
        if sequence in seen_sequences:
            raise ValueError(f"duplicate PepMLM sequence at rank {rank}")
        seen_sequences.add(sequence)
        action_id = str(candidate.get("action_id", ""))
        if not action_id.startswith(f"{branch_key}-denovo-"):
            raise ValueError(f"candidate action does not belong to {branch_key}")
        if action_id in seen_action_ids:
            raise ValueError(f"duplicate PepMLM action id: {action_id}")
        seen_action_ids.add(action_id)
        if candidate.get("action_kind") != "de_novo":
            raise ValueError("long-batch import only accepts de-novo actions")
        action_sha256 = str(candidate.get("action_sha256", ""))
        if len(action_sha256) != 64 or set(action_sha256) - set("0123456789abcdef"):
            raise ValueError("PepMLM action is missing a valid SHA-256")
        conditional_nll = float(candidate["conditional_nll"])
        conditional_ppl = float(candidate["conditional_ppl"])
        if not math.isfinite(conditional_nll) or not math.isfinite(conditional_ppl):
            raise ValueError("PepMLM target-conditional score is non-finite")
        rows.append(
            {
                "branch_key": branch_key,
                "generation": generation,
                "proposal_rank": rank,
                "operator_id": "pepmlm-target-conditioned-de-novo-v1",
                "action_id": action_id,
                "action_kind": "de_novo",
                "action_seed": int(candidate["action_seed"]),
                "action_sha256": action_sha256,
                "sequence": sequence,
                "sequence_sha256": sha256_text(sequence),
                "pepmlm_conditional_nll": f"{conditional_nll:.12g}",
                "pepmlm_conditional_ppl": f"{conditional_ppl:.12g}",
                "pepmlm_model": str(payload.get("model", "")),
                "pepmlm_revision": str(payload.get("revision", "")),
                "expected_improvement_axes_json": json.dumps(
                    candidate.get("expected_improvement_axes", []),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "protected_axes_json": json.dumps(
                    candidate.get("protected_axes", []),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "historical_exact_replay": "unchecked",
                "score_all_status": "pending",
            }
        )
    if branch_receipt.get("unique_sequence_count") != len(seen_sequences):
        raise ValueError("completion receipt unique sequence count drifted")
    receipt = {
        "schema_version": "ampgent.autoresearch-pepmlm-score-input.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "branch_key": branch_key,
        "generation": generation,
        "candidate_count": len(rows),
        "unique_sequence_count": len(seen_sequences),
        "action_count": len(seen_action_ids),
        "source_output_sha256": output_sha256,
        "source_completion_receipt_sha256": completion_sha256,
        "source_workload_sha256": completion.get("workload_sha256"),
        "history_check_status": "deferred_to_postgresql_materialization_gate",
        "all_valid_unique_sequences_require_score_all": True,
        "historical_run_modified": False,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
    }
    return rows, receipt


def run(args: argparse.Namespace) -> None:
    rows, receipt = import_branch(
        output_json=args.output_json,
        completion_receipt=args.completion_receipt,
        branch_key=args.branch,
        generation=args.generation,
    )
    _write_csv(args.output_csv, rows)
    receipt["output_csv_sha256"] = sha256_file(args.output_csv)
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--completion-receipt", type=Path, required=True)
    parser.add_argument("--branch", choices=sorted(BRANCHES), required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
