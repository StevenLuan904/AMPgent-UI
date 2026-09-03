from __future__ import annotations

import bisect
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "reports/target_agnostic_quality_combined_round10_20260826T2112.csv"
CONCORDANCE = (
    ROOT
    / "reports/target_agnostic_quality_combined_round10_20260826T2112_activity_concordance.json"
)
OUT = ROOT / "reports/target_agnostic_v10_witness_20260903"
METRICS = ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


archive = rows(ARCHIVE)
ordered = {metric: sorted(float(row[metric]) for row in archive) for metric in METRICS}
eligible = []
for row in archive:
    display = (
        row.get("branch_key") == "target_agnostic_amp"
        and row.get("toxinpred3_label", "").lower() == "non-toxin"
        and row.get("macrel_hemolysis_label", "").lower() == "low"
        and float(row.get("guruprasad_instability_index", 999)) <= 50
    )
    percentiles = {}
    for metric in METRICS:
        value = float(row[metric])
        if metric == "macrel_amp_probability":
            percentiles[metric] = bisect.bisect_right(ordered[metric], value) / len(archive)
        else:
            percentiles[metric] = (len(archive) - bisect.bisect_left(ordered[metric], value)) / len(
                archive
            )
    support = sum(value >= 0.75 for value in percentiles.values())
    if display and support >= 2:
        eligible.append(
            {
                "candidate_id": row["candidate_id"],
                "sequence": row["sequence"],
                "sequence_sha256": row["sequence_sha256"],
                "family_key_80_80": row.get("family_key_80_80", ""),
                "percentiles": percentiles,
                "support": support,
            }
        )
eligible.sort(key=lambda row: (-row["support"], row["sequence_sha256"]))
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "parents.csv").write_text(
    "candidate_id,sequence,sequence_sha256,family_key_80_80,support\n"
    + "\n".join(
        f"{row['candidate_id']},{row['sequence']},{row['sequence_sha256']},{row['family_key_80_80']},{row['support']}"
        for row in eligible[:16]
    )
    + "\n",
    encoding="utf-8",
)
payload = {
    "schema_version": "ampgent.target-agnostic-calibration-witness.1",
    "branch": "target_agnostic_amp",
    "cohort_count": len(archive),
    "source_archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
    "source_concordance_sha256": hashlib.sha256(CONCORDANCE.read_bytes()).hexdigest(),
    "model_release_key": "ampgent_formal12_frozen",
    "directions": {
        "amp_read_log10_mic_um": "minimize",
        "llamp_log10_mic_um": "minimize",
        "macrel_amp_probability": "maximize",
    },
    "percentile_threshold": 0.75,
    "display_gate": "ToxinPred3 Non-Toxin + Macrel low + Guruprasad <=50",
    "display_support_ge_2_count": len(eligible),
    "frozen_parent_count": min(16, len(eligible)),
    "parents_csv": str(OUT / "parents.csv"),
    "parent_ids": [row["candidate_id"] for row in eligible[:16]],
    "source_pg_runs": [
        "c8c29180-c076-5851-a465-a55f8a5ce9ea",
        "0f886428-28a0-5ccf-84d2-ac022c2dd462",
        "1da451b5-d8b3-5c3d-b8b4-176c9f35b35a",
        "066d3144-2eb1-533f-9c9f-fad39f938f26",
        "6fe56b08-6360-533f-8491-72f7f9a7ff0b",
        "c6df420c-674b-59fd-bb34-5bdae2f296e4",
        "f7c3405a-d3dc-55bb-a5e8-4852a7e97166",
        "3a15dab3-2888-5340-9bb9-4f414c1b986d",
    ],
    "primary_release_evidence": (
        "PG evaluations grouped under ampgent_formal12_frozen; "
        "challenger/shadow releases recorded separately in pg_witness_probe.json"
    ),
}
(OUT / "witness.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
