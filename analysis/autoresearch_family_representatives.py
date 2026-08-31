from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json


def _selection_key(row: dict[str, str]) -> tuple[object, ...]:
    return (
        -int(row["activity_model_support_count_calibrated"]),
        float(row["calibrated_hemolysis_probability"]),
        -float(row["amp_read_log10_mic_um__parent_benefit_percentile"]),
        -float(row["llamp_log10_mic_um__parent_benefit_percentile"]),
        -float(row["macrel_amp_probability__parent_benefit_percentile"]),
        float(row["guruprasad_instability_index"]),
        row["sequence"],
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty family representatives")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> None:
    with args.input_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("family-audited candidate input is empty")
    by_family: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        if row["excellent_sequence_stage_calibrated"].lower() != "true":
            continue
        key = (row["branch_key"], row["family_key_80_80"])
        by_family.setdefault(key, []).append(row)
    representatives: list[dict[str, Any]] = []
    for (branch_key, family_key), family_rows in sorted(by_family.items()):
        selected = min(family_rows, key=_selection_key)
        representatives.append(
            {
                **selected,
                "family_candidate_count": len(family_rows),
                "family_representative_selected": "true",
                "family_selection_method": (
                    "lexicographic_support_challenger_activity_stability_no_weighted_total"
                ),
                "diversity_qualified": "true",
                "selection_branch_key": branch_key,
                "selection_family_key": family_key,
            }
        )
    representatives.sort(key=lambda row: (row["branch_key"], row["family_key_80_80"]))
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_csv, representatives)
    branch_summary = []
    for branch_key in sorted({row["branch_key"] for row in representatives}):
        branch_rows = [row for row in representatives if row["branch_key"] == branch_key]
        branch_summary.append(
            {
                "branch_key": branch_key,
                "representative_count": len(branch_rows),
                "distinct_family_count": len({row["family_key_80_80"] for row in branch_rows}),
                "support_3_count": sum(
                    int(row["activity_model_support_count_calibrated"]) == 3 for row in branch_rows
                ),
            }
        )
    receipt = {
        "schema_version": "ampgent.autoresearch-family-representatives.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256": sha256_file(args.input_csv),
        "source_candidate_count": len(rows),
        "representative_count": len(representatives),
        "distinct_family_count": len({row["family_key_80_80"] for row in representatives}),
        "branch_summary": branch_summary,
        "selection_uses_weighted_total": False,
        "output_csv_sha256": sha256_file(args.output_csv),
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
