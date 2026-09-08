from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import gemmi


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_SNAPSHOT = ROOT / "public" / "data" / "launch-analysis.snapshot.json"
OUTPUT = ROOT / "public" / "data" / "candidate-case.snapshot.json"
API_BASE = os.environ.get("AMPGENT_API_BASE", "http://127.0.0.1:8081").rstrip("/")


def fetch_json(path: str) -> Any:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(path: str) -> str:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    request = Request(url, headers={"Accept": "chemical/x-cif, chemical/x-pdb, text/plain"})
    with urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8")


def stable_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def context_matches(call: dict[str, Any], sequence: str) -> bool:
    return any(item.get("candidate_sequence") == sequence for item in call.get("structure_context", []))


def artifact_json(call: dict[str, Any], role: str) -> dict[str, Any] | None:
    artifact = next((item for item in call.get("artifacts", []) if item.get("role") == role), None)
    if not artifact:
        return None
    try:
        value = fetch_json(artifact["url"])
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, KeyError):
        return None


def rosetta_scores(call: dict[str, Any]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for artifact in call.get("artifacts", []):
        if artifact.get("media_type") != "application/json" or not str(artifact.get("role", "")).startswith("engine_output_"):
            continue
        try:
            value = fetch_json(artifact["url"])
        except (OSError, ValueError, KeyError):
            continue
        if not isinstance(value, dict) or "dG_separated" not in value:
            continue
        rows.append({
            key: float(value[key])
            for key in (
                "dG_separated",
                "dSASA_int",
                "interface_hbonds",
                "packstat",
                "peptide_score",
                "total_score",
            )
            if value.get(key) is not None
        })
    return rows


def load_cif_structure(text: str) -> gemmi.Structure:
    return gemmi.make_structure_from_block(gemmi.cif.read_string(text).sole_block())


def polymer_sequence(chain: gemmi.Chain) -> str:
    return chain.get_polymer().make_one_letter_sequence()


def residue_plddt_and_contacts(text: str, peptide_sequence: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    structure = load_cif_structure(text)
    model = structure[0]
    peptide_chain = next((chain for chain in model if polymer_sequence(chain) == peptide_sequence), None)
    if peptide_chain is None:
        raise RuntimeError("结构文件中未找到候选短肽链")
    target_chain = max((chain for chain in model if chain.name != peptide_chain.name), key=lambda chain: len(polymer_sequence(chain)))

    peptide_residues = list(peptide_chain.get_polymer())
    target_residues = list(target_chain.get_polymer())
    plddt = []
    for position, residue in enumerate(peptide_residues, start=1):
        atom_values = [float(atom.b_iso) for atom in residue if atom.element.name != "H"]
        plddt.append({
            "position": position,
            "residue": peptide_sequence[position - 1],
            "value": round(sum(atom_values) / len(atom_values), 2) if atom_values else None,
        })

    peptide_atoms = [[atom for atom in residue if atom.element.name != "H"] for residue in peptide_residues]
    target_atoms = [[atom for atom in residue if atom.element.name != "H"] for residue in target_residues]
    distances: list[list[float]] = []
    for peptide_row in peptide_atoms:
        row = []
        for target_row in target_atoms:
            minimum = min((left.pos.dist(right.pos) for left in peptide_row for right in target_row), default=20.0)
            row.append(float(minimum))
        distances.append(row)

    target_minimums = [min((row[index] for row in distances), default=20.0) for index in range(len(target_residues))]
    selected_indices = [index for index, value in enumerate(target_minimums) if value <= 8.0]
    if len(selected_indices) > 32:
        selected_indices = sorted(selected_indices, key=lambda index: target_minimums[index])[:32]
    selected_indices.sort(key=lambda index: target_residues[index].seqid.num)

    contact_map = {
        "distanceThresholdAngstrom": 5.0,
        "source": "boltz_native_pose_heavy_atom_minimum_distance",
        "peptideResidues": [
            {"position": index + 1, "residue": peptide_sequence[index]}
            for index in range(len(peptide_residues))
        ],
        "targetResidues": [
            {
                "position": int(target_residues[index].seqid.num),
                "residue": target_residues[index].name,
                "closestDistance": round(target_minimums[index], 2),
            }
            for index in selected_indices
        ],
        "distances": [
            [round(row[index], 2) for index in selected_indices]
            for row in distances
        ],
    }
    return plddt, contact_map


def composition_context(analysis: dict[str, Any], candidate_sequence: str) -> list[dict[str, Any]]:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    eligible_sequences = [
        item["sequence"] for item in analysis["candidates"]
        if item.get("admission", {}).get("structureEligible")
    ]
    background_sequences = [item["sequence"] for item in analysis["candidates"]]
    candidate_counts = Counter(candidate_sequence)
    eligible_counts = Counter("".join(eligible_sequences))
    background_counts = Counter("".join(background_sequences))
    eligible_total = sum(eligible_counts.values())
    background_total = sum(background_counts.values())
    return [
        {
            "residue": residue,
            "candidateCount": candidate_counts[residue],
            "candidateFraction": round(candidate_counts[residue] / len(candidate_sequence), 5),
            "structureEligibleFraction": round(eligible_counts[residue] / eligible_total, 5),
            "backgroundFraction": round(background_counts[residue] / background_total, 5),
            "log2Enrichment": round(math.log2(
                ((eligible_counts[residue] + 0.5) / (eligible_total + 0.5 * len(alphabet)))
                / ((background_counts[residue] + 0.5) / (background_total + 0.5 * len(alphabet)))
            ), 4),
        }
        for residue in alphabet
    ]


def main() -> None:
    analysis = json.loads(ANALYSIS_SNAPSHOT.read_text(encoding="utf-8"))
    run_id = analysis["run"]["id"]
    detail = fetch_json(f"/v1/observer/runs/{run_id}")
    viewer = detail.get("viewer")
    if not viewer:
        raise RuntimeError("发布轮次没有可读取的结构案例")

    candidate = next(
        item for item in analysis["candidates"]
        if item["id"] == viewer["candidate_id"] or item["sequence"] == viewer["sequence"]
    )
    sequence = candidate["sequence"]

    metric_directions = {
        "macrel_amp_probability": "higher",
        "llamp_log10_mic_um": "lower",
        "macrel_hemolysis_probability": "lower",
        "toxinpred3_hybrid_score": "lower",
    }
    metric_context: dict[str, dict[str, Any]] = {}
    for metric_key, direction in metric_directions.items():
        values = [
            item.get("metrics", {}).get(metric_key, {}).get("value")
            for item in analysis["candidates"]
        ]
        values = [float(value) for value in values if isinstance(value, (int, float))]
        current_value = float(candidate["metrics"][metric_key]["value"])
        favorable_count = sum(
            value <= current_value if direction == "higher" else value >= current_value
            for value in values
        )
        metric_context[metric_key] = {
            "value": current_value,
            "favorablePercentile": round(favorable_count / len(values) * 100),
            "cohortSize": len(values),
            "direction": direction,
        }

    target_rows = []
    for branch in detail.get("branches", []):
        pocket_payload = fetch_json(f"/v1/targets/by-accession/{branch['accession']}/pockets")
        target_rows.append({
            "order": branch["order"],
            "name": branch["target_name"],
            "organism": branch["organism"],
            "accession": branch["accession"],
            "sequence": branch["sequence"],
            "sequenceLength": branch["sequence_length"],
            "status": branch["status"],
            "pockets": [
                {
                    "name": pocket["name"],
                    "type": pocket["type"],
                    "functionalRole": pocket["functional_role"],
                    "evidenceGrade": pocket["evidence_grade"],
                    "evidenceScore": pocket["evidence_score"],
                    "conditioningPriority": pocket["conditioning_priority"],
                    "conditioningEnabled": pocket["conditioning_enabled"],
                    "residueIndices": pocket["residue_indices"],
                    "limitations": pocket["limitations"],
                    "evidence": [
                        {
                            "sourceType": evidence["source_type"],
                            "sourceAccession": evidence["source_accession"],
                            "confidence": evidence["confidence"],
                            "method": evidence["experimental_method"],
                            "resolutionAngstrom": evidence["resolution_angstrom"],
                        }
                        for evidence in pocket.get("evidence", [])
                    ],
                }
                for pocket in pocket_payload.get("pockets", [])
            ],
        })

    boltz_node = fetch_json(f"/v1/observer/runs/{run_id}/nodes/boltz")
    rosetta_node = fetch_json(f"/v1/observer/runs/{run_id}/nodes/rosetta")
    boltz_calls = [call for call in boltz_node.get("calls", []) if context_matches(call, sequence)]
    rosetta_calls = [call for call in rosetta_node.get("calls", []) if context_matches(call, sequence)]

    boltz_runs = []
    contact_map: dict[str, Any] | None = None
    for call in boltz_calls:
        confidence = artifact_json(call, "raw_output") or {}
        context = call["structure_context"][0]
        structure_artifact = next(
            (artifact for artifact in call.get("artifacts", []) if artifact.get("media_type") == "chemical/x-cif"),
            None,
        )
        residue_plddt: list[dict[str, Any]] = []
        if structure_artifact:
            structure_text = fetch_text(structure_artifact["url"])
            residue_plddt, run_contact_map = residue_plddt_and_contacts(structure_text, sequence)
            if contact_map is None and context.get("lane") == "native":
                contact_map = run_contact_map
        boltz_runs.append({
            "seed": call.get("random_seed"),
            "target": context.get("target"),
            "lane": context.get("lane"),
            "confidenceScore": confidence.get("confidence_score"),
            "complexPlddt": (confidence.get("raw_confidence") or {}).get("complex_plddt"),
            "iptm": confidence.get("iptm"),
            "pairIptm": confidence.get("pair_iptm"),
            "ptm": (confidence.get("raw_confidence") or {}).get("ptm"),
            "residuePlddt": residue_plddt,
            "artifact": {
                "candidate_id": candidate["id"],
                "sequence": sequence,
                "target_id": viewer["target_id"],
                "target_name": context.get("target"),
                "lane": context.get("lane"),
                "seed": call.get("random_seed"),
                "artifact_sha256": structure_artifact["sha256"],
                "media_type": structure_artifact["media_type"],
                "artifact_url": structure_artifact["url"],
            } if structure_artifact else None,
        })

    rosetta_runs = []
    for call in rosetta_calls:
        context = call["structure_context"][0]
        scores = rosetta_scores(call)
        structure_artifact = next(
            (artifact for artifact in call.get("artifacts", []) if artifact.get("media_type") == "chemical/x-pdb"),
            None,
        )
        rosetta_runs.append({
            "seed": call.get("random_seed"),
            "target": context.get("target"),
            "lane": context.get("lane"),
            "decoyCount": context.get("records"),
            "scores": scores,
            "artifact": {
                "candidate_id": candidate["id"],
                "sequence": sequence,
                "target_id": viewer["target_id"],
                "target_name": context.get("target"),
                "lane": context.get("lane"),
                "seed": call.get("random_seed"),
                "artifact_sha256": structure_artifact["sha256"],
                "media_type": structure_artifact["media_type"],
                "artifact_url": structure_artifact["url"],
            } if structure_artifact else None,
        })

    observed_events = [
        event for event in detail.get("events", [])
        if event.get("type") == "v38.multitarget_structure.persisted"
        and event.get("payload", {}).get("candidate_id") == candidate["id"]
    ]
    observed_poses = sum(int(event["payload"].get("boltz_pose_count", 0)) for event in observed_events)
    observed_decoys = sum(int(event["payload"].get("rosetta_decoy_count", 0)) for event in observed_events)

    payload: dict[str, Any] = {
        "schemaVersion": "ampgent-candidate-case.1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "postgresql_read_only_with_frozen_metrics",
        "run": {
            "id": run_id,
            "status": detail["run"]["status"],
            "updatedAt": detail["updated_at"],
        },
        "candidate": {
            "id": candidate["id"],
            "sequence": sequence,
            "originSet": candidate["originSet"],
            "proposalRank": candidate["proposalRank"],
            "admission": candidate["admission"],
            "metrics": candidate["metrics"],
            "metricContext": metric_context,
            "compositionContext": composition_context(analysis, sequence),
        },
        "targets": target_rows,
        "structure": {
            "boltzRuns": boltz_runs,
            "rosettaRuns": rosetta_runs,
            "contactMap": contact_map,
            "coverage": {
                "plannedBoltzPoses": len(target_rows) * 2 * 3,
                "observedBoltzPoses": observed_poses,
                "plannedRosettaDecoys": len(target_rows) * 2 * 3 * 16,
                "observedRosettaDecoys": observed_decoys,
            },
        },
        "review": {
            "status": "not_formed",
            "reason": "run_cancelled_before_final_portfolio",
            "candidateDecisionAvailable": True,
            "finalPortfolioAvailable": False,
        },
    }
    payload["transportSha256"] = stable_digest(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
