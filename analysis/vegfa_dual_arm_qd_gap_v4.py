"""Comparable VEGFA v4 PepMLM versus PepGLAD/PepFlow gap-directed arms."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s
from qd_gap_directed_reciprocal_micrograft_v3 import generate


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def safe(row: dict[str, str]) -> bool:
    return (
        row.get("display_eligible", "").lower() == "true"
        and int(row.get("activity_model_support_count_calibrated") or 0) >= 2
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pepmlm-csv", type=Path, required=True)
    parser.add_argument("--prior-micrograft-csv", type=Path, required=True)
    parser.add_argument("--b-parent-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    pepmlm = read(args.pepmlm_csv)
    prior = read(args.prior_micrograft_csv)
    archive = json.loads(args.archive_json.read_text(encoding="utf-8"))
    history = asyncio.run(_historical_sequence_sha256s())
    a_parents = sorted((row for row in pepmlm if safe(row)), key=lambda row: row["sequence"])[:16]
    b_parents = [row for row in read(args.b_parent_csv) if safe(row)]
    a_donors = [{"donor_sequence": row["sequence"]} for row in a_parents]
    b_donors = prior
    proposals = []
    for arm, parents, donors in (
        ("A_pepmlm", a_parents, a_donors),
        ("B_pepglad_pepflow", b_parents, b_donors),
    ):
        rows = generate(parents, donors, history, archive, "vegfa", 16)
        for row in rows:
            row["arm"] = arm
            row["proposal_mode"] = "qd-gap-directed-reciprocal-micrograft-v4"
        proposals.extend(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(proposals[0]) if proposals else ["sequence"]
        )
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.vegfa-dual-arm-qd-gap-v4.1",
        "operator_id": "vegfa-dual-arm-qd-gap-v4",
        "arm_limits": {"A_pepmlm": 16, "B_pepglad_pepflow": 16},
        "proposal_count": len(proposals),
        "arm_counts": {
            arm: sum(row["arm"] == arm for row in proposals)
            for arm in ("A_pepmlm", "B_pepglad_pepflow")
        },
        "preflight_target_cell_hit_count": sum(
            row["target_cell_hit_preflight"] == "true" for row in proposals
        ),
        "proposal_csv_sha256": __import__("hashlib").sha256(output.read_bytes()).hexdigest(),
        "weighted_q_plus_lambda_d_used": False,
        "gpu_rosetta_md_submitted": False,
    }
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
