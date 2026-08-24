from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--release-sha256", required=True)
    parser.add_argument("--probe-sequence", default="GRWRQWKWWWKELHHVLLDDDELL")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    for target in manifest["targets"]:
        peptide_sha = _sequence_sha256(args.probe_sequence)
        request = {
            "target": {
                "target_key": target["target_key"],
                "accession": target["protein_accession"],
                "sequence": target["sequence"],
                "sequence_sha256": target["sequence_sha256"],
            },
            "peptides": [
                {
                    "candidate_id": "historical-stability-probe",
                    "sequence": args.probe_sequence,
                    "sequence_sha256": peptide_sha,
                }
            ],
            "model": str(args.model),
            "revision": args.revision,
        }
        request_path = args.output_dir / f"{target['target_key']}.request.json"
        output_path = args.output_dir / f"{target['target_key']}.result.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(args.python),
                "-m",
                "pepagent.model_workers.pepmlm_target_conditional_cli",
                "--request",
                str(request_path),
                "--output",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            env=os.environ.copy(),
            text=True,
        )
        result = (
            json.loads(output_path.read_text(encoding="utf-8"))
            if output_path.exists()
            else None
        )
        scored = result["results"][0] if result and result.get("results") else None
        summaries.append(
            {
                "target_key": target["target_key"],
                "target_protein_accession": target["protein_accession"],
                "target_sequence_length": target["sequence_length"],
                "target_sequence_sha256": target["sequence_sha256"],
                "returncode": completed.returncode,
                "conditional_nll": scored.get("conditional_nll") if scored else None,
                "conditional_ppl": scored.get("conditional_ppl") if scored else None,
                "device": result.get("device") if result else None,
                "request_sha256": _sha256(request_path),
                "output_sha256": _sha256(output_path) if output_path.exists() else None,
                "stderr_tail": completed.stderr[-2000:],
            }
        )
        if completed.returncode:
            break

    report = {
        "schema_version": "ampgent.six-target-conditional-smoke.1",
        "source_revision": args.source_revision,
        "release_sha256": args.release_sha256,
        "manifest_sha256": _sha256(args.manifest),
        "model_path": str(args.model),
        "revision": args.revision,
        "python": str(args.python),
        "gpu_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "probe_sequence_sha256": _sequence_sha256(args.probe_sequence),
        "same_executable_subprocess": True,
        "all_succeeded": len(summaries) == len(manifest["targets"])
        and all(
            item["returncode"] == 0
            and item["device"] == "cuda"
            and item["conditional_ppl"] is not None
            for item in summaries
        ),
        "targets": summaries,
    }
    report_path = args.output_dir / "summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report["all_succeeded"] else 1)


if __name__ == "__main__":
    main()
