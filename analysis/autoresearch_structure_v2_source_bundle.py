from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json

TARGET_ORDER = ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
SOURCE_SCHEMA = "ampgent.structure-v2-strict-source.1"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> None:
    with args.selected_csv.open(encoding="utf-8-sig", newline="") as stream:
        selected = list(csv.DictReader(stream))
    if len(selected) != 300:
        raise ValueError("structure v2 source requires exactly 300 selected candidates")
    selected.sort(
        key=lambda row: (
            TARGET_ORDER.index(row["branch_key"]),
            float(row["calibrated_hemolysis_probability"]),
            row["sequence_sha256"],
        )
    )
    if any(
        sum(row["branch_key"] == branch for row in selected) != 50
        for branch in TARGET_ORDER
    ):
        raise ValueError("structure v2 source requires exactly 50 candidates per target")
    if len({row["sequence_sha256"] for row in selected}) != 300:
        raise ValueError("structure v2 source sequences are not globally unique")
    if len({row["family_key_80_80"] for row in selected}) != 300:
        raise ValueError("structure v2 source families are not globally unique")

    source_result_sha256 = sha256_file(args.qualified_csv)
    rows: list[dict[str, Any]] = []
    for ordinal, row in enumerate(selected, start=2):
        rows.append(
            {
                "source_row_ordinal": ordinal,
                "source_candidate_id": row["candidate_id"],
                "sequence": row["sequence"],
                "sequence_sha256": row["sequence_sha256"],
                "family_key_80_80": row["family_key_80_80"],
                "target_key": row["branch_key"],
                "strict_display_eligible": True,
                "valid_sequence": True,
                "toxinpred3_label": row["toxinpred3_label"],
                "macrel_hemolysis_label": row["macrel_hemolysis_label"],
                "guruprasad_instability_index": float(
                    row["guruprasad_instability_index"]
                ),
                "guruprasad_instability_ood": len(row["sequence"]) < 20,
                "activity_model_support_count": int(
                    row["activity_model_support_count_calibrated"]
                ),
                "source_result_sha256": source_result_sha256,
            }
        )
    identity = {
        "schema_version": SOURCE_SCHEMA,
        "selected_csv_sha256": sha256_file(args.selected_csv),
        "qualified_csv_sha256": source_result_sha256,
        "candidate_identities": [
            {
                "target_key": row["target_key"],
                "sequence_sha256": row["sequence_sha256"],
                "family_key_80_80": row["family_key_80_80"],
            }
            for row in rows
        ],
    }
    content_address_key = sha256_json(identity)
    output_dir = args.output_dir.resolve()
    library_path = output_dir / "library" / "strict_library_global.csv"
    _write_csv(library_path, rows)
    strict_library_sha256 = sha256_file(library_path)
    created_at = datetime.now(UTC).isoformat()
    bundle_receipt = {
        "schema_version": "ampgent.autoresearch-structure-v2-source-bundle.1",
        "content_address_key": content_address_key,
        "bundle_run_id": args.bundle_run_id,
        "bundle_created_at": created_at,
        "selected_csv_sha256": sha256_file(args.selected_csv),
        "qualified_csv_sha256": source_result_sha256,
        "strict_library_sha256": strict_library_sha256,
        "strict_library_size_bytes": library_path.stat().st_size,
        "strict_library_row_count": len(rows),
        "temporal_submission_performed": False,
        "gpu_task_submitted": False,
    }
    bundle_receipt_path = output_dir / "bundle.receipt.json"
    bundle_receipt_path.write_text(
        json.dumps(bundle_receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = output_dir / "MANIFEST.sha256"
    manifest_path.write_text(
        "\n".join(
            (
                f"{strict_library_sha256}  library/strict_library_global.csv",
                f"{sha256_file(bundle_receipt_path)}  bundle.receipt.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    storage_uri = (
        args.remote_base_uri.rstrip("/") + f"/{content_address_key}/"
    )
    source_document = {
        "schema_version": SOURCE_SCHEMA,
        "source": {
            "content_address_key": content_address_key,
            "bundle_run_id": args.bundle_run_id,
            "bundle_created_at": created_at,
            "bundle_storage_uri": storage_uri,
            "bundle_receipt_sha256": sha256_file(bundle_receipt_path),
            "bundle_receipt_size_bytes": bundle_receipt_path.stat().st_size,
            "manifest_sha256": sha256_file(manifest_path),
            "manifest_size_bytes": manifest_path.stat().st_size,
            "strict_library_sha256": strict_library_sha256,
            "strict_library_size_bytes": library_path.stat().st_size,
        },
        "rows": rows,
    }
    source_path = output_dir / "structure_v2_source.json"
    source_path.write_text(
        json.dumps(source_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    build_receipt = {
        "schema_version": "ampgent.autoresearch-structure-v2-source-build.1",
        "built_at_utc": datetime.now(UTC).isoformat(),
        "content_address_key": content_address_key,
        "bundle_storage_uri": storage_uri,
        "candidate_count": len(rows),
        "per_target_count": 50,
        "globally_distinct_family_count": len(
            {row["family_key_80_80"] for row in rows}
        ),
        "source_document_sha256": sha256_file(source_path),
        "strict_library_sha256": strict_library_sha256,
        "bundle_receipt_sha256": sha256_file(bundle_receipt_path),
        "manifest_sha256": sha256_file(manifest_path),
        "remote_publish_status": "not_started",
        "pg_reservation_status": "not_started",
        "temporal_submission_performed": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    build_receipt["receipt_payload_sha256"] = sha256_json(build_receipt)
    (output_dir / "build_receipt.json").write_text(
        json.dumps(build_receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected-csv", type=Path, required=True)
    parser.add_argument("--qualified-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bundle-run-id", required=True)
    parser.add_argument("--remote-base-uri", required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
