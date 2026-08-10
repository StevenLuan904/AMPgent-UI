from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pepagent.generator_structure_report import (
    V31B_COHORT_COLUMNS,
    render_csv,
    select_v31b_confirmation_cohort,
    sha256_bytes,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze the preregistered v31b cohort")
    parser.add_argument("--phase-a-report", type=Path, required=True)
    parser.add_argument("--cohort-output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()
    with args.phase_a_report.open(encoding="utf-8", newline="") as handle:
        phase_a_rows = list(csv.DictReader(handle))
    selected, audit = select_v31b_confirmation_cohort(phase_a_rows)
    cohort_payload = render_csv(selected, V31B_COHORT_COLUMNS)
    audit_payload = (json.dumps(audit, sort_keys=True, indent=2) + "\n").encode("utf-8")
    args.cohort_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.cohort_output.write_bytes(cohort_payload)
    args.audit_output.write_bytes(audit_payload)
    print(
        json.dumps(
            {
                "cohort_path": str(args.cohort_output),
                "cohort_sha256": sha256_bytes(cohort_payload),
                "audit_path": str(args.audit_output),
                "audit_sha256": sha256_bytes(audit_payload),
                "selected_count": len(selected),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
