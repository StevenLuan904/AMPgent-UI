"""Verify exact Pool-A 50 ns MD and analysis closure across compact reports."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

SCHEMAS = {
    "summary": "ampgent.pool-a-md-summary.1",
    "gap": "ampgent.pool-a-md-gap-manifest.1",
    "contacts": "ampgent.pool-a-key-contact-occupancy.1",
    "residues": "ampgent.pool-a-peptide-residue-decomposition.1",
    "frontier": "ampgent.pool-s-provisional-md-pareto.2",
    "dossiers": "ampgent.pool-s-candidate-dossiers.1",
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
    return {
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


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in SCHEMAS:
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    result = verify(**{name: load(getattr(args, name)) for name in SCHEMAS})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    if not result["all_required_evidence_complete"] and not args.allow_incomplete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
