from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from pepagent.generator_structure_validation import (
    GeneratorStructureScreenManifest,
    freeze_balanced_structure_cohort,
    write_frozen_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze the preregistered v31 structure cohort")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest = GeneratorStructureScreenManifest.model_validate(payload)
    if manifest.execution_status != "preregistered":
        raise ValueError("cohort freeze is allowed only from preregistered status")
    base_dir = manifest_path.parent
    rows, audit = freeze_balanced_structure_cohort(manifest, base_dir)
    cohort_path = (base_dir / manifest.output_cohort_path).resolve()
    audit_path = (base_dir / manifest.output_audit_path).resolve()
    cohort_sha256, audit_sha256 = write_frozen_outputs(
        rows, audit, cohort_path, audit_path
    )
    print(
        json.dumps(
            {
                "cohort_path": str(cohort_path),
                "cohort_sha256": cohort_sha256,
                "audit_path": str(audit_path),
                "audit_sha256": audit_sha256,
                "selected_count": len(rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
