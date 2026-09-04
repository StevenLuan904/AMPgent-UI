"""Prepare and close a local FGF2 PepFlow QD-neighbor evidence batch.

The wrapper owns the cross-artifact identity contract.  It never runs remote
work: PG is consulted only for the exact materialization/readback identities
after the local evidence has been closed.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.db.models import Candidate, Evaluation, ToolCall
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text

REQUIRED_CHALLENGER_FIELDS = (
    "hemopi2_classification_score",
    "calibrated_hemolysis_probability",
    "hemopi2_hc50_um",
    "missing_verified_runtimes",
)
QD_CONTRIBUTIONS = {"empty_cell", "incumbent_replacement", "new_cell", "replacement"}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _identity(rows: list[dict[str, str]], expected: list[dict[str, str]], label: str) -> None:
    if len(rows) != len(expected):
        raise ValueError(f"{label} row count drifted")
    actual = [row.get("sequence_sha256", "").strip().lower() for row in rows]
    wanted = [row.get("sequence_sha256", "").strip().lower() for row in expected]
    if actual != wanted:
        raise ValueError(f"{label} sequence identity/order drifted")
    for row in rows:
        sequence = "".join(str(row.get("sequence") or "").split()).upper()
        if sha256_text(sequence) != row["sequence_sha256"].strip().lower():
            raise ValueError(f"{label} sequence hash drifted")


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def prepare(report_dir: Path, capacity_path: Path) -> dict[str, Any]:
    proposals_path = report_dir / "proposals.csv"
    score_path = report_dir / "score_all" / "candidate_scores.csv"
    calibrated_path = report_dir / "candidate_scores_calibrated.csv"
    challenger_path = report_dir / "challenger" / "challenger_review.csv"
    qd_path = report_dir / "provisional_qd.json"
    generation_path = report_dir / "generation_receipt.json"
    proposals = _rows(proposals_path)
    score = _rows(score_path)
    calibrated = _rows(calibrated_path)
    challenger = _rows(challenger_path)
    generation = _json(generation_path)
    qd = _json(qd_path)
    _identity(score, proposals, "score-all")
    _identity(calibrated, proposals, "calibration")
    _identity(challenger, proposals, "challenger")
    if generation.get("proposal_count") != len(proposals) or len(proposals) != 12:
        raise ValueError("FGF2 vnext proposal identity contract requires twelve rows")
    challenger_by_hash = {row["sequence_sha256"].strip().lower(): row for row in challenger}
    score_by_hash = {row["sequence_sha256"].strip().lower(): row for row in calibrated}
    contributions = {
        str(item["candidate_id"]).strip().lower(): item
        for item in qd.get("contributions", [])
        if item.get("contribution") in QD_CONTRIBUTIONS
    }
    selected: list[dict[str, str]] = []
    reasons: dict[str, int] = {
        "qd_valid": 0,
        "display_and_support": 0,
        "challenger_complete": 0,
        "selected": 0,
    }
    for digest, _qd_item in contributions.items():
        row = score_by_hash.get(digest)
        if row is None:
            raise ValueError(f"QD contribution is not in calibrated identity: {digest}")
        reasons["qd_valid"] += 1
        if (
            row.get("display_eligible", "").lower() != "true"
            or int(row.get("activity_model_support_count_calibrated", "0") or 0) < 2
        ):
            continue
        reasons["display_and_support"] += 1
        challenger_row = challenger_by_hash.get(digest)
        if challenger_row is None or any(
            not str(challenger_row.get(field) or "").strip() for field in REQUIRED_CHALLENGER_FIELDS
        ):
            continue
        reasons["challenger_complete"] += 1
        selected.append(row)
    selected.sort(
        key=lambda row: proposals.index(
            next(item for item in proposals if item["sequence_sha256"] == row["sequence_sha256"])
        )
    )
    reasons["selected"] = len(selected)
    selected_challenger = [challenger_by_hash[row["sequence_sha256"].lower()] for row in selected]
    material_dir = report_dir / "materialization_input"
    selected_score_path = material_dir / "candidate_scores.csv"
    selected_challenger_path = material_dir / "challenger_review.csv"
    _write_csv(selected_score_path, selected, list(calibrated[0]))
    _write_csv(selected_challenger_path, selected_challenger, list(challenger[0]))
    capacity = _json(capacity_path)
    receipt = {
        "schema_version": "ampgent.fgf2-pepflow-qd-neighbor-vnext-selection.1",
        "target_key": "fgf2",
        "generation": generation["generation"],
        "proposal_count": len(proposals),
        "formal12_count": sum(row.get("formal_12_complete", "").lower() == "true" for row in score),
        "display_count": sum(
            row.get("display_eligible", "").lower() == "true" for row in calibrated
        ),
        "support_ge_2_count": sum(
            int(row.get("activity_model_support_count_calibrated", "0") or 0) >= 2
            for row in calibrated
        ),
        "challenger_reviewed_count": len(challenger),
        "challenger_no_conflict_count": sum(
            row.get("challenger_conflict_status") == "no_conflict" for row in challenger
        ),
        "challenger_conflict_count": sum(
            row.get("challenger_conflict_status") == "cross_model_disagreement_retained"
            for row in challenger
        ),
        "qd_provisional": {
            "quality_eligible_count": qd.get(
                "quality_eligible_count", qd.get("eligible_batch_candidate_count")
            ),
            "new_cell_count": qd.get("new_cell_count", qd.get("diversity_gain")),
            "replacement_count": qd.get("replacement_count", qd.get("incumbent_replacement_count")),
        },
        "strict_materialization_selection": reasons,
        "materialization_candidate_count": len(selected),
        "identity_contract": {
            "proposal_sha256": sha256_file(proposals_path),
            "score_sha256": sha256_file(score_path),
            "calibrated_sha256": sha256_file(calibrated_path),
            "challenger_sha256": sha256_file(challenger_path),
            "sequence_sha256_order_preserved": True,
            "sequence_sha256_unique": len({row["sequence_sha256"] for row in proposals})
            == len(proposals),
        },
        "capacity_snapshot": {
            "path": str(capacity_path).replace("/", "\\"),
            "sha256": sha256_file(capacity_path),
            "captured_at": capacity.get("captured_at"),
            "idle_gpu_keys": capacity.get("idle_gpu_keys", []),
            "observed_unreachable_count": sum(
                item.get("status") == "unreachable" for item in capacity.get("observations", [])
            ),
            "read_only_input": True,
            "remote_compute_submitted": False,
        },
        "historical_pg_gate": "pending_exact_preflight",
        "materialization_status": "pending_exact_pg_gate",
        "selected_score_sha256": sha256_file(selected_score_path),
        "selected_challenger_sha256": sha256_file(selected_challenger_path),
        "postgresql_reads": 0,
        "postgresql_writes": 0,
        "pool_a_admitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (report_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return receipt


async def _readback(material: dict[str, Any], hashes: list[str]) -> dict[str, Any]:
    run_id = uuid.UUID(material["operational_run_id"])
    call_id = uuid.UUID(material["tool_call_id"])
    async with SessionFactory() as session:
        candidates = list(
            await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == run_id, Candidate.sequence_sha256.in_(hashes)
                )
            )
        )
        by_hash = {row.sequence_sha256: row for row in candidates}
        if set(by_hash) != set(hashes) or len(candidates) != len(hashes):
            raise ValueError("authoritative candidate readback is incomplete")
        evaluations = list(
            await session.scalars(select(Evaluation).where(Evaluation.tool_call_id == call_id))
        )
        tool_call = await session.get(ToolCall, call_id)
    if tool_call is None or tool_call.run_id != run_id:
        raise ValueError("materialization tool-call binding drifted")
    if any(row.run_id != run_id or row.sequence_sha256 not in hashes for row in candidates):
        raise ValueError("candidate run/hash binding drifted")
    counts = {
        digest: sum(e.candidate_id == by_hash[digest].id for e in evaluations) for digest in hashes
    }
    if set(counts.values()) != {17}:
        raise ValueError("expected exactly seventeen evaluations per candidate")
    return {
        "status": "readback_verified",
        "run_id": str(run_id),
        "tool_call_id": str(call_id),
        "candidate_count": len(candidates),
        "evaluation_count": len(evaluations),
        "evaluations_per_candidate": counts,
        "drift": 0,
        "replay_noop_expected": material.get("global_exact_replay_skip_count", 0) == 0,
    }


def close(report_dir: Path, capacity_path: Path) -> dict[str, Any]:
    selection = _json(report_dir / "selection_receipt.json")
    material = _json(report_dir / "materialization_receipt.json")
    qd = _json(report_dir / "provisional_qd.json")
    selected = _rows(report_dir / "materialization_input" / "candidate_scores.csv")
    hashes = [row["sequence_sha256"].lower() for row in selected]
    readback = asyncio.run(_readback(material, hashes))
    replay_path = report_dir / "materialization_replay.json"
    replay = _json(replay_path) if replay_path.exists() else None
    coarse_receipt_path = report_dir / "coarse5_prepared" / "coarse5_prepared_receipt.json"
    coarse = _json(coarse_receipt_path) if coarse_receipt_path.exists() else None
    qd_by_hash = {str(item["candidate_id"]).lower(): item for item in qd.get("contributions", [])}
    qd_selected = [qd_by_hash[digest] for digest in hashes]
    receipt = {
        "schema_version": "ampgent.fgf2-pepflow-qd-neighbor-vnext-close.1",
        "target_key": "fgf2",
        "generation": selection["generation"],
        "stage_counts": {
            "proposal": selection["proposal_count"],
            "formal12": selection["formal12_count"],
            "display": selection["display_count"],
            "support_ge_2": selection["support_ge_2_count"],
            "challenger_reviewed": selection["challenger_reviewed_count"],
            "challenger_no_conflict": selection["challenger_no_conflict_count"],
            "challenger_conflict": selection["challenger_conflict_count"],
        },
        "source_provisional_qd": selection["qd_provisional"],
        "final_qd": {
            "status": "formal_pg_new_materialized",
            "quality_eligible_count": len(qd_selected),
            "new_cell_count": sum(
                item.get("contribution") in {"empty_cell", "new_cell"} for item in qd_selected
            ),
            "replacement_count": sum(
                item.get("contribution") in {"incumbent_replacement", "replacement"}
                for item in qd_selected
            ),
            "materialized_contribution_count": len(qd_selected),
            "formal_pg_new": True,
            "future_priority_only": False,
        },
        "persistence": {
            "historical_pg_gate": "exact_verified",
            "run_id": material["operational_run_id"],
            "candidate_count": material["materialized_or_reused_in_run_count"],
            "evaluation_count": readback["evaluation_count"],
            "tool_call_id": material["tool_call_id"],
            "replay_count": material.get("global_exact_replay_skip_count", 0),
            "readback": readback,
            "replay_validation": {
                "status": (
                    "readback_noop_verified"
                    if replay and replay.get("replay_existing_operation")
                    and replay.get("inserted_evaluation_count") == 0
                    else "not_verified"
                ),
                "receipt_sha256": sha256_file(replay_path) if replay else None,
                "inserted_evaluation_count": (
                    replay.get("inserted_evaluation_count") if replay else None
                ),
                "drift": 0 if replay and replay.get("replay_existing_operation") else None,
            },
            "historical_runs_modified": False,
            "pool_a_admitted": False,
        },
        "coarse5": {
            "status": "prepared_not_dispatched",
            "dispatch_allowed": False,
            "candidate_count": len(qd_selected),
            "nstruct": 5,
            "task_key_contract": "rosetta-coarse5:fgf2:<run_id>:<authoritative_candidate_id>",
            "authoritative_candidate_ids": (
                coarse["authoritative_candidate_ids"] if coarse else []
            ),
            "queue_sha256": (coarse["queue_csv_sha256"] if coarse else None),
        },
        "capacity_snapshot": selection["capacity_snapshot"],
        "artifact_sha256s": {
            "selection": sha256_file(report_dir / "selection_receipt.json"),
            "materialization": sha256_file(report_dir / "materialization_receipt.json"),
            "provisional_qd": sha256_file(report_dir / "provisional_qd.json"),
            "capacity": sha256_file(capacity_path),
            "replay": sha256_file(replay_path) if replay else None,
        },
        "postgresql_reads": 3,
        "postgresql_writes": 0,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (report_dir / "close_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--capacity", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--close", action="store_true")
    args = parser.parse_args()
    payload = (
        prepare(args.report_dir, args.capacity)
        if args.prepare
        else close(args.report_dir, args.capacity)
    )
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
