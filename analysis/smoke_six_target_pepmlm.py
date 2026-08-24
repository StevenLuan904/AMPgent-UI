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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--peptide-length", type=int, default=12)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    for index, target in enumerate(manifest["targets"]):
        request = {
            "target_sequence": target["sequence"],
            "peptide_length": args.peptide_length,
            "count": 1,
            "seed": 2026082400 + index,
            "model": str(args.model),
            "revision": args.revision,
            "top_k": 3,
            "temperature": 1.0,
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
                "pepagent.model_workers.pepmlm_cli",
                "--request",
                str(request_path),
                "--output",
                str(output_path),
            ],
            check=False,
            env=os.environ.copy(),
        )
        result = (
            json.loads(output_path.read_text(encoding="utf-8"))
            if output_path.exists()
            else None
        )
        summaries.append(
            {
                "target_key": target["target_key"],
                "target_protein_accession": target["protein_accession"],
                "target_sequence_length": target["sequence_length"],
                "target_sequence_sha256": target["sequence_sha256"],
                "returncode": completed.returncode,
                "generated_count": result.get("generated_count") if result else 0,
                "device": result.get("device") if result else None,
                "candidate": result["candidates"][0] if result and result["candidates"] else None,
                "request_sha256": _sha256(request_path),
                "output_sha256": _sha256(output_path) if output_path.exists() else None,
            }
        )
        if completed.returncode:
            break

    report = {
        "schema_version": "ampgent.six_target_pepmlm_smoke.v1",
        "manifest_sha256": _sha256(args.manifest),
        "model_path": str(args.model),
        "revision": args.revision,
        "gpu_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "all_succeeded": len(summaries) == len(manifest["targets"])
        and all(item["returncode"] == 0 for item in summaries),
        "targets": summaries,
    }
    report_path = args.output_dir / "summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report["all_succeeded"] else 1)


if __name__ == "__main__":
    main()
