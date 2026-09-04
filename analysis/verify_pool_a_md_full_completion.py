"""Verify exact Pool-A 50 ns MD and analysis closure across compact reports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from analysis.ingest_pool_a_md_evidence import (
    MD_RELEASE,
    MD_RELEASE_V2,
    MMGBSA_RELEASE,
)

SCHEMAS = {
    "summary": "ampgent.pool-a-md-summary.1",
    "gap": "ampgent.pool-a-md-gap-manifest.1",
    "contacts": "ampgent.pool-a-key-contact-occupancy.1",
    "residues": "ampgent.pool-a-peptide-residue-decomposition.1",
    "frontier": "ampgent.pool-s-provisional-md-pareto.2",
    "dossiers": "ampgent.pool-s-candidate-dossiers.1",
}

AUDIT_SCHEMA = "ampgent.pool-a-md-candidate-completion-audit.1"
REQUIRED_EVIDENCE = (
    "md_protocol",
    "interface_rmsd",
    "key_contact_occupancy",
    "hydrogen_bond_occupancy",
    "salt_bridge_occupancy",
    "water_bridge_occupancy",
    "peptide_departure",
    "mmgbsa_mean_ci",
    "residue_wise_decomposition",
    "postgresql_interface",
    "postgresql_mmgbsa",
)
INTERFACE_SCHEMAS = {
    "ampgent.pool-a-md-interface-analysis.2",
    "ampgent.pool-a-md-interface-analysis.3",
}
DECOMPOSITION_COLUMNS = (
    "mean_Internal",
    "mean_van der Waals",
    "mean_Electrostatic",
    "mean_Polar Solvation",
    "mean_Non-Polar Solv.",
    "mean_TOTAL",
)


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _fraction(value: object) -> bool:
    return _finite(value) and 0 <= float(value) <= 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _audit_item() -> dict:
    return {
        "complete": False,
        "gap_reasons": [],
        "provenance_strength": "not_verified",
        "limitations": [],
    }


def _add_reason(items: dict[str, dict], evidence: str, reason: str) -> None:
    items[evidence]["gap_reasons"].append(reason)


def _receipt_audit(
    path: Path,
    expected: dict,
    releases: set[str],
    local_files: dict[str, Path],
) -> tuple[bool, list[str], str, list[str]]:
    reasons: list[str] = []
    limitations: list[str] = []
    provenance_strength = "receipt_verified"
    receipt = _load_json(path)
    if receipt is None:
        return False, ["postgresql_ingest_receipt_missing_or_invalid"], "not_verified", []
    if str(receipt.get("candidate_id")) != str(expected["candidate_id"]):
        reasons.append("postgresql_ingest_candidate_identity_drift")
    if str(receipt.get("subject_run_id")) != str(expected["run_id"]):
        reasons.append("postgresql_ingest_run_identity_drift")
    if receipt.get("model_release_key") not in releases:
        reasons.append("postgresql_ingest_model_release_drift")
    try:
        UUID(str(receipt.get("tool_call_id")))
    except (ValueError, TypeError, AttributeError):
        reasons.append("postgresql_ingest_tool_call_id_missing_or_invalid")
    inserted = receipt.get("inserted_evaluation_count")
    already_complete = receipt.get("already_complete") is True
    if not already_complete:
        try:
            if int(inserted) <= 0:
                reasons.append("postgresql_ingest_has_no_persisted_evaluations")
        except (TypeError, ValueError):
            reasons.append("postgresql_ingest_evaluation_count_missing_or_invalid")
    files = receipt.get("files")
    if files is None:
        provenance_strength = "receipt_only_legacy_no_artifact_hashes"
        limitations.append("receipt_only_legacy_no_artifact_hashes")
    elif not isinstance(files, dict) or not files:
        reasons.append("postgresql_ingest_file_evidence_missing")
    else:
        for key, local in local_files.items():
            entry = files.get(key)
            if not isinstance(entry, dict) or not entry.get("sha256"):
                reasons.append(f"postgresql_ingest_file_evidence_missing:{key}")
                continue
            uri = str(entry.get("uri", "")).replace("\\", "/")
            expected_uri = f"/{expected['target_key']}/{expected['candidate_id']}/"
            if expected_uri not in uri:
                reasons.append(f"postgresql_ingest_artifact_uri_identity_drift:{key}")
            if not local.is_file():
                reasons.append(f"postgresql_ingest_local_artifact_missing:{key}")
                continue
            try:
                observed = _sha256(local)
            except OSError:
                reasons.append(f"postgresql_ingest_local_artifact_unreadable:{key}")
                continue
            if observed.casefold() != str(entry["sha256"]).casefold():
                reasons.append(f"postgresql_ingest_artifact_hash_mismatch:{key}")
    return not reasons, reasons, provenance_strength, limitations


def audit_candidate(row: dict, evidence_root: Path) -> dict:
    """Audit one exact run/candidate identity; every missing item stays pending."""
    expected = {
        "target_key": str(row.get("target_key", "")).casefold(),
        "run_id": str(row.get("run_id", "")),
        "candidate_id": str(row.get("candidate_id", "")),
        "sequence": row.get("sequence"),
        "sequence_sha256": row.get("sequence_sha256"),
    }
    checks = {name: _audit_item() for name in REQUIRED_EVIDENCE}
    root = evidence_root / expected["target_key"] / expected["candidate_id"]
    launch = _load_json(root / "launch_receipt.json")
    if launch is None:
        for name in REQUIRED_EVIDENCE:
            _add_reason(checks, name, "launch_receipt_missing_or_invalid")
    else:
        for key in ("target_key", "run_id", "candidate_id", "sequence_sha256"):
            if str(launch.get(key, "")).casefold() != str(expected.get(key, "")).casefold():
                for name in REQUIRED_EVIDENCE:
                    _add_reason(checks, name, f"launch_identity_drift:{key}")
                break

    manifest = _load_json(root / "manifest.json")
    if manifest is None:
        _add_reason(checks, "md_protocol", "md_manifest_missing_or_invalid")
    elif (
        manifest.get("schema_version") != "ampgent.pool-a-md.1"
        or manifest.get("status") != "succeeded"
        or not _finite(manifest.get("npt_ns"))
        or float(manifest["npt_ns"]) != 1.0
        or not _finite(manifest.get("production_ns"))
        or float(manifest["production_ns"]) != 50.0
    ):
        _add_reason(checks, "md_protocol", "md_protocol_not_exactly_1ns_npt_plus_50ns_nvt")
    else:
        checks["md_protocol"]["complete"] = True

    interface_path = root / "analysis/interface/interface_analysis.json"
    interface = _load_json(interface_path)
    schema = interface.get("schema_version") if interface else None
    if interface is None or schema not in INTERFACE_SCHEMAS:
        reason = "interface_analysis_missing_or_invalid"
        _add_reason(checks, "interface_rmsd", reason)
        for name in REQUIRED_EVIDENCE[2:7]:
            _add_reason(checks, name, reason)
    else:
        rmsd = interface.get("interface_rmsd_nm")
        if isinstance(rmsd, dict) and all(_finite(rmsd.get(key)) for key in ("mean", "maximum")):
            checks["interface_rmsd"]["complete"] = True
        else:
            _add_reason(
                checks,
                "interface_rmsd",
                "interface_rmsd_mean_or_maximum_missing_or_nonfinite",
            )

        contacts = interface.get("key_contacts")
        if isinstance(contacts, list) and contacts and all(
            isinstance(contact, dict) and _fraction(contact.get("occupancy"))
            for contact in contacts
        ):
            checks["key_contact_occupancy"]["complete"] = True
        else:
            _add_reason(checks, "key_contact_occupancy", "key_contact_occupancy_missing_or_invalid")

        if _fraction(interface.get("hydrogen_bond_occupancy")):
            if schema.endswith(".3"):
                network = interface.get("hydrogen_bond_network")
                network_ok = isinstance(network, dict) and all(
                    key in network
                    for key in (
                        "bond_count_per_frame",
                        "unique_atom_pair_count",
                        "unique_residue_pair_count",
                        "persistent_residue_pair_count",
                        "residue_pairs",
                        "atom_pairs",
                    )
                )
                hbond_timeseries = root / "analysis/interface/hydrogen_bond_timeseries.csv"
                if not network_ok:
                    _add_reason(
                        checks,
                        "hydrogen_bond_occupancy",
                        "interface_v3_hydrogen_bond_network_missing_or_incomplete",
                    )
                elif not hbond_timeseries.is_file():
                    _add_reason(
                        checks,
                        "hydrogen_bond_occupancy",
                        "interface_v3_hydrogen_bond_timeseries_missing",
                    )
                else:
                    checks["hydrogen_bond_occupancy"]["complete"] = True
            else:
                checks["hydrogen_bond_occupancy"]["complete"] = True
        else:
            _add_reason(
                checks,
                "hydrogen_bond_occupancy",
                "hydrogen_bond_occupancy_missing_or_invalid",
            )

        for name, key in (
            ("salt_bridge_occupancy", "salt_bridge_occupancy"),
            ("water_bridge_occupancy", "water_bridge_occupancy"),
        ):
            if _fraction(interface.get(key)):
                checks[name]["complete"] = True
            else:
                _add_reason(checks, name, f"{key}_missing_or_invalid")

        if (
            isinstance(interface.get("peptide_departed"), bool)
            and _finite(interface.get("maximum_departure_duration_ps"))
            and float(interface["maximum_departure_duration_ps"]) >= 0
            and _finite(interface.get("maximum_peptide_com_shift_nm"))
            and float(interface["maximum_peptide_com_shift_nm"]) >= 0
        ):
            checks["peptide_departure"]["complete"] = True
        else:
            _add_reason(checks, "peptide_departure", "peptide_departure_fields_missing_or_invalid")

    mmgbsa_path = root / "analysis/mmgbsa/mmgbsa_analysis.json"
    decomposition_path = root / "analysis/mmgbsa/residue_decomposition_mean.csv"
    mmgbsa = _load_json(mmgbsa_path)
    interval = mmgbsa.get("confidence_interval_95_kcal_mol") if mmgbsa else None
    if (
        mmgbsa is None
        or mmgbsa.get("schema_version") != "ampgent.pool-a-mmgbsa.1"
        or not _finite(mmgbsa.get("mean_binding_energy_kcal_mol"))
        or not isinstance(interval, list)
        or len(interval) != 2
        or not all(_finite(value) for value in interval)
        or float(interval[0]) > float(interval[1])
    ):
        _add_reason(
            checks,
            "mmgbsa_mean_ci",
            "mmgbsa_mean_or_confidence_interval_missing_or_invalid",
        )
    else:
        checks["mmgbsa_mean_ci"]["complete"] = True

    if not decomposition_path.is_file():
        _add_reason(checks, "residue_wise_decomposition", "residue_decomposition_file_missing")
    else:
        try:
            with decomposition_path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
            declared = int(mmgbsa.get("decomposition_residue_count", 0)) if mmgbsa else 0
            valid_rows = bool(rows) and all(
                all(column in row and _finite(row[column]) for column in DECOMPOSITION_COLUMNS)
                and row.get("residue")
                for row in rows
            )
            if declared <= 0 or len(rows) != declared:
                _add_reason(
                    checks,
                    "residue_wise_decomposition",
                    "residue_decomposition_row_count_mismatch",
                )
            elif not valid_rows:
                _add_reason(
                    checks,
                    "residue_wise_decomposition",
                    "residue_decomposition_row_or_energy_field_invalid",
                )
            else:
                checks["residue_wise_decomposition"]["complete"] = True
        except (OSError, UnicodeError, ValueError, csv.Error):
            _add_reason(
                checks,
                "residue_wise_decomposition",
                "residue_decomposition_unreadable_or_invalid",
            )

    interface_release = {MD_RELEASE_V2} if schema and schema.endswith(".2") else {MD_RELEASE}
    interface_files = {
        "manifest": root / "manifest.json",
        "interface_analysis": interface_path,
        "timeseries": root / "analysis/interface/timeseries.csv",
    }
    if schema and schema.endswith(".3"):
        interface_files["hydrogen_bond_timeseries"] = (
            root / "analysis/interface/hydrogen_bond_timeseries.csv"
        )
    ok, reasons, strength, limitations = _receipt_audit(
        root / "analysis/interface/postgresql_ingest_receipt.json",
        expected,
        interface_release,
        interface_files,
    )
    if ok:
        checks["postgresql_interface"]["complete"] = True
        checks["postgresql_interface"]["provenance_strength"] = strength
        checks["postgresql_interface"]["limitations"] = limitations
    else:
        for reason in reasons:
            _add_reason(checks, "postgresql_interface", reason)

    ok, reasons, strength, limitations = _receipt_audit(
        root / "analysis/mmgbsa/postgresql_ingest_receipt.json",
        expected,
        {MMGBSA_RELEASE},
        {
            "mmgbsa_analysis": mmgbsa_path,
            "residue_decomposition": decomposition_path,
        },
    )
    if ok:
        checks["postgresql_mmgbsa"]["complete"] = True
        checks["postgresql_mmgbsa"]["provenance_strength"] = strength
        checks["postgresql_mmgbsa"]["limitations"] = limitations
    else:
        for reason in reasons:
            _add_reason(checks, "postgresql_mmgbsa", reason)

    for item in checks.values():
        item["gap_reasons"] = list(dict.fromkeys(item["gap_reasons"]))
    gaps = [
        {"evidence": name, "reason": reason}
        for name, item in checks.items()
        for reason in item["gap_reasons"]
    ]
    limitations = sorted(
        {
            limitation
            for item in checks.values()
            for limitation in item["limitations"]
        }
    )
    science_checks = REQUIRED_EVIDENCE[1:9]
    if not checks["md_protocol"]["complete"]:
        stage = (
            "md_running_or_checkpoint_pending"
            if launch is not None
            else "not_launched"
        )
    elif not all(checks[name]["complete"] for name in science_checks):
        stage = "post_md_analysis_pending"
    elif not all(checks[name]["complete"] for name in REQUIRED_EVIDENCE[9:]):
        stage = "postgresql_ingest_pending"
    else:
        stage = "complete"
    return {
        "schema_version": AUDIT_SCHEMA,
        "target_key": expected["target_key"],
        "run_id": expected["run_id"],
        "candidate_id": expected["candidate_id"],
        "md_launched": launch is not None,
        "stage": stage,
        "full_evidence_complete": all(item["complete"] for item in checks.values()),
        "checks": checks,
        "gap_reasons": gaps,
        "limitations": limitations,
    }


def identity(row: dict) -> tuple[str, str]:
    return str(row["run_id"]), str(row["candidate_id"])


def identity_set(rows: list[dict], label: str) -> set[tuple[str, str]]:
    values = [identity(row) for row in rows]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate run/candidate identity in {label}")
    return set(values)


def verify(
    summary: dict,
    gap: dict,
    contacts: dict,
    residues: dict,
    frontier: dict,
    dossiers: dict,
    evidence_root: Path | None = None,
) -> dict:
    payloads = {
        "summary": summary,
        "gap": gap,
        "contacts": contacts,
        "residues": residues,
        "frontier": frontier,
        "dossiers": dossiers,
    }
    errors: list[str] = []
    for name, expected in SCHEMAS.items():
        observed = payloads[name].get("schema_version")
        if observed != expected:
            errors.append(f"{name}.schema_version={observed!r}; expected {expected!r}")

    overall = summary.get("overall", {})
    expected_count = int(overall.get("expected_candidate_count", -1))
    gap_rows = gap.get("candidates", [])
    expected_identities = identity_set(gap_rows, "gap manifest")
    complete_identities = identity_set(
        [row for row in gap_rows if row.get("stage") == "complete"],
        "complete gap manifest",
    )
    complete_count = len(complete_identities)

    count_fields = {
        "gap.candidate_count": gap.get("candidate_count"),
        "contacts.pool_a_candidate_count": contacts.get("pool_a_candidate_count"),
        "residues.pool_a_candidate_count": residues.get("pool_a_candidate_count"),
        "frontier.pool_a_candidate_count": frontier.get("pool_a_candidate_count"),
        "dossiers.pool_a_candidate_count": dossiers.get("pool_a_candidate_count"),
    }
    for label, value in count_fields.items():
        if int(value if value is not None else -1) != expected_count:
            errors.append(f"{label}={value!r}; expected {expected_count}")
    if len(expected_identities) != expected_count:
        errors.append(
            f"gap identity count={len(expected_identities)}; expected {expected_count}"
        )

    partial_count_fields = {
        "summary.md_complete_count": overall.get("md_complete_count"),
        "summary.interface_complete_count": overall.get("interface_complete_count"),
        "summary.mmgbsa_complete_count": overall.get("mmgbsa_complete_count"),
        "contacts.interface_and_postgresql_complete_count": contacts.get(
            "interface_and_postgresql_complete_count"
        ),
        "residues.decomposition_complete_count": residues.get(
            "decomposition_complete_count"
        ),
    }
    for label, value in partial_count_fields.items():
        observed = int(value if value is not None else -1)
        if not complete_count <= observed <= expected_count:
            errors.append(
                f"{label}={value!r}; expected between {complete_count} and {expected_count}"
            )

    complete_count_fields = {
        "summary.pool_s_evidence_complete_count": overall.get(
            "pool_s_evidence_complete_count"
        ),
        "summary.postgresql_evidence_complete_count": overall.get(
            "postgresql_evidence_complete_count"
        ),
        "frontier.md_and_postgresql_complete_count": frontier.get(
            "md_and_postgresql_complete_count"
        ),
        "dossiers.complete_dossier_count": dossiers.get("complete_dossier_count"),
    }
    for label, value in complete_count_fields.items():
        if int(value if value is not None else -1) != complete_count:
            errors.append(f"{label}={value!r}; expected {complete_count}")

    report_sets = {
        "contacts": identity_set(contacts.get("candidates", []), "contacts"),
        "residues": identity_set(residues.get("candidates", []), "residues"),
        "dossiers": identity_set(dossiers.get("dossiers", []), "dossiers"),
    }
    identity_mismatches: dict[str, dict[str, int]] = {}
    for name, observed in report_sets.items():
        missing = complete_identities - observed
        unexpected = observed - expected_identities
        partial = observed - complete_identities
        identity_mismatches[name] = {
            "missing_complete_identity_count": len(missing),
            "unexpected_identity_count": len(unexpected),
            "valid_partial_identity_count": len(partial - unexpected),
        }
        if missing or unexpected:
            errors.append(
                f"{name} identity mismatch: missing={len(missing)}, "
                f"unexpected={len(unexpected)}"
            )

    issue_count = sum(int(value) for value in gap.get("issue_counts", {}).values())
    if issue_count:
        errors.append(f"gap manifest has {issue_count} consistency issues")
    if frontier.get("weighted_total_used") is not False:
        errors.append("Pool-S frontier must not use a weighted total")

    pending_count = expected_count - complete_count
    fully_complete = not errors and expected_count > 0 and pending_count == 0
    candidate_audits: list[dict] = []
    audit_gap_identities: dict[str, set[tuple[str, str]]] = {}
    if evidence_root is not None:
        for row in gap_rows:
            audit = audit_candidate(row, evidence_root)
            candidate_audits.append(audit)
            for item in audit["gap_reasons"]:
                reason = str(item["reason"])
                audit_gap_identities.setdefault(reason, set()).add(identity(audit))
        audited_complete = {
            identity(item): item
            for item in candidate_audits
            if item["full_evidence_complete"]
        }
        audited_ids = set(audited_complete)
        if audited_ids != complete_identities:
            errors.append(
                "candidate audit disagrees with gap manifest: "
                f"audit_complete={len(audited_ids)}, gap_complete={len(complete_identities)}"
            )
        complete_count = len(audited_ids)
        pending_count = expected_count - complete_count
        fully_complete = not errors and expected_count > 0 and pending_count == 0

    result = {
        "schema_version": "ampgent.pool-a-md-full-completion-verification.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "status": "complete" if fully_complete else "in_progress",
        "expected_candidate_count": expected_count,
        "complete_candidate_count": complete_count,
        "pending_candidate_count": pending_count,
        "all_required_evidence_complete": fully_complete,
        "cross_report_identity_mismatches": identity_mismatches,
        "consistency_error_count": len(errors),
        "consistency_errors": errors,
        "completion_definition": (
            "exact run/candidate identity has 1 ns NPT + 50 ns NVT, interface RMSD/contacts/"
            "hydrogen-bond/salt-bridge/water-bridge/departure evidence, MM/GBSA mean+95% CI, "
            "residue decomposition, and both PostgreSQL evaluation receipts"
        ),
    }
    if evidence_root is not None:
        result.update(
            {
                "candidate_audit_schema_version": AUDIT_SCHEMA,
                "candidate_audit_source": str(evidence_root.resolve()),
                "postgresql_verification": "receipt_only; live_database_verification_unavailable",
                "large_artifact_local_cache_required": False,
                "candidate_audits": candidate_audits,
                "audit_stage_counts": {
                    stage: sum(item["stage"] == stage for item in candidate_audits)
                    for stage in sorted({item["stage"] for item in candidate_audits})
                },
                "gap_reason_counts": {
                    reason: len(identities)
                    for reason, identities in sorted(audit_gap_identities.items())
                },
                "gap_evidence_counts": {
                    name: sum(not item["checks"][name]["complete"] for item in candidate_audits)
                    for name in REQUIRED_EVIDENCE
                },
            }
        )
    return result


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in SCHEMAS:
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    result = verify(
        **{name: load(getattr(args, name)) for name in SCHEMAS},
        evidence_root=args.evidence_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    temporary.replace(args.output)
    if not result["all_required_evidence_complete"] and not args.allow_incomplete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
