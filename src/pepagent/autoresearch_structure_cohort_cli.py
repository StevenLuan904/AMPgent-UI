from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from pepagent.autoresearch_structure_cohort import (
    DEFAULT_STRUCTURE_ESCALATION_COUNT,
    freeze_structure_escalation_cohort,
    iter_cohort_csv_rows,
)
from pepagent.provenance.hashing import sha256_file


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def freeze(args: argparse.Namespace) -> dict[str, object]:
    cohort = freeze_structure_escalation_cohort(
        strict_library_path=args.strict_library.resolve(),
        strict_library_sha256=args.strict_library_sha256,
        bundle_receipt_path=args.bundle_receipt.resolve(),
        bundle_receipt_sha256=args.bundle_receipt_sha256,
        target_manifest_path=args.target_manifest.resolve(),
        target_manifest_sha256=args.target_manifest_sha256,
        pocket_catalog_path=args.pocket_catalog.resolve(),
        pocket_catalog_sha256=args.pocket_catalog_sha256,
        per_target_count=args.per_target_count,
    )
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / cohort.cohort_sha256
    if final_dir.exists():
        raise FileExistsError(f"structure cohort already exists: {final_dir}")
    temporary_dir = output_root / f".{cohort.cohort_sha256}.tmp-{os.getpid()}"
    if temporary_dir.exists():
        raise FileExistsError(f"structure cohort temporary directory exists: {temporary_dir}")
    temporary_dir.mkdir()
    cohort_path = temporary_dir / "structure_escalation_cohort.json"
    csv_path = temporary_dir / "structure_escalation_cohort.csv"
    audit_path = temporary_dir / "structure_escalation_audit.json"
    receipt_path = temporary_dir / "freeze.receipt.json"
    payload = cohort.model_dump(mode="json", exclude_computed_fields=True)
    payload["cohort_sha256"] = cohort.cohort_sha256
    payload["selected_count"] = cohort.selected_count
    _write_json(cohort_path, payload)
    csv_rows = list(iter_cohort_csv_rows(cohort))
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    audit = {
        "schema_version": "ampgent.structure-escalation-audit.1",
        "cohort_sha256": cohort.cohort_sha256,
        "selected_count": cohort.selected_count,
        "target_counts": {
            target.target_key: {
                "eligible_candidate_count": target.eligible_candidate_count,
                "eligible_family_count": target.eligible_family_count,
                "excluded_instability_ood_count": target.excluded_instability_ood_count,
                "selected_count": len(target.selected),
                "structure_evidence_mode": target.qualification.structure_evidence_mode,
                "pocket_evidence_grade": target.qualification.pocket_evidence_grade,
            }
            for target in cohort.target_cohorts
        },
    }
    _write_json(audit_path, audit)
    files = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in (cohort_path, csv_path, audit_path)
    }
    receipt = {
        "schema_version": "ampgent.structure-escalation-freeze-receipt.1",
        "status": "frozen",
        "cohort_sha256": cohort.cohort_sha256,
        "selected_count": cohort.selected_count,
        "per_target_count": cohort.per_target_requested_count,
        "files": files,
        "source": cohort.source.model_dump(mode="json"),
        "no_binding_or_affinity_claim": True,
        "minimum_rosetta_decoys_per_completed_candidate": 200,
    }
    _write_json(receipt_path, receipt)
    receipt_sha256 = sha256_file(receipt_path)
    temporary_dir.replace(final_dir)
    return {
        "status": "frozen",
        "output_dir": str(final_dir),
        "cohort_sha256": cohort.cohort_sha256,
        "selected_count": cohort.selected_count,
        "receipt_sha256": receipt_sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze six family-diverse AutoResearch structure-escalation cohorts"
    )
    parser.add_argument("--strict-library", type=Path, required=True)
    parser.add_argument("--strict-library-sha256", required=True)
    parser.add_argument("--bundle-receipt", type=Path, required=True)
    parser.add_argument("--bundle-receipt-sha256", required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--target-manifest-sha256", required=True)
    parser.add_argument("--pocket-catalog", type=Path, required=True)
    parser.add_argument("--pocket-catalog-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--per-target-count", type=int, default=DEFAULT_STRUCTURE_ESCALATION_COUNT)
    args = parser.parse_args()
    print(json.dumps(freeze(args), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
