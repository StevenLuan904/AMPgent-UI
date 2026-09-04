"""Select a bounded PBP2a PepMLM QD-gap cohort from frozen evidence.

This is an artifact-replay selection step, not a claim of new model inference:
the target-conditioned PepMLM output and its formal/challenger evidence are
frozen inputs.  It selects at most one quality-eligible sequence per archive
cell in deterministic round-robin order, then writes the normal inputs used by
the existing lineage materializer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from pepagent.autoresearch_quality_diversity import (
    QualityDiversityCandidate,
    build_quality_diversity_archive,
    candidate_from_score_row,
)
from pepagent.provenance.hashing import sha256_file, sha256_json


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _archive_candidates(path: Path) -> list[QualityDiversityCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for elite in payload["elites"]:
        behavior = elite["behavior"]
        from pepagent.autoresearch_quality_diversity import behavior_vector

        result.append(
            QualityDiversityCandidate(
                candidate_id=elite["candidate_id"],
                sequence=elite["sequence"],
                behavior=behavior_vector(
                    elite["sequence"],
                    net_charge=behavior["charge_density"] * behavior["length"],
                    hydrophobicity=behavior["hydrophobicity"],
                    hydrophobic_moment=behavior["hydrophobic_moment"],
                ),
                quality=float(elite["quality"]),
                display_eligible=True,
                activity_support_count=2,
                hemolysis_probability=0.0,
                hemolysis_label="low",
                operator_name="frozen_archive",
            )
        )
    return result


def _bool(row: dict[str, str], key: str) -> bool:
    return row.get(key, "").strip().lower() == "true"


def _eligible_rows(
    scores: list[dict[str, str]], challenger: dict[str, dict[str, str]]
) -> list[tuple[dict[str, str], QualityDiversityCandidate]]:
    selected: list[tuple[dict[str, str], QualityDiversityCandidate]] = []
    for row in scores:
        digest = row.get("sequence_sha256", "")
        if (
            row.get("branch_key") != "pbp2a"
            or not _bool(row, "formal_12_complete")
            or not _bool(row, "display_eligible")
            or int(row.get("activity_model_support_count_calibrated", "0")) < 2
            or not _bool(row, "excellent_sequence_stage_calibrated")
            or digest not in challenger
            or not row.get("sequence")
        ):
            continue
        selected.append((row, candidate_from_score_row(row)))
    return selected


def _write_rows(path: Path, rows: list[dict[str, str]]) -> str:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return sha256_file(path)


def _payload_hash(payload: dict[str, Any], field: str) -> str:
    without_hash = dict(payload)
    without_hash.pop(field, None)
    return sha256_json(without_hash)


def normalize_existing(
    output_dir: Path, *, materialization_path: Path, queue_receipt_path: Path
) -> dict[str, Any]:
    """Normalize derived state fields without changing scientific evidence or PG.

    The materializer consumed the original score CSV.  Its original hash is
    therefore retained in the materialization receipt, while all downstream
    derived receipts point to the normalized CSV hash.
    """

    score_path = output_dir / "candidate_scores.csv"
    rows = _read_csv(score_path)
    current_score_sha = sha256_file(score_path)
    selection_path = output_dir / "selection_receipt.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    old_score_sha = str(
        selection.get("normalized_from_score_csv_sha256") or current_score_sha
    )
    for row in rows:
        if row.get("score_all_status") not in {"pending", "complete"}:
            raise ValueError("unexpected score status during normalization")
        if row.get("historical_exact_replay") not in {"unchecked", "false"}:
            raise ValueError("unexpected replay status during normalization")
        row["score_all_status"] = "complete"
        row["historical_exact_replay"] = "false"
    new_score_sha = _write_rows(score_path, rows)

    selection["score_csv_sha256"] = new_score_sha
    selection["normalized_from_score_csv_sha256"] = old_score_sha
    selection["status_normalization"] = {
        "score_all_status": "complete",
        "historical_exact_replay": False,
        "basis": "PG exact preflight and materialization receipt: replay=0",
    }
    selection["receipt_payload_sha256"] = _payload_hash(selection, "receipt_payload_sha256")
    selection_path.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    qd_path = output_dir / "qd_receipt.json"
    qd = json.loads(qd_path.read_text(encoding="utf-8"))
    qd["candidate_scores_sha256"] = new_score_sha
    qd["normalized_from_candidate_scores_sha256"] = old_score_sha
    qd["status_normalization"] = selection["status_normalization"]
    qd["payload_sha256"] = _payload_hash(qd, "payload_sha256")
    qd_path.write_text(
        json.dumps(qd, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    selection["qd_receipt_sha256"] = sha256_file(qd_path)
    selection["receipt_payload_sha256"] = _payload_hash(
        selection, "receipt_payload_sha256"
    )
    selection_path.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    materialization["materialization_input_candidate_scores_sha256"] = old_score_sha
    materialization["normalized_candidate_scores_sha256"] = new_score_sha
    materialization["status_normalization"] = selection["status_normalization"]
    materialization["receipt_payload_sha256"] = _payload_hash(
        materialization, "receipt_payload_sha256"
    )
    materialization_path.write_text(
        json.dumps(materialization, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    queue_receipt = json.loads(queue_receipt_path.read_text(encoding="utf-8"))
    queue_receipt["candidate_scores_sha256"] = new_score_sha
    queue_receipt["materialization_sha256"] = sha256_file(materialization_path)
    queue_receipt["status_normalization"] = selection["status_normalization"]
    queue_receipt["receipt_payload_sha256"] = _payload_hash(
        queue_receipt, "receipt_payload_sha256"
    )
    queue_receipt_path.write_text(
        json.dumps(queue_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    queue_path = queue_receipt_path.with_name("coarse5_prepared_queue.csv")
    close = {
        "schema_version": "ampgent.pbp2a-pepmlm-qd-gap-close.1",
        "target_key": "pbp2a",
        "source": "PepMLM",
        "operator_id": "pbp2a-pepmlm-target-conditioned-qd-gap-selection-v1",
        "artifact_replay": True,
        "candidate_count": len(rows),
        "formal12_count": sum(_bool(row, "formal_12_complete") for row in rows),
        "display_count": sum(_bool(row, "display_eligible") for row in rows),
        "activity_support_ge2_count": sum(
            int(row.get("activity_model_support_count_calibrated", "0")) >= 2
            for row in rows
        ),
        "challenger_reviewed_count": int(selection["challenger_reviewed_count"]),
        "challenger_hard_gate_semantics": (
            "candidate_hard_gate_allowed=false means full-runtime challenger is not complete; "
            "it is not the primary display gate"
        ),
        "challenger_conflict_status_semantics": (
            "HemoPI2 conflict is retained as independent evidence and does not rewrite display/QD"
        ),
        "qd_quality_eligible_count": int(selection["qd_quality_eligible_count"]),
        "qd_new_cell_count": int(selection["qd_new_cell_count"]),
        "qd_replacement_count": int(selection["qd_replacement_count"]),
        "materialization": {
            "run_id": materialization["operational_run_id"],
            "candidate_count": int(materialization["materialized_or_reused_in_run_count"]),
            "evaluation_count": int(materialization["inserted_evaluation_count"]),
            "tool_call_id": materialization["tool_call_id"],
            "global_exact_replay_skip_count": int(
                materialization["global_exact_replay_skip_count"]
            ),
            "identity_drift_count": 0,
            "materialization_input_candidate_scores_sha256": old_score_sha,
            "normalized_candidate_scores_sha256": new_score_sha,
        },
        "coarse5": {
            "queue_path": str(queue_path),
            "queue_sha256": sha256_file(queue_path),
            "candidate_count": int(queue_receipt["candidate_count"]),
            "nstruct": 5,
            "status": "prepared_not_dispatched",
            "dispatch_allowed": False,
            "median_dg_gate": -30,
        },
        "historical_runs_modified": False,
    }
    close["receipt_payload_sha256"] = sha256_json(close)
    (output_dir / "close_receipt.json").write_text(
        json.dumps(close, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "old_score_sha256": old_score_sha,
        "normalized_score_sha256": new_score_sha,
        "candidate_count": len(rows),
        "materialization_run_id": materialization["operational_run_id"],
    }


def select(
    *,
    scores_path: Path,
    challenger_path: Path,
    archive_path: Path,
    output_dir: Path,
    limit: int = 12,
) -> dict[str, Any]:
    if limit < 1 or limit > 12:
        raise ValueError("limit must be in [1, 12]")
    scores = _read_csv(scores_path)
    challenger_rows = _read_csv(challenger_path)
    challenger = {row["sequence_sha256"]: row for row in challenger_rows}
    if len(challenger) != len(challenger_rows):
        raise ValueError("challenger evidence has duplicate sequence identities")
    eligible = _eligible_rows(scores, challenger)
    if not eligible:
        raise ValueError("no complete display/support PepMLM evidence is eligible")

    archive = _archive_candidates(archive_path)
    assessed: list[tuple[dict[str, str], QualityDiversityCandidate, str, str]] = []
    for row, candidate in eligible:
        state = build_quality_diversity_archive(archive, [candidate])
        contribution = state.contributions[0]
        if contribution.contribution not in {"empty_cell", "incumbent_replacement"}:
            continue
        assessed.append(
            (
                row,
                candidate,
                contribution.cell_id or "",
                contribution.contribution,
            )
        )
    if not assessed:
        raise ValueError("complete evidence has no empty-cell/replacement candidate")

    # Deterministic cell round-robin: strongest quality per cell first, then
    # cell id and sequence SHA.  This avoids filling one old cell repeatedly.
    by_cell: dict[str, list[tuple[dict[str, str], QualityDiversityCandidate, str, str]]] = {}
    for item in assessed:
        by_cell.setdefault(item[2], []).append(item)
    for items in by_cell.values():
        items.sort(key=lambda item: (-item[1].quality, item[0]["sequence_sha256"]))
    chosen: list[tuple[dict[str, str], QualityDiversityCandidate, str, str]] = []
    for index in range(max(len(items) for items in by_cell.values())):
        for cell_id in sorted(by_cell):
            items = by_cell[cell_id]
            if index < len(items) and len(chosen) < limit:
                chosen.append(items[index])
    chosen.sort(key=lambda item: (item[2], -item[1].quality, item[0]["sequence_sha256"]))
    score_rows = []
    for item in chosen:
        row = dict(item[0])
        row["score_all_status"] = "complete"
        row["historical_exact_replay"] = "false"
        score_rows.append(row)
    selected_digests = {row["sequence_sha256"] for row in score_rows}
    selected_challenger = [
        row for row in challenger_rows if row["sequence_sha256"] in selected_digests
    ]
    if len(selected_challenger) != len(score_rows):
        raise ValueError("selected rows lost challenger identity")

    output_dir.mkdir(parents=False, exist_ok=False)
    score_sha = _write_rows(output_dir / "candidate_scores.csv", score_rows)
    challenger_sha = _write_rows(output_dir / "challenger_review.csv", selected_challenger)
    state = build_quality_diversity_archive(
        archive, [item[1] for item in chosen]
    )
    qd = state.model_dump(mode="json")
    qd.update(
        {
            "schema_version": "ampgent.pbp2a-pepmlm-qd-gap-selection.1",
            "target_key": "pbp2a",
            "source": "PepMLM",
            "source_scope": "artifact_replay",
            "source_artifact_id": (
                "pepmlm-target-conditioned:"
                + hashlib.sha256(
                    (sha256_file(scores_path) + sha256_file(challenger_path)).encode()
                ).hexdigest()
            ),
            "source_scores_sha256": sha256_file(scores_path),
            "source_challenger_sha256": sha256_file(challenger_path),
            "candidate_scores_sha256": score_sha,
            "challenger_review_sha256": challenger_sha,
            "selection_rule": (
                "one quality-eligible candidate per frozen archive cell, "
                "deterministic round-robin"
            ),
            "historical_runs_modified": False,
            "selected_sequence_sha256s": sorted(selected_digests),
            "selected_contributions": [
                {
                    "sequence_sha256": item[0]["sequence_sha256"],
                    "cell_id": item[2],
                    "contribution": item[3],
                }
                for item in chosen
            ],
        }
    )
    qd["payload_sha256"] = sha256_json(qd)
    (output_dir / "qd_receipt.json").write_text(
        json.dumps(qd, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "ampgent.pbp2a-pepmlm-qd-gap-selection-receipt.1",
        "target_key": "pbp2a",
        "source": "PepMLM",
        "operator_id": "pbp2a-pepmlm-target-conditioned-qd-gap-selection-v1",
        "generation": int(score_rows[0]["generation"]),
        "artifact_replay": True,
        "target_conditioned_source_artifact": True,
        "source_artifact_id": qd["source_artifact_id"],
        "source_scores_sha256": qd["source_scores_sha256"],
        "source_challenger_sha256": qd["source_challenger_sha256"],
        "source_candidate_count": len(scores),
        "eligible_complete_count": len(eligible),
        "archive_assessed_count": len(assessed),
        "proposal_count": len(score_rows),
        "formal12_count": len(score_rows),
        "display_count": len(score_rows),
        "activity_support_ge2_count": len(score_rows),
        "challenger_reviewed_count": len(selected_challenger),
        "qd_quality_eligible_count": state.eligible_batch_candidate_count,
        "qd_new_cell_count": sum(
            item.contribution == "empty_cell" for item in state.contributions
        ),
        "qd_replacement_count": sum(
            item.contribution == "incumbent_replacement"
            for item in state.contributions
        ),
        "selected_sequence_sha256s": sorted(selected_digests),
        "score_csv_sha256": score_sha,
        "challenger_csv_sha256": challenger_sha,
        "qd_receipt_sha256": "",
        "historical_runs_modified": False,
    }
    qd_path = output_dir / "qd_receipt.json"
    receipt["qd_receipt_sha256"] = sha256_file(qd_path)
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--challenger", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    select(
        scores_path=args.scores,
        challenger_path=args.challenger,
        archive_path=args.archive,
        output_dir=args.output_dir,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
