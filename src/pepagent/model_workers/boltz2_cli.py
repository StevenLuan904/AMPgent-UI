import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


def build_input(request: dict[str, Any]) -> dict[str, Any]:
    target = request["target_sequence"]
    peptide = request["peptide_sequence"]
    target_payload: dict[str, Any] = {"id": "A", "sequence": target}
    if not request.get("use_msa_server", True):
        target_payload["msa"] = "empty"
    payload: dict[str, Any] = {
        "version": 1,
        "sequences": [
            {"protein": target_payload},
            {"protein": {"id": "B", "sequence": peptide, "msa": "empty"}},
        ],
    }
    pocket = request.get("pocket_residues") or []
    if pocket:
        payload["constraints"] = [
            {
                "pocket": {
                    "binder": "B",
                    "contacts": [["A", int(index)] for index in pocket],
                    "max_distance": float(request.get("pocket_max_distance", 8.0)),
                    "force": bool(request.get("force_pocket", False)),
                }
            }
        ]
    return payload


def pair_iptm(confidence: dict[str, Any]) -> float | None:
    matrix = confidence.get("pair_chains_iptm", {})
    for first, second in (("0", "1"), ("A", "B")):
        if first in matrix and second in matrix[first]:
            return float(matrix[first][second])
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    affinity_sentinel = args.cache_dir / "boltz2_aff.ckpt"
    if not affinity_sentinel.exists():
        affinity_sentinel.write_bytes(b"")
    if affinity_sentinel.stat().st_size != 0:
        raise RuntimeError(
            "Boltz affinity checkpoint is present, but peptide-chain affinity execution is "
            "forbidden in this adapter"
        )
    input_path = args.work_dir / "complex.yaml"
    prediction_dir = args.work_dir / "boltz-output"
    input_path.write_text(yaml.safe_dump(build_input(request), sort_keys=False), encoding="utf-8")

    boltz_executable = shutil.which("boltz")
    if boltz_executable is None:
        expected = Path(sys.executable).with_name("boltz")
        raise FileNotFoundError(
            f"Boltz executable is unavailable; expected a runnable console script at {expected}"
        )
    command = [
        boltz_executable,
        "predict",
        str(input_path),
        "--out_dir",
        str(prediction_dir),
        "--cache",
        str(args.cache_dir),
        "--diffusion_samples",
        str(int(request.get("diffusion_samples", 5))),
        "--recycling_steps",
        str(int(request.get("recycling_steps", 3))),
        "--sampling_steps",
        str(int(request.get("sampling_steps", 200))),
        "--seed",
        str(int(request["seed"])),
        "--write_full_pae",
        "--write_full_pde",
    ]
    if request.get("use_msa_server", True):
        command.append("--use_msa_server")
    if request.get("use_potentials", True):
        command.append("--use_potentials")
    if request.get("no_kernels", True):
        command.append("--no_kernels")
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"Boltz-2 failed ({completed.returncode}): {completed.stderr[-4000:]}")

    confidence_files = sorted(prediction_dir.rglob("confidence_*_model_0.json"))
    if not confidence_files:
        manifest_files = sorted(prediction_dir.rglob("manifest.json"))
        manifests = [
            path.read_text(encoding="utf-8", errors="replace")[-2000:]
            for path in manifest_files
        ]
        msa_errors = []
        for path in prediction_dir.rglob("out.tar.gz"):
            header = path.read_bytes()[:2]
            if header != b"\x1f\x8b":
                msa_errors.append(path.read_text(encoding="utf-8", errors="replace")[-2000:])
        raise FileNotFoundError(
            "Boltz-2 produced no confidence JSON; "
            f"manifests={manifests!r}; non_gzip_msa_responses={msa_errors!r}; "
            f"stdout_tail={completed.stdout[-2000:]!r}; stderr_tail={completed.stderr[-2000:]!r}"
        )
    confidence = json.loads(confidence_files[0].read_text(encoding="utf-8"))
    artifacts = [
        str(path.relative_to(args.work_dir))
        for path in prediction_dir.rglob("*")
        if path.is_file()
    ]
    result = {
        "schema_version": "1.0",
        "method": "boltz2",
        "seed": int(request["seed"]),
        "confidence_score": confidence.get("confidence_score"),
        "iptm": confidence.get("iptm"),
        "pair_iptm": pair_iptm(confidence),
        "complex_iplddt": confidence.get("complex_iplddt"),
        "artifacts": artifacts,
        "raw_confidence": confidence,
        "scope_note": (
            "structure confidence only; the Boltz-2 small-molecule affinity checkpoint is "
            "neither downloaded nor loaded"
        ),
        "affinity_head": "hard_disabled_for_peptide_chain",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
