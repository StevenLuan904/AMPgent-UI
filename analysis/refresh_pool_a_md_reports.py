"""Refresh every compact Pool-A MD and provisional Pool-S report in dependency order."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def commands(
    snapshot: Path,
    evidence_root: Path,
    output_dir: Path,
    python: str = sys.executable,
) -> list[list[str]]:
    scripts = Path(__file__).resolve().parent
    candidates = output_dir / "candidates.csv"
    contacts = output_dir / "key_contact_occupancy.json"
    residues = output_dir / "peptide_residue_decomposition.json"
    frontier = output_dir / "pool_s_provisional_frontier.json"
    dossiers = output_dir / "pool_s_candidate_dossiers.json"
    gap = output_dir / "completion_gap_manifest.json"
    return [
        [
            python,
            str(scripts / "summarize_pool_a_md_results.py"),
            "--snapshot",
            str(snapshot),
            "--evidence-root",
            str(evidence_root),
            "--output-dir",
            str(output_dir),
        ],
        [
            python,
            str(scripts / "summarize_pool_a_key_contacts.py"),
            "--candidates",
            str(candidates),
            "--evidence-root",
            str(evidence_root),
            "--output",
            str(contacts),
            "--contact-csv",
            str(output_dir / "key_contact_occupancy.csv"),
        ],
        [
            python,
            str(scripts / "summarize_pool_a_residue_decomposition.py"),
            "--candidates",
            str(candidates),
            "--evidence-root",
            str(evidence_root),
            "--output",
            str(residues),
            "--residue-csv",
            str(output_dir / "peptide_residue_decomposition.csv"),
        ],
        [
            python,
            str(scripts / "analyze_pool_s_frontier.py"),
            "--candidates",
            str(candidates),
            "--output",
            str(frontier),
            "--frontier-csv",
            str(output_dir / "pool_s_provisional_frontier.csv"),
        ],
        [
            python,
            str(scripts / "build_pool_s_candidate_dossiers.py"),
            "--candidates",
            str(candidates),
            "--contacts",
            str(contacts),
            "--decomposition",
            str(residues),
            "--frontier",
            str(frontier),
            "--output",
            str(dossiers),
        ],
        [
            python,
            str(scripts / "build_pool_a_md_gap_manifest.py"),
            "--candidates",
            str(candidates),
            "--output",
            str(gap),
        ],
        [
            python,
            str(scripts / "analyze_rosetta_md_concordance.py"),
            "--candidates",
            str(candidates),
            "--output",
            str(output_dir / "rosetta_md_concordance.json"),
        ],
        [
            python,
            str(scripts / "verify_pool_a_md_full_completion.py"),
            "--summary",
            str(output_dir / "summary.json"),
            "--gap",
            str(gap),
            "--contacts",
            str(contacts),
            "--residues",
            str(residues),
            "--frontier",
            str(frontier),
            "--dossiers",
            str(dossiers),
            "--output",
            str(output_dir / "full_completion_verification.json"),
            "--allow-incomplete",
        ],
    ]


def subprocess_environment() -> dict[str, str]:
    """Keep sibling analysis modules importable when scripts run by absolute path."""
    environment = os.environ.copy()
    repository_root = str(Path(__file__).resolve().parent.parent)
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        repository_root if not current else os.pathsep.join((repository_root, current))
    )
    return environment


def refresh(snapshot: Path, evidence_root: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline = commands(snapshot, evidence_root, output_dir)
    environment = subprocess_environment()
    for command in pipeline:
        subprocess.run(command, check=True, env=environment)
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    verification = json.loads(
        (output_dir / "full_completion_verification.json").read_text(encoding="utf-8")
    )
    result = {
        "schema_version": "ampgent.pool-a-md-report-refresh.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "command_count": len(pipeline),
        "expected_candidate_count": summary["overall"]["expected_candidate_count"],
        "complete_candidate_count": verification["complete_candidate_count"],
        "pending_candidate_count": verification["pending_candidate_count"],
        "verification_status": verification["status"],
        "consistency_error_count": verification["consistency_error_count"],
    }
    receipt = output_dir / "refresh_receipt.json"
    temporary = receipt.with_name(f".{receipt.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(receipt)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    refresh(args.snapshot, args.evidence_root, args.output_dir)


if __name__ == "__main__":
    main()
