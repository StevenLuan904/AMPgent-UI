from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections.abc import Iterable
from pathlib import Path
from typing import Any

TARGETS = ("acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa")
PASSING_CHALLENGER_STATES = {
    "no_conflict",
    "none",
    "cross_model_disagreement_retained",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _truth(value: Any) -> bool:
    return str(value).strip().casefold() == "true"


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return ""


def _candidate_evidence_pass(row: dict[str, str]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if not _truth(row.get("display_eligible")):
        failures.append("display_eligible")
    if not _truth(_first(row, "formal_12_complete", "formal_metrics_complete")):
        failures.append("formal_12_complete")
    metric_count = _first(row, "formal_metric_count")
    if metric_count and int(float(metric_count)) != 12:
        failures.append("formal_metric_count")
    try:
        instability = float(row.get("guruprasad_instability_index", ""))
    except (TypeError, ValueError):
        instability = math.nan
    if not math.isfinite(instability) or instability > 50.0:
        failures.append("guruprasad_instability_index_lte_50")
    if _first(row, "toxinpred3_label").casefold() not in {
        "non-toxin",
        "non-toxic",
        "nontoxic",
    }:
        failures.append("toxinpred3_non_toxin")
    if _first(row, "macrel_hemolysis_label").casefold() != "low":
        failures.append("macrel_hemolysis_low")
    try:
        support = int(
            float(
                _first(
                    row,
                    "activity_model_support_count_calibrated",
                    "activity_model_support_count",
                )
            )
        )
    except ValueError:
        support = -1
    if support < 2:
        failures.append("activity_model_support_count_ge_2")
    replay = _first(row, "historical_exact_replay").casefold()
    if replay not in {"false", "no", "0"}:
        failures.append("historical_exact_replay_false")
    challenger = _first(row, "challenger_conflict_status").casefold()
    if challenger not in PASSING_CHALLENGER_STATES:
        failures.append("challenger_review_complete")
    if not _first(row, "family_key_80_80"):
        failures.append("family_key_80_80")
    return not failures, failures


def _load_candidate_rows(roots: Iterable[Path]) -> dict[str, dict[str, str]]:
    by_sequence: dict[str, dict[str, str]] = {}
    for root in roots:
        for path in root.rglob("inputs/candidates.csv"):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    sequence_sha = str(row.get("sequence_sha256", "")).strip().lower()
                    if len(sequence_sha) != 64:
                        continue
                    prior = by_sequence.get(sequence_sha)
                    if prior is None:
                        by_sequence[sequence_sha] = row
                    elif prior != row:
                        # Candidate identity may appear in several exact-once queues. Preserve
                        # the row with the more complete evidence, but never merge fields.
                        prior_score = sum(bool(str(value).strip()) for value in prior.values())
                        row_score = sum(bool(str(value).strip()) for value in row.values())
                        if row_score > prior_score:
                            by_sequence[sequence_sha] = row
    return by_sequence


def audit(roots: list[Path]) -> dict[str, Any]:
    evidence = _load_candidate_rows(roots)
    rows: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    ignored_non_candidate_receipts: list[dict[str, str]] = []
    seen_receipts: set[str] = set()
    for root in roots:
        for receipt_path in root.rglob("completion_receipt.json"):
            receipt_key = str(receipt_path.resolve())
            if receipt_key in seen_receipts:
                continue
            seen_receipts.add(receipt_key)
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("schema_version") != (
                    "ampgent.autoresearch-rosetta-candidate-completion.1"
                ):
                    ignored_non_candidate_receipts.append(
                        {
                            "receipt_path": str(receipt_path),
                            "schema_version": str(receipt.get("schema_version", "missing")),
                        }
                    )
                    continue
                sequence_sha = str(receipt["sequence_sha256"]).lower()
                target = str(receipt["target_key"]).casefold()
                if target not in TARGETS:
                    raise ValueError("unknown target")
                nstruct = int(receipt.get("nstruct", 0))
                if receipt.get("status") != "succeeded" or nstruct not in {20, 200}:
                    raise ValueError("receipt is not a succeeded 20/200-decoy result")
                result_path = receipt_path.parent / "results" / "rosetta_result.json"
                if not result_path.is_file() or _sha256(result_path) != receipt.get(
                    "result_sha256"
                ):
                    raise ValueError("result identity mismatch")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                decoys = result.get("decoys")
                if not isinstance(decoys, list) or len(decoys) != nstruct:
                    raise ValueError("result decoy count differs from receipt")
                top = sorted(decoys, key=lambda item: float(item["reweighted_sc"]))[:10]
                recomputed = statistics.median(float(item["dG_separated"]) for item in top)
                primary = float(receipt["primary_dG_separated_reu"])
                if not math.isclose(recomputed, primary, rel_tol=0.0, abs_tol=1e-9):
                    raise ValueError("primary aggregation mismatch")
                candidate = evidence.get(sequence_sha)
                if candidate is None:
                    raise ValueError("candidate evidence row missing")
                evidence_pass, evidence_failures = _candidate_evidence_pass(candidate)
                candidate_target = _first(candidate, "target_key", "branch_key").casefold()
                if candidate_target != target:
                    raise ValueError("candidate target identity mismatch")
                rows.append(
                    {
                        "target_key": target,
                        "candidate_id": receipt.get("candidate_id"),
                        "sequence_sha256": sequence_sha,
                        "family_key_80_80": _first(candidate, "family_key_80_80"),
                        "nstruct": nstruct,
                        "primary_dG_separated_reu": primary,
                        "evidence_pass": evidence_pass,
                        "evidence_failures": evidence_failures,
                        "rosetta_gate_pass": primary < -30.0,
                        "pool_a_eligible": evidence_pass and primary < -30.0,
                        "receipt_path": str(receipt_path),
                        "receipt_sha256": _sha256(receipt_path),
                    }
                )
            except Exception as error:  # fail closed and retain precise audit path
                invalid.append({"receipt_path": str(receipt_path), "error": str(error)})

    summary: dict[str, Any] = {}
    for target in TARGETS:
        target_rows = [row for row in rows if row["target_key"] == target]
        eligible = [row for row in target_rows if row["pool_a_eligible"]]
        target_evidence = [
            row
            for row in evidence.values()
            if _first(row, "target_key", "branch_key").casefold() == target
        ]
        qualified_evidence = [
            row for row in target_evidence if _candidate_evidence_pass(row)[0]
        ]
        qualified_evidence_sequences = {
            str(row.get("sequence_sha256", "")).strip().lower()
            for row in qualified_evidence
        }
        completed_sequences = {row["sequence_sha256"] for row in target_rows}
        by_family: dict[str, dict[str, Any]] = {}
        for row in sorted(eligible, key=lambda item: item["primary_dG_separated_reu"]):
            by_family.setdefault(row["family_key_80_80"], row)
        summary[target] = {
            "candidate_evidence_qualified_sequence_count": len(
                qualified_evidence_sequences
            ),
            "candidate_evidence_qualified_family_count": len(
                {_first(row, "family_key_80_80") for row in qualified_evidence}
            ),
            "candidate_evidence_receipt_pending_count": len(
                qualified_evidence_sequences - completed_sequences
            ),
            "valid_complete_receipt_count": len(target_rows),
            "evidence_complete_count": sum(row["evidence_pass"] for row in target_rows),
            "rosetta_dg_lt_minus_30_count": sum(row["rosetta_gate_pass"] for row in target_rows),
            "pool_a_eligible_sequence_count": len(eligible),
            "pool_a_eligible_family_count": len(by_family),
            "pool_a_top50_filled": min(50, len(by_family)),
            "pool_a_family_gap_to_50": max(0, 50 - len(by_family)),
        }
    return {
        "schema_version": "ampgent.pool-a-receipt-audit.1",
        "roots": [str(root) for root in roots],
        "candidate_evidence_sequence_count": len(evidence),
        "valid_receipt_count": len(rows),
        "invalid_receipt_count": len(invalid),
        "ignored_non_candidate_receipt_count": len(ignored_non_candidate_receipts),
        "summary": summary,
        "rows": rows,
        "invalid_receipts": invalid,
        "ignored_non_candidate_receipts": ignored_non_candidate_receipts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = audit(args.root)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
