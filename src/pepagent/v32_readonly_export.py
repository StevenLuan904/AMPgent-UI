from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.db.models import Artifact, Candidate, Evaluation, EvidenceArtifact, ToolCall
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_file
from pepagent.storage.object_store import ContentAddressedObjectStore

V32_RUN_ID = uuid.UUID("d695853e-cb94-4608-ad71-e4d7c4df1e85")
PORTFOLIO_SHA256 = "d50b0b77e8e04f86f6b8d48fa3bc24f9d96a43aa9016a315f4004cca0db6d0e3"
CLAIM_LIMITATIONS = (
    "computational predictions only; MIC values are model estimates; membrane descriptors "
    "are physicochemical proxies; Macrel hemolysis and ToxinPred3 general toxicity are "
    "different endpoints and are not experimental safety evidence; no AceA binding or affinity "
    "claim; lane membership and order are frozen from v32"
)

CSV_FIELDS = (
    "portfolio_order",
    "lane",
    "lane_rank",
    "candidate_id",
    "sequence",
    "sequence_sha256",
    "seed",
    "macrel_amp_probability",
    "llamp_predicted_mic_um",
    "amp_read_predicted_mic_um",
    "net_charge_ph7_4",
    "hydrophobic_ratio_modlamp",
    "hydrophobic_moment_eisenberg",
    "maximum_hydrophobic_run",
    "isoelectric_point",
    "macrel_hemolysis_probability",
    "macrel_hemolysis_label",
    "toxinpred3_ml_score",
    "toxinpred3_hybrid_score",
    "toxinpred3_label",
    "activity_mic_family_depth",
    "membrane_family_depth",
    "risk_control_family_depth",
    "claim_scope",
    "limitations",
)


def _same_number(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-8)


async def reconstruct_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reconstruct the frozen v32 portfolio from PostgreSQL and its object bytes."""
    async with SessionFactory() as session:
        artifact = await session.scalar(
            select(Artifact)
            .join(EvidenceArtifact, EvidenceArtifact.artifact_id == Artifact.id)
            .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
            .where(
                ToolCall.run_id == V32_RUN_ID,
                EvidenceArtifact.role == "portfolio_output",
                Artifact.sha256 == PORTFOLIO_SHA256,
            )
        )
        if artifact is None:
            raise ValueError("locked v32 portfolio artifact is absent from PostgreSQL")
        raw = await asyncio.to_thread(
            ContentAddressedObjectStore().get_bytes, artifact.storage_uri
        )
        if sha256_bytes(raw) != PORTFOLIO_SHA256:
            raise OSError("locked v32 portfolio object checksum mismatch")
        portfolio = json.loads(raw)
        lane_results = portfolio.get("lane_results")
        if not isinstance(lane_results, list) or len(lane_results) != 24:
            raise ValueError("locked v32 portfolio does not contain exactly 24 members")
        candidate_ids = [uuid.UUID(item["candidate_id"]) for item in lane_results]
        candidates = list(
            await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == V32_RUN_ID, Candidate.id.in_(candidate_ids)
                )
            )
        )
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        )

    candidate_by_id = {str(item.id): item for item in candidates}
    if set(candidate_by_id) != {str(item) for item in candidate_ids}:
        raise ValueError("v32 portfolio candidate identity set differs from PostgreSQL")
    numeric_by_candidate: dict[str, dict[str, float]] = {}
    text_by_candidate: dict[str, dict[str, str]] = {}
    for evaluation in evaluations:
        candidate_id = str(evaluation.candidate_id)
        if evaluation.numeric_value is not None:
            metrics = numeric_by_candidate.setdefault(candidate_id, {})
            if evaluation.metric_name in metrics:
                raise ValueError("ambiguous v32 numeric evaluation in PostgreSQL")
            metrics[evaluation.metric_name] = evaluation.numeric_value
        if evaluation.text_value is not None:
            labels = text_by_candidate.setdefault(candidate_id, {})
            if evaluation.metric_name in labels:
                raise ValueError("ambiguous v32 label evaluation in PostgreSQL")
            labels[evaluation.metric_name] = evaluation.text_value

    rows: list[dict[str, Any]] = []
    for portfolio_order, item in enumerate(lane_results, start=1):
        candidate_id = str(item["candidate_id"])
        candidate = candidate_by_id[candidate_id]
        if (
            candidate.sequence != item["sequence"]
            or candidate.sequence_sha256 != item["sequence_sha256"]
        ):
            raise ValueError("v32 portfolio candidate sequence differs from PostgreSQL")
        db_metrics = numeric_by_candidate.get(candidate_id, {})
        db_labels = text_by_candidate.get(candidate_id, {})
        for name, value in item["metrics"].items():
            if name not in db_metrics or not _same_number(db_metrics[name], value):
                raise ValueError(f"v32 portfolio metric differs from PostgreSQL: {name}")
        for name, value in item["labels"].items():
            if db_labels.get(name) != value:
                raise ValueError(f"v32 portfolio label differs from PostgreSQL: {name}")
        metrics = item["metrics"]
        labels = item["labels"]
        depths = item["family_depths"]
        rows.append(
            {
                "portfolio_order": portfolio_order,
                "lane": item["lane"],
                "lane_rank": item["lane_rank"],
                "candidate_id": candidate_id,
                "sequence": item["sequence"],
                "sequence_sha256": item["sequence_sha256"],
                "seed": item["seed"],
                "macrel_amp_probability": metrics["macrel_amp_probability"],
                "llamp_predicted_mic_um": metrics["llamp_predicted_mic_um"],
                "amp_read_predicted_mic_um": metrics["amp_read_predicted_mic_um"],
                "net_charge_ph7_4": metrics["net_charge_ph7_4"],
                "hydrophobic_ratio_modlamp": metrics["hydrophobic_ratio_modlamp"],
                "hydrophobic_moment_eisenberg": metrics[
                    "hydrophobic_moment_eisenberg"
                ],
                "maximum_hydrophobic_run": metrics["maximum_hydrophobic_run"],
                "isoelectric_point": metrics["isoelectric_point"],
                "macrel_hemolysis_probability": metrics[
                    "macrel_hemolysis_probability"
                ],
                "macrel_hemolysis_label": labels["macrel_hemolysis_label"],
                "toxinpred3_ml_score": metrics["toxinpred3_ml_score"],
                "toxinpred3_hybrid_score": metrics["toxinpred3_hybrid_score"],
                "toxinpred3_label": labels["toxinpred3_label"],
                "activity_mic_family_depth": depths["activity_mic"],
                "membrane_family_depth": depths["membrane"],
                "risk_control_family_depth": depths["risk_control"],
                "claim_scope": item["claim_scope"],
                "limitations": CLAIM_LIMITATIONS,
            }
        )
    provenance = {
        "source": "read_only_PostgreSQL_plus_content_addressed_object_store",
        "v32_run_id": str(V32_RUN_ID),
        "portfolio_artifact_sha256": PORTFOLIO_SHA256,
        "portfolio_artifact_storage_uri": artifact.storage_uri,
        "portfolio_policy": portfolio["policy"],
        "portfolio_selected_count": portfolio["selected_count"],
        "portfolio_order_preserved": True,
        "selection_changed": False,
        "database_written": False,
        "new_scientific_result": False,
    }
    return rows, provenance


def _write_markdown(path: Path, rows: list[dict[str, Any]], provenance: dict[str, Any]) -> None:
    lines = [
        "# v32 冻结候选组合（只读导出）",
        "",
        "这是 v32 已锁定 24 条 portfolio 的只读视图，不是新筛选、新排序、新计算结果或实验结果。",
        f"来源 run：`{provenance['v32_run_id']}`；portfolio SHA：`{PORTFOLIO_SHA256}`。",
        "",
        "|序号|lane|rank|candidate ID|sequence|AMP prob|LLAMP MIC μM|AMP-READ MIC μM|"
        "charge pH7.4|hydrophobic moment|Macrel hemolysis|ToxinPred3|",
        "|---:|---|---:|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "|{portfolio_order}|{lane}|{lane_rank}|`{candidate_id}`|`{sequence}`|"
            "{macrel_amp_probability:.3f}|{llamp_predicted_mic_um:.3f}|"
            "{amp_read_predicted_mic_um:.3f}|{net_charge_ph7_4:.3f}|"
            "{hydrophobic_moment_eisenberg:.3f}|{macrel_hemolysis_label} "
            "({macrel_hemolysis_probability:.3f})|{toxinpred3_label} "
            "(ML {toxinpred3_ml_score:.3f})|".format(**row)
        )
    lines.extend(
        [
            "",
            "限制：所有活性、MIC、膜作用和风险字段均为计算预测或理化 proxy；Macrel 溶血与 "
            "ToxinPred3 一般毒性不是同一终点，也不是实验安全证据；本导出不支持 AceA 结合或"
            "亲和力声明。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


async def export(output_prefix: Path) -> dict[str, str]:
    rows, provenance = await reconstruct_rows()
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_prefix.with_suffix(".csv")
    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "provenance": provenance,
                "limitations": CLAIM_LIMITATIONS,
                "candidates": rows,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_markdown(markdown_path, rows, provenance)
    hashes = {
        path.name: sha256_file(path) for path in (csv_path, json_path, markdown_path)
    }
    hash_path = output_prefix.with_suffix(".sha256")
    hash_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
        newline="\n",
    )
    hashes[hash_path.name] = sha256_file(hash_path)
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(export(args.output_prefix)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
