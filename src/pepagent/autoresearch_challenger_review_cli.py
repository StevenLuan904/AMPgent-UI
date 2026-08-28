from __future__ import annotations

import argparse
import json
from pathlib import Path

from pepagent.autoresearch_challenger_review import (
    run_challenger_review,
    write_challenger_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run and freeze the HemoPI2 shadow review for a structure cohort"
    )
    parser.add_argument("--structure-cohort-dir", type=Path, required=True)
    parser.add_argument("--structure-cohort-receipt-sha256", required=True)
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--inference", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--calibration-sha256", required=True)
    parser.add_argument("--ood-witness", type=Path, required=True)
    parser.add_argument("--ood-witness-sha256", required=True)
    parser.add_argument("--lineage-witness", type=Path, required=True)
    parser.add_argument("--lineage-witness-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    bundle, worker_output = run_challenger_review(
        structure_cohort_dir=args.structure_cohort_dir.resolve(),
        structure_cohort_receipt_sha256=args.structure_cohort_receipt_sha256,
        runtime_python=args.runtime_python.resolve(),
        worker_path=args.worker.resolve(),
        inference_path=args.inference.resolve(),
        model_root=args.model_root.resolve(),
        calibration_path=args.calibration.resolve(),
        calibration_sha256=args.calibration_sha256,
        ood_witness_path=args.ood_witness.resolve(),
        ood_witness_sha256=args.ood_witness_sha256,
        lineage_witness_path=args.lineage_witness.resolve(),
        lineage_witness_sha256=args.lineage_witness_sha256,
        source_root=args.source_root.resolve(),
        scratch_root=args.scratch_root.resolve(),
    )
    result = write_challenger_bundle(
        bundle=bundle,
        worker_output=worker_output,
        output_root=args.output_root.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
