from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from pepagent.db.session import SessionFactory

TARGETS = ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
ACCESSION_TO_TARGET = {
    "P0A9G6": "acea",
    "NP_416734.1": "gyra",
    "WP_308061015.1": "pbp2a",
    "NP_001020421.2": "vegfa",
    "NP_032032.1": "fgf2",
    "NP_001272991.1": "angpt1",
}
FORMAL_METRICS = (
    "amp_read_log10_mic_um",
    "guruprasad_instability_index",
    "hydrophobic_moment_eisenberg",
    "hydrophobic_ratio_modlamp",
    "llamp_log10_mic_um",
    "macrel_amp_probability",
    "macrel_hemolysis_label",
    "macrel_hemolysis_probability",
    "maximum_hydrophobic_run",
    "net_charge_ph7_4",
    "toxinpred3_hybrid_score",
    "toxinpred3_label",
)

POOL_A_SQL = text(
    """
    WITH rosetta_ranked AS MATERIALIZED (
      SELECT e.candidate_id, e.numeric_value AS primary_dg,
             (e.raw_json->>'nstruct')::int AS nstruct,
             e.raw_json->>'primary_aggregation' AS primary_aggregation,
             e.raw_json->>'receipt_sha256' AS receipt_sha256,
             row_number() OVER (
               PARTITION BY e.candidate_id
               ORDER BY (e.raw_json->>'nstruct')::int DESC, e.created_at DESC, e.id DESC
             ) AS protocol_rank
      FROM evaluations e
      WHERE e.metric_name='rosetta_dg_separated_reu'
        AND e.status='succeeded'
        AND e.numeric_value IS NOT NULL
        AND e.raw_json->>'nstruct' IN ('5','20','200')
    ), rosetta AS MATERIALIZED (
      SELECT * FROM rosetta_ranked WHERE protocol_rank=1
    ), formal AS MATERIALIZED (
      SELECT e.candidate_id,
             count(DISTINCT e.metric_name) FILTER (
               WHERE e.status='succeeded' AND e.metric_name = ANY(:formal_metrics)
             ) AS formal_metric_count,
             max(e.numeric_value) FILTER (
               WHERE e.status='succeeded' AND e.metric_name='guruprasad_instability_index'
             ) AS instability,
             bool_or(e.text_value='Non-Toxin') FILTER (
               WHERE e.status='succeeded' AND e.metric_name='toxinpred3_label'
             ) AS non_toxin,
             bool_or(lower(e.text_value)='low') FILTER (
               WHERE e.status='succeeded' AND e.metric_name='macrel_hemolysis_label'
             ) AS macrel_low
      FROM evaluations e
      JOIN rosetta r ON r.candidate_id=e.candidate_id
      GROUP BY e.candidate_id
    ), challenger AS MATERIALIZED (
      SELECT e.candidate_id,
             bool_or(
               e.evidence_role='challenger'
               AND e.model_release_key='hemopi2_v27_calibrated_v39'
               AND e.applicability_status='applicable'
             ) AS hemopi2_covered,
             bool_or(
               e.evidence_role='shadow'
               AND e.model_release_key='apex_runtime_inventory_v1'
               AND e.applicability_status IN ('applicable','runtime_unavailable')
             ) AS apex_covered,
             bool_or(
               e.evidence_role='shadow'
               AND e.model_release_key='peptiverse_runtime_inventory_v1'
               AND e.applicability_status IN ('applicable','runtime_unavailable')
             ) AS peptiverse_covered,
             bool_or(e.conflict_status='cross_model_disagreement_retained') AS retained_conflict
      FROM evaluations e
      JOIN rosetta r ON r.candidate_id=e.candidate_id
      GROUP BY e.candidate_id
    )
    SELECT c.id AS candidate_id, c.run_id, c.sequence, c.sequence_sha256,
           t.accession, c.metadata_json->>'family_key_80_80' AS family_key,
           coalesce((c.metadata_json->>'display_eligible')::boolean,false) AS display_eligible,
           coalesce((c.metadata_json->>'activity_model_support_count')::int,0) AS activity_support,
           coalesce((c.metadata_json->>'excellent_sequence_stage')::boolean,false) AS excellent,
           f.formal_metric_count, f.instability, coalesce(f.non_toxin,false) AS non_toxin,
           coalesce(f.macrel_low,false) AS macrel_low,
           coalesce(ch.hemopi2_covered,false) AS hemopi2_covered,
           coalesce(ch.apex_covered,false) AS apex_covered,
           coalesce(ch.peptiverse_covered,false) AS peptiverse_covered,
           coalesce(ch.retained_conflict,false) AS retained_conflict,
           ro.primary_dg, ro.nstruct, ro.primary_aggregation, ro.receipt_sha256
    FROM rosetta ro
    JOIN candidates c ON c.id=ro.candidate_id
    JOIN experiment_runs er ON er.id=c.run_id
    JOIN targets t ON t.id=er.target_id
    LEFT JOIN formal f ON f.candidate_id=c.id
    LEFT JOIN challenger ch ON ch.candidate_id=c.id
    WHERE t.accession = ANY(:accessions)
    ORDER BY t.accession, ro.primary_dg, c.id
    """
)


def _eligible(row: dict[str, Any]) -> bool:
    instability = row.get("instability")
    return bool(
        row.get("display_eligible")
        and int(row.get("activity_support") or 0) >= 2
        and int(row.get("formal_metric_count") or 0) == 12
        and instability is not None
        and float(instability) <= 50.0
        and row.get("non_toxin")
        and row.get("macrel_low")
        and row.get("hemopi2_covered")
        and row.get("apex_covered")
        and row.get("peptiverse_covered")
        and row.get("family_key")
        and float(row["primary_dg"]) < -30.0
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, dict[str, Any]] = {}
    selected: list[dict[str, Any]] = []
    for target in TARGETS:
        cohort = [row for row in rows if row["target_key"] == target]
        dg_pass = [row for row in cohort if float(row["primary_dg"]) < -30.0]
        eligible = [row for row in cohort if _eligible(row)]
        by_family: dict[str, dict[str, Any]] = {}
        ordered = sorted(
            eligible,
            key=lambda item: (float(item["primary_dg"]), item["sequence_sha256"]),
        )
        for row in ordered:
            by_family.setdefault(str(row["family_key"]), row)
        representatives = list(by_family.values())
        for rank, row in enumerate(representatives[:50], start=1):
            selected.append({"target_key": target, "pool_a_rank": rank, **row})
        summaries[target] = {
            "rosetta_completed_candidate_count": len(cohort),
            "rosetta_dg_lt_minus_30_candidate_count": len(dg_pass),
            "strict_pool_a_candidate_count": len(eligible),
            "strict_pool_a_family_count": len(representatives),
            "pool_a_top50_filled": min(50, len(representatives)),
            "pool_a_family_gap_to_50": max(0, 50 - len(representatives)),
            "retained_conflict_family_count": len(
                {row["family_key"] for row in representatives if row["retained_conflict"]}
            ),
            "gate_pass_candidate_counts": {
                "display_eligible": sum(bool(row.get("display_eligible")) for row in cohort),
                "activity_support_gte_2": sum(
                    int(row.get("activity_support") or 0) >= 2 for row in cohort
                ),
                "formal_12_complete": sum(
                    int(row.get("formal_metric_count") or 0) == 12 for row in cohort
                ),
                "instability_lte_50": sum(
                    row.get("instability") is not None
                    and float(row["instability"]) <= 50.0
                    for row in cohort
                ),
                "toxinpred3_non_toxin": sum(bool(row.get("non_toxin")) for row in cohort),
                "macrel_hemolysis_low": sum(bool(row.get("macrel_low")) for row in cohort),
                "challenger_coverage": sum(
                    bool(row.get("hemopi2_covered"))
                    and bool(row.get("apex_covered"))
                    and bool(row.get("peptiverse_covered"))
                    for row in cohort
                ),
                "family_present": sum(bool(row.get("family_key")) for row in cohort),
                "rosetta_dg_lt_minus_30": len(dg_pass),
            },
        }
    return {
        "schema_version": "ampgent.pool-a-live-postgresql.1",
        "observed_at": datetime.now(UTC).isoformat(),
        "hard_gates": {
            "formal_metric_count": 12,
            "toxinpred3_label": "Non-Toxin",
            "macrel_hemolysis_label": "low",
            "guruprasad_instability_index_lte": 50,
            "activity_model_support_count_gte": 2,
            "challenger_coverage_required": True,
            "family_unique_80_80": True,
            "rosetta_primary_dg_reu_lt": -30,
            "accepted_nstruct": [5, 20, 200],
            "protocol_preference": "highest_nstruct_then_latest",
        },
        "targets": summaries,
        "pool_a_top50": selected,
    }


async def report() -> dict[str, Any]:
    async with SessionFactory() as session:
        records = (
            await session.execute(
                POOL_A_SQL,
                {
                    "formal_metrics": list(FORMAL_METRICS),
                    "accessions": list(ACCESSION_TO_TARGET),
                },
            )
        ).mappings().all()
    rows = []
    for record in records:
        row = dict(record)
        row["candidate_id"] = str(row["candidate_id"])
        row["run_id"] = str(row["run_id"])
        row["target_key"] = ACCESSION_TO_TARGET[row.pop("accession")]
        rows.append(row)
    return summarize(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.output, asyncio.run(report()))


if __name__ == "__main__":
    main()
