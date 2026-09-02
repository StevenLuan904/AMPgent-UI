"""Ingest completed Pool-A MD and MM/GBSA evidence into PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from pepagent.db.models import Candidate, Evaluation, ExperimentRun, Target
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_json

MD_RELEASE = "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_v1"
MMGBSA_RELEASE = "ambertools26_mmgbsa_igb5_sparse_v1"
TARGET_BY_ACCESSION = {
    "P0A9G6": "acea",
    "NP_416734.1": "gyra",
    "WP_308061015.1": "pbp2a",
    "NP_001020421.2": "vegfa",
    "NP_032032.1": "fgf2",
    "NP_001272991.1": "angpt1",
}


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=120)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def marker_matches(path: Path, evidence: dict[str, Any]) -> bool:
    if not path.is_file():
        return False
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        marker.get("model_release_key") == evidence["release"]
        and marker.get("files") == evidence["files"]
    )


def finite(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} is not finite")
    return result


def identity(candidate: Path) -> dict[str, str]:
    launch = json.loads((candidate / "launch_receipt.json").read_text(encoding="utf-8"))
    return {
        "candidate_id": str(uuid.UUID(str(launch["candidate_id"]))),
        "run_id": str(uuid.UUID(str(launch["run_id"]))),
        "target_key": str(launch["target_key"]).casefold(),
        "sequence_sha256": str(launch["sequence_sha256"]).lower(),
    }


def md_evidence(candidate: Path) -> dict[str, Any] | None:
    manifest_path = candidate / "manifest.json"
    analysis_path = candidate / "analysis/interface/interface_analysis.json"
    if not manifest_path.is_file() or not analysis_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "ampgent.pool-a-md.1":
        raise ValueError("unexpected MD manifest schema")
    if manifest.get("status") != "succeeded":
        raise ValueError("MD manifest is not successful")
    if finite(manifest.get("npt_ns"), "npt_ns") != 1.0:
        raise ValueError("MD NPT duration is not 1 ns")
    if finite(manifest.get("production_ns"), "production_ns") != 50.0:
        raise ValueError("MD production duration is not 50 ns")
    if analysis.get("schema_version") != "ampgent.pool-a-md-interface-analysis.1":
        raise ValueError("unexpected interface-analysis schema")
    values = {
        "md_interface_rmsd_mean_nm": finite(
            analysis["interface_rmsd_nm"]["mean"], "interface RMSD mean"
        ),
        "md_interface_rmsd_max_nm": finite(
            analysis["interface_rmsd_nm"]["maximum"], "interface RMSD maximum"
        ),
        "md_native_contact_fraction_mean": finite(
            analysis["native_contact_fraction"]["mean"], "contact fraction mean"
        ),
        "md_native_contact_fraction_min": finite(
            analysis["native_contact_fraction"]["minimum"], "contact fraction minimum"
        ),
        "md_hydrogen_bond_occupancy": finite(
            analysis["hydrogen_bond_occupancy"], "hydrogen-bond occupancy"
        ),
        "md_salt_bridge_occupancy": finite(
            analysis["salt_bridge_occupancy"], "salt-bridge occupancy"
        ),
        "md_water_bridge_occupancy": finite(
            analysis["water_bridge_occupancy"], "water-bridge occupancy"
        ),
        "md_key_contact_count": float(len(analysis.get("key_contacts", []))),
        "md_peptide_departed": float(bool(analysis["peptide_departed"])),
        "md_maximum_departure_duration_ps": finite(
            analysis["maximum_departure_duration_ps"], "departure duration"
        ),
        "md_maximum_peptide_com_shift_nm": finite(
            analysis["maximum_peptide_com_shift_nm"], "peptide COM shift"
        ),
    }
    units = {
        "md_interface_rmsd_mean_nm": "nm",
        "md_interface_rmsd_max_nm": "nm",
        "md_native_contact_fraction_mean": "fraction",
        "md_native_contact_fraction_min": "fraction",
        "md_hydrogen_bond_occupancy": "fraction",
        "md_salt_bridge_occupancy": "fraction",
        "md_water_bridge_occupancy": "fraction",
        "md_key_contact_count": "residue_pairs",
        "md_peptide_departed": "boolean",
        "md_maximum_departure_duration_ps": "ps",
        "md_maximum_peptide_com_shift_nm": "nm",
    }
    return {
        "identity": identity(candidate),
        "release": MD_RELEASE,
        "family": "molecular_dynamics_interface",
        "tool_name": "pool-a-md-interface-ingest",
        "tool_version": "2026.09.03-v1",
        "files": {
            "manifest": {"uri": str(manifest_path), "sha256": sha256_file(manifest_path)},
            "interface_analysis": {
                "uri": str(analysis_path),
                "sha256": sha256_file(analysis_path),
            },
            "timeseries": {
                "uri": str(analysis_path.parent / "timeseries.csv"),
                "sha256": sha256_file(analysis_path.parent / "timeseries.csv"),
            },
        },
        "values": values,
        "units": units,
        "raw": {
            "protocol": manifest,
            "definitions": analysis.get("definitions", {}),
            "frame_count": int(analysis["frame_count"]),
            "interaction_sample_count": int(analysis["interaction_sample_count"]),
            "key_contacts": analysis.get("key_contacts", []),
        },
        "marker": analysis_path.parent / "postgresql_ingest_receipt.json",
    }


def mmgbsa_evidence(candidate: Path) -> dict[str, Any] | None:
    result_path = candidate / "analysis/mmgbsa/mmgbsa_analysis.json"
    decomposition = candidate / "analysis/mmgbsa/residue_decomposition_mean.csv"
    if not result_path.is_file() or not decomposition.is_file():
        return None
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("schema_version") != "ampgent.pool-a-mmgbsa.1":
        raise ValueError("unexpected MM/GBSA schema")
    interval = result["confidence_interval_95_kcal_mol"]
    values = {
        "mmgbsa_binding_energy_mean_kcal_mol": finite(
            result["mean_binding_energy_kcal_mol"], "MM/GBSA mean"
        ),
        "mmgbsa_binding_energy_ci95_lower_kcal_mol": finite(interval[0], "CI lower"),
        "mmgbsa_binding_energy_ci95_upper_kcal_mol": finite(interval[1], "CI upper"),
        "mmgbsa_residue_decomposition_count": finite(
            result["decomposition_residue_count"], "decomposition residue count"
        ),
    }
    return {
        "identity": identity(candidate),
        "release": MMGBSA_RELEASE,
        "family": "binding_energy_postprocessing",
        "tool_name": "pool-a-mmgbsa-ingest",
        "tool_version": "2026.09.03-v1",
        "files": {
            "mmgbsa_analysis": {"uri": str(result_path), "sha256": sha256_file(result_path)},
            "residue_decomposition": {
                "uri": str(decomposition),
                "sha256": sha256_file(decomposition),
            },
        },
        "values": values,
        "units": {
            "mmgbsa_binding_energy_mean_kcal_mol": "kcal/mol",
            "mmgbsa_binding_energy_ci95_lower_kcal_mol": "kcal/mol",
            "mmgbsa_binding_energy_ci95_upper_kcal_mol": "kcal/mol",
            "mmgbsa_residue_decomposition_count": "residues",
        },
        "raw": result,
        "marker": result_path.parent / "postgresql_ingest_receipt.json",
    }


async def persist(evidence: dict[str, Any], source_commit: str) -> dict[str, Any]:
    item_identity = evidence["identity"]
    candidate_id = uuid.UUID(item_identity["candidate_id"])
    run_id = uuid.UUID(item_identity["run_id"])
    environment_sha = sha256_json(
        {"source_commit": source_commit, "model_release_key": evidence["release"]}
    )
    async with SessionFactory() as session, session.begin():
        candidate_row = (
            await session.execute(
                select(Candidate, Target.accession)
                .join(ExperimentRun, ExperimentRun.id == Candidate.run_id)
                .join(Target, Target.id == ExperimentRun.target_id)
                .where(Candidate.id == candidate_id)
            )
        ).one_or_none()
        if candidate_row is None:
            raise ValueError("candidate is absent from PostgreSQL")
        candidate, accession = candidate_row
        if (
            candidate.run_id != run_id
            or candidate.sequence_sha256 != item_identity["sequence_sha256"]
            or TARGET_BY_ACCESSION.get(accession) != item_identity["target_key"]
        ):
            raise ValueError("candidate PostgreSQL identity drifted")
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            evidence["tool_name"],
            evidence["tool_version"],
            environment_sha,
            {"candidate_id": str(candidate_id), "files": evidence["files"]},
            {
                "model_release_key": evidence["release"],
                "database_binding": "subject_run_id+candidate_id+model_release_key",
            },
            {"metric_count": len(evidence["values"]), "files": evidence["files"]},
        )
        rows = []
        for metric, value in evidence["values"].items():
            details = dict(evidence["raw"])
            if metric != "md_key_contact_count":
                details.pop("key_contacts", None)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "candidate_id": candidate_id,
                    "tool_call_id": call.id,
                    "subject_run_id": run_id,
                    "evidence_role": "structure_validation",
                    "evidence_family": evidence["family"],
                    "model_release_key": evidence["release"],
                    "applicability_status": "applicable",
                    "conflict_status": "not_assessed",
                    "metric_name": metric,
                    "numeric_value": value,
                    "text_value": (
                        str(bool(value)).lower() if metric == "md_peptide_departed" else None
                    ),
                    "unit": evidence["units"][metric],
                    "status": "succeeded",
                    "out_of_domain": False,
                    "limitations_json": evidence["raw"].get("limitations", []),
                    "raw_json": {"files": evidence["files"], "details": details},
                }
            )
        result = await session.execute(
            insert(Evaluation)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["candidate_id", "metric_name", "tool_call_id"])
        )
    return {
        "candidate_id": str(candidate_id),
        "subject_run_id": str(run_id),
        "model_release_key": evidence["release"],
        "tool_call_id": str(call.id),
        "inserted_evaluation_count": max(result.rowcount or 0, 0),
    }


async def scan_once(root: Path, source_commit: str) -> dict[str, Any]:
    result = {"observed_at": datetime.now(UTC).isoformat(), "ingested": [], "failures": []}
    candidates = await asyncio.to_thread(
        lambda: sorted(path.parent for path in root.glob("*/*/manifest.json"))
    )
    for candidate in candidates:
        for loader in (md_evidence, mmgbsa_evidence):
            try:
                evidence = await asyncio.to_thread(loader, candidate)
                if evidence is None or await asyncio.to_thread(
                    marker_matches, evidence["marker"], evidence
                ):
                    continue
                receipt = await persist(evidence, source_commit)
                receipt["ingested_at"] = datetime.now(UTC).isoformat()
                receipt["files"] = evidence["files"]
                await asyncio.to_thread(write_json, evidence["marker"], receipt)
                result["ingested"].append(receipt)
            except Exception as error:
                result["failures"].append(
                    {"candidate": str(candidate), "loader": loader.__name__, "error": str(error)}
                )
    return result


async def main() -> None:
    args = cli()
    if len(args.source_commit) != 40:
        raise ValueError("source commit must be a full Git SHA-1")
    while True:
        state = await scan_once(args.root, args.source_commit)
        await asyncio.to_thread(write_json, args.root / "postgresql_ingester_state.json", state)
        if args.once:
            return
        await asyncio.sleep(args.poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
