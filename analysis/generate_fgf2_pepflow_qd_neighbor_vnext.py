"""Generate a local, identity-bound FGF2 PepFlow QD-neighbor batch."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import generate_fgf2_pepflow_source_expansion as source_expansion

from pepagent.provenance.hashing import sha256_file, sha256_json

HISTORY_CUTOFF = "20260904"


def _sequence_sha(sequence: str) -> str:
    normalized = "".join(sequence.split()).upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def scan_local_history(
    reports_root: Path, *, exclude_dir: Path
) -> tuple[set[str], set[tuple[str, int, str]], dict[str, int]]:
    """Collect sequence and parent-position-to history without a database query."""
    sequence_hashes: set[str] = set()
    edit_keys: set[tuple[str, int, str]] = set()
    csv_count = 0
    row_count = 0
    hash_drift_count = 0
    for path in sorted(reports_root.rglob("*.csv")):
        if exclude_dir == path or exclude_dir in path.parents:
            continue
        relative = path.relative_to(reports_root)
        path_text = str(relative).lower()
        if "fgf2" not in path_text and "source_effect_benchmark" not in path_text:
            continue
        dates = re.findall(r"20\d{6}", path_text)
        if any(date > HISTORY_CUTOFF for date in dates):
            continue
        if any(part.lower() in {"work", "metrics", "raw", "output"} for part in relative.parts):
            continue
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    row_count += 1
                    sequence = "".join(str(row.get("sequence") or "").split()).upper()
                    declared = str(row.get("sequence_sha256") or "").strip().lower()
                    if sequence:
                        digest = _sequence_sha(sequence)
                        sequence_hashes.add(digest)
                        if declared and declared != digest:
                            hash_drift_count += 1
                    elif len(declared) == 64:
                        sequence_hashes.add(declared)
                    parent_sha = str(row.get("parent_sequence_sha256") or "").strip().lower()
                    position = row.get(
                        "acceptor_start_zero_based",
                        row.get("edit_position_zero_based", ""),
                    )
                    residue = (
                        str(row.get("to_residue") or row.get("donor_fragment") or "")
                        .strip()
                        .upper()
                    )
                    if parent_sha and position not in {"", None} and residue:
                        try:
                            edit_keys.add((parent_sha, int(position), residue))
                        except ValueError:
                            continue
            csv_count += 1
        except (OSError, UnicodeError):
            continue
    return (
        sequence_hashes,
        edit_keys,
        {
            "csv_count": csv_count,
            "row_count": row_count,
            "sequence_hash_count": len(sequence_hashes),
            "edit_key_count": len(edit_keys),
            "declared_hash_drift_count": hash_drift_count,
        },
    )


def run(args: argparse.Namespace) -> dict[str, object]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    source_expansion._POLICY = source_expansion._read_policy(args.archive_json)
    parents = source_expansion.load_authoritative_parents(
        args.parent_scores, args.authority_queue, args.archive_json
    )
    donors = source_expansion.load_pepflow_donors(args.donor_csv)
    history, prior_edits, history_stats = scan_local_history(
        args.reports_root.resolve(), exclude_dir=output_dir
    )
    archive = json.loads(args.archive_json.read_text(encoding="utf-8"))
    proposals = source_expansion.build_proposals(
        parents,
        donors,
        history,
        prior_edits,
        set(archive.get("empty_cell_ids", [])),
        limit=args.limit,
    )
    if not proposals:
        raise RuntimeError("no local FGF2 PepFlow QD-neighbor proposal reached an empty cell")
    for row in proposals:
        row["generation"] = str(args.generation)
        row["operator_id"] = args.operator_id
        row["proposal_mode"] = "pepflow_qd_neighbor_1aa_vnext"
        row["history_gate"] = "local_reports_exact_sequence_and_prior_edit_passed"
        row["pg_history_gate"] = "pending_exact_preflight_for_eligible_candidates"
        row["materialization_status"] = "proposed_not_materialized"
        row["candidate_identity_status"] = "proposal_only"
    output = output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt: dict[str, object] = {
        "schema_version": "ampgent.fgf2-pepflow-qd-neighbor-vnext-generation.1",
        "target_key": "fgf2",
        "source": "PepFlow",
        "operator_id": args.operator_id,
        "generation": args.generation,
        "seed": args.seed,
        "parent_source_evidence": ["FGF2_PepGLAD_QD_elites", "FGF2_PepFlow_QD_elites"],
        "parent_count": len(parents),
        "authoritative_parent_candidate_ids": [row["candidate_id"] for row in parents],
        "parent_run_ids": sorted({row["parent_run_id"] for row in parents}),
        "donor_count": len(donors),
        "donor_artifact": str(args.donor_csv),
        "donor_artifact_sha256": sha256_file(args.donor_csv),
        "proposal_count": len(proposals),
        "empty_cell_preflight_count": len(proposals),
        "fixed_archive_cell_count": 2160,
        "historical_local_scan": history_stats,
        "proposal_csv_sha256": sha256_file(output),
        "identity_contract": {
            "sequence_sha256_unique": len({row["sequence_sha256"] for row in proposals})
            == len(proposals),
            "sequence_order_preserved": True,
            "expected_input": "proposals.csv",
        },
        "historical_pg_gate": "pending",
        "materialization_status": "proposed_not_materialized",
        "candidate_identity_status": "proposal_only",
        "postgresql_reads": 0,
        "postgresql_writes": 0,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--authority-queue", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generation", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--operator-id", default="fgf2-pepflow-qd-neighbor-vnext-1aa-v1")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
