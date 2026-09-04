"""Generate an identity-bound AceA PepFlow QD-neighbor batch locally."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import generate_fgf2_pepflow_source_expansion as source_expansion

from pepagent.provenance.hashing import sha256_file, sha256_json

OPERATOR_ID = "acea-pepflow-qd-neighbor-vnext-1aa-v1"
TARGET_KEY = "acea"
GENERATION = 4
SEED = 20260904
SKIP_PARTS = {"work", "metrics", "raw"}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _scan_history(
    reports_root: Path, exclude: Path
) -> tuple[set[str], set[tuple[str, int, str]], dict[str, int]]:
    hashes: set[str] = set()
    edits: set[tuple[str, int, str]] = set()
    csv_count = 0
    row_count = 0
    for path in sorted(reports_root.rglob("*.csv")):
        if exclude in path.parents or path == exclude:
            continue
        if SKIP_PARTS.intersection(path.parts):
            continue
        rows = _rows(path)
        if not rows:
            continue
        csv_count += 1
        row_count += len(rows)
        for row in rows:
            digest = str(row.get("sequence_sha256") or "").strip().lower()
            sequence = "".join(str(row.get("sequence") or "").split()).upper()
            if (
                digest
                and sequence
                and hashlib.sha256(sequence.encode("utf-8")).hexdigest() == digest
            ):
                hashes.add(digest)
            parent = str(row.get("parent_sequence_sha256") or "").strip().lower()
            position = str(
                row.get("acceptor_start_zero_based") or row.get("edit_position_zero_based") or ""
            ).strip()
            residue = str(row.get("to_residue") or row.get("donor_fragment") or "").strip().upper()
            if parent and position.isdigit() and residue:
                edits.add((parent, int(position), residue))
    return (
        hashes,
        edits,
        {
            "csv_count": csv_count,
            "row_count": row_count,
            "sequence_sha256_count": len(hashes),
            "edit_key_count": len(edits),
        },
    )


def _parents(
    score_path: Path,
    queue_path: Path,
    qd_path: Path,
    required_candidate_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    scores = {row["sequence_sha256"].lower(): row for row in _rows(score_path)}
    queue = _rows(queue_path)
    qd = _rows(qd_path)
    qd_by_hash = {
        row["sequence_sha256"].lower(): row
        for row in qd
        if row.get("contribution") in {"empty_cell", "new_cell"}
    }
    parents: list[dict[str, str]] = []
    for item in queue:
        digest = item["sequence_sha256"].lower()
        qd_item = qd_by_hash.get(digest)
        score = scores.get(digest)
        candidate_id = item.get("candidate_id") or item.get("authoritative_candidate_id")
        if not qd_item or not score or not candidate_id:
            continue
        if not (
            score.get("formal_12_complete", "").lower() == "true"
            and score.get("display_eligible", "").lower() == "true"
            and int(score.get("activity_model_support_count_calibrated", "0") or 0) >= 2
        ):
            continue
        if digest != hashlib.sha256(score["sequence"].strip().upper().encode("utf-8")).hexdigest():
            raise ValueError("AceA parent sequence identity drifted")
        parents.append(
            {
                "candidate_id": candidate_id,
                "parent_run_id": item["run_id"],
                "sequence": score["sequence"].strip().upper(),
                "sequence_sha256": digest,
                "qd_cell": qd_item.get("actual_cell_id", item.get("qd_cell", "")),
                "display_eligible": "true",
                "activity_support_calibrated": score["activity_model_support_count_calibrated"],
            }
        )
    if required_candidate_ids is not None:
        parents = [row for row in parents if row["candidate_id"] in required_candidate_ids]
        if len(parents) != len(required_candidate_ids) or len(
            {row["candidate_id"] for row in parents}
        ) != len(required_candidate_ids):
            raise ValueError("requested authoritative AceA parent identities are incomplete")
    elif len(parents) != 4 or len({row["candidate_id"] for row in parents}) != 4:
        raise ValueError("expected four authoritative AceA PepGLAD QD parents")
    return sorted(parents, key=lambda row: (row["qd_cell"], row["sequence_sha256"]))


def _require_parent_readback(path: Path, candidate_ids: set[str]) -> dict[str, Any]:
    receipt = _json(path)
    observed = {str(value).lower() for value in receipt.get("candidate_ids", [])}
    if (
        receipt.get("status") != "readback_verified"
        or str(receipt.get("run_id", "")).lower()
        != "c6718e9b-15be-5197-87b0-ffe9d78d2ed7"
        or observed != {value.lower() for value in candidate_ids}
        or receipt.get("candidate_count") != len(candidate_ids)
        or receipt.get("evaluation_count") != len(candidate_ids) * 17
        or receipt.get("identity_drift") != 0
    ):
        raise ValueError("AceA parent exact PG readback contract failed")
    return receipt


def run(args: argparse.Namespace) -> dict[str, Any]:
    global GENERATION, OPERATOR_ID
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    GENERATION = args.generation
    OPERATOR_ID = args.operator_id
    source_expansion.TARGET_KEY = TARGET_KEY
    source_expansion.GENERATION = GENERATION
    source_expansion.OPERATOR_ID = OPERATOR_ID
    source_expansion._POLICY = source_expansion._read_policy(args.archive_json)
    required_parent_ids = {
        value.strip().lower() for value in args.parent_candidate_ids
    } or None
    if required_parent_ids and args.parent_readback_receipt is None:
        raise ValueError("parent exact PG readback receipt is required")
    parent_readback = (
        _require_parent_readback(args.parent_readback_receipt, required_parent_ids)
        if required_parent_ids
        else None
    )
    parents = _parents(
        args.parent_scores,
        args.parent_queue,
        args.parent_qd,
        required_candidate_ids=required_parent_ids,
    )
    donors = source_expansion.load_pepflow_donors(args.donor_csv)
    history, prior_edits, scan = _scan_history(args.repo_root / "reports", output_dir)
    archive = _json(args.archive_json)
    proposals = source_expansion.build_proposals(
        parents,
        donors,
        history,
        prior_edits,
        set(archive.get("empty_cell_ids", [])),
        limit=args.limit,
    )
    if not proposals:
        raise RuntimeError("no AceA PepFlow proposal reached a fixed-archive empty cell")
    proposal_path = output_dir / "proposals.csv"
    with proposal_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    capacity = _json(args.capacity)
    receipt = {
        "schema_version": "ampgent.acea-pepflow-qd-neighbor-vnext-generation.1",
        "target_key": TARGET_KEY,
        "source": "PepFlow",
        "operator_id": OPERATOR_ID,
        "generation": GENERATION,
        "seed": SEED,
        "parent_source_evidence": ["AceA_PepGLAD_QD_new_cell_elites", "AceA_PepFlow_source_parent"],
        "parent_count": len(parents),
        "authoritative_parent_candidate_ids": [row["candidate_id"] for row in parents],
        "parent_exact_readback_receipt": (
            str(args.parent_readback_receipt) if parent_readback else None
        ),
        "parent_exact_readback_receipt_sha256": (
            sha256_file(args.parent_readback_receipt) if parent_readback else None
        ),
        "parent_run_ids": sorted({row["parent_run_id"] for row in parents}),
        "donor_count": len(donors),
        "donor_artifact": str(args.donor_csv),
        "donor_artifact_sha256": sha256_file(args.donor_csv),
        "proposal_count": len(proposals),
        "empty_cell_preflight_count": len(proposals),
        "fixed_archive_cell_count": 2160,
        "archive_sha256": sha256_file(args.archive_json),
        "historical_local_scan": scan,
        "proposal_csv_sha256": sha256_file(proposal_path),
        "identity_contract": {
            "sequence_sha256_unique": len({row["sequence_sha256"] for row in proposals})
            == len(proposals),
            "sequence_order_preserved": True,
            "target_key": TARGET_KEY,
            "output_dir_is_independent": True,
        },
        "capacity_snapshot": {
            "path": str(args.capacity).replace("/", "\\"),
            "sha256": sha256_file(args.capacity),
            "captured_at_in_file": capacity.get("captured_at"),
            "idle_gpu_keys": capacity.get("idle_gpu_keys", []),
            "unreachable_count": sum(
                item.get("status") == "unreachable" for item in capacity.get("observations", [])
            ),
            "read_only_input": True,
            "remote_compute_submitted": False,
        },
        "historical_pg_gate": "pending_exact_preflight",
        "materialization_status": "proposed_not_materialized",
        "candidate_identity_status": "proposal_only",
        "postgresql_reads": 0,
        "postgresql_writes": 0,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--parent-queue", type=Path, required=True)
    parser.add_argument("--parent-qd", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--capacity", type=Path, required=True)
    parser.add_argument("--parent-readback-receipt", type=Path)
    parser.add_argument("--parent-candidate-ids", nargs="*", default=[])
    parser.add_argument("--generation", type=int, default=4)
    parser.add_argument("--operator-id", default=OPERATOR_ID)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
