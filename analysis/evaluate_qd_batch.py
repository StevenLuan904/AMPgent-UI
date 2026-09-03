from __future__ import annotations

import argparse
import bisect
import csv
import json
from pathlib import Path

from pepagent.autoresearch_quality_diversity import (
    build_quality_diversity_archive,
    candidate_from_score_row,
)
from pepagent.provenance.hashing import sha256_file, sha256_json


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _qd_rows(path: Path) -> list[dict[str, str]]:
    rows = _rows(path)
    if not rows or "amp_read_log10_mic_um__parent_benefit_percentile" in rows[0]:
        return rows
    required = (
        "amp_read_log10_mic_um",
        "llamp_log10_mic_um",
        "macrel_amp_probability",
    )
    if any(column not in rows[0] for column in required):
        raise ValueError(f"QD input lacks legacy activity columns: {path}")
    values = {
        column: sorted(float(row[column]) for row in rows)
        for column in required
    }
    for row in rows:
        for column in required:
            series = values[column]
            value = float(row[column])
            rank = bisect.bisect_right(series, value)
            row[f"{column}__parent_benefit_percentile"] = str(rank / len(series))
        toxin = row.get("toxinpred3_label", "").lower() == "non-toxin"
        hemolysis = row.get("macrel_hemolysis_label", "").lower() == "low"
        stable = float(row.get("guruprasad_instability_index", "999")) <= 50
        row["display_eligible"] = str(toxin and hemolysis and stable).lower()
        support = sum(
            float(row[column]) >= 0.5
            for column in (
                "amp_read_log10_mic_um",
                "llamp_log10_mic_um",
                "macrel_amp_probability",
            )
        )
        row["activity_model_support_count_calibrated"] = str(int(support))
    return [
        row
        for row in rows
        if row["display_eligible"] == "true"
        and int(row["activity_model_support_count_calibrated"]) >= 2
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-csv", type=Path, required=True)
    parser.add_argument("--prior-csv", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    batch = [candidate_from_score_row(row) for row in _rows(args.batch_csv)]
    prior = [
        candidate_from_score_row(row)
        for path in args.prior_csv
        for row in _qd_rows(path)
    ]
    state = build_quality_diversity_archive(prior, batch)
    payload = state.model_dump(mode="json")
    payload["batch_csv_sha256"] = sha256_file(args.batch_csv)
    payload["prior_csv_sha256s"] = [sha256_file(path) for path in args.prior_csv]
    payload["historical_run_modified"] = False
    payload["payload_sha256"] = sha256_json(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
