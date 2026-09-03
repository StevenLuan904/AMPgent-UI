"""Cross-target v6 wrapper for the validated one-aa QD-gap operator."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s
from fgf2_qd_gap_v5 import eligible_parents, generate_arm

from pepagent.provenance.hashing import sha256_file, sha256_json


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def run_target(
    target: str,
    parent_csv: Path,
    archive_path: Path,
    output_root: Path,
    limit: int,
    history: set[str],
) -> dict[str, object]:
    wrapper = json.loads(archive_path.read_text(encoding="utf-8"))
    archive = wrapper.get("branches", {}).get(target, wrapper)
    rows = [row for row in read_rows(parent_csv) if row.get("branch_key", "").lower() == target]
    parents = eligible_parents(rows, archive)
    proposals = generate_arm(parents, history, archive, "A_one_aa", limit)
    for row in proposals:
        row["branch_key"] = target
        row["proposal_mode"] = "cross-target-qd-gap-v6"
    history.update(row["sequence_sha256"] for row in proposals)
    output_dir = output_root / target
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(proposals[0]) if proposals else ["sequence"]
        )
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.cross-target-qd-gap-v6.1",
        "operator_id": "cross-target-qd-gap-v6",
        "target_key": target,
        "limit": limit,
        "parent_count": len(parents),
        "proposal_count": len(proposals),
        "preflight_target_cell_hit_count": len(proposals),
        "target_cells_hit": sorted({row["target_cell"] for row in proposals}),
        "parent_csv_sha256": sha256_file(parent_csv),
        "archive_sha256": sha256_file(archive_path),
        "proposal_csv_sha256": sha256_file(output),
        "weighted_q_plus_lambda_d_used": False,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target", action="append", required=True,
        help="target=limit=parent_csv=archive_json",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    history = asyncio.run(_historical_sequence_sha256s())
    results = []
    for spec in args.target:
        target, limit, parent_csv, archive = spec.split("=", 3)
        results.append(
            run_target(
                target, Path(parent_csv), Path(archive), args.output_root, int(limit), history
            )
        )
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
