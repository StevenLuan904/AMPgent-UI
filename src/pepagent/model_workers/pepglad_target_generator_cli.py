from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from pepagent.target_structure_generation import (
    TargetStructureGenerationRequest,
    collect_pepglad_proposals,
    sha256_file,
    write_pepglad_pocket,
)

PEPGLAD_SEEDED_LAUNCH = r"""
import random
import sys
import numpy as np
import torch
from api.run import design

seed = int(sys.argv[1])
random.seed(seed)
np.random.seed(seed)
torch.manual_seed(seed)
torch.cuda.manual_seed_all(seed)
design(
    mode="codesign",
    ckpt=sys.argv[2],
    gpu=int(sys.argv[3]),
    pdbs=[sys.argv[4]],
    epitope_defs=[sys.argv[5]],
    n_samples=[int(sys.argv[6])],
    out_dir=sys.argv[7],
    identifiers=[sys.argv[8]],
    lengths_range=[(int(sys.argv[9]), int(sys.argv[10]))],
)
"""


def _require_file_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(f"{label} sha256 mismatch: expected {expected}, got {observed}")


def run_pepglad(
    request: TargetStructureGenerationRequest,
    *,
    python_executable: Path,
    source_root: Path,
    checkpoint_path: Path,
    receptor_pdb: Path,
    run_dir: Path,
    gpu_index: int,
) -> dict[str, object]:
    if request.generator_id != "pepglad":
        raise ValueError("PepGLAD adapter received a different generator")
    if gpu_index < 0:
        raise ValueError("physical GPU index must be non-negative")
    if not python_executable.is_file():
        raise FileNotFoundError(python_executable)
    if not (source_root / "api" / "run.py").is_file():
        raise FileNotFoundError(source_root / "api" / "run.py")
    _require_file_hash(checkpoint_path, request.runtime.checkpoint_sha256, "PepGLAD checkpoint")
    _require_file_hash(receptor_pdb, request.target.structure_sha256, "target coordinate")
    if run_dir.exists():
        raise FileExistsError(f"PepGLAD run directory already exists: {run_dir}")
    output_dir = run_dir / "generated"
    pocket_path = run_dir / "pocket.json"
    run_dir.mkdir(parents=True)
    write_pepglad_pocket(request.target, pocket_path)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONHASHSEED": str(request.seed),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            # Constrain both PyTorch and PepGLAD's fractional Ray relaxation tasks
            # to the one physical GPU that the capacity guard authorized.
            "CUDA_VISIBLE_DEVICES": str(gpu_index),
        }
    )
    command = [
        str(python_executable),
        "-c",
        PEPGLAD_SEEDED_LAUNCH,
        str(request.seed),
        str(checkpoint_path),
        "0",
        str(receptor_pdb),
        str(pocket_path),
        str(request.requested_proposals),
        str(output_dir),
        f"{request.target.target_key}-pepglad-{request.seed}",
        str(request.peptide_length_min),
        str(request.peptide_length_max_exclusive),
    ]
    completed = subprocess.run(
        command,
        cwd=source_root,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    (run_dir / "launcher.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "launcher.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"PepGLAD launcher failed with exit code {completed.returncode}; "
            f"see {run_dir / 'launcher.stderr.log'}"
        )
    proposals = collect_pepglad_proposals(request, output_dir)
    return {
        "schema_version": "ampgent.target-structure-generator-result.1",
        "generator_id": request.generator_id,
        "target": request.target.model_dump(mode="json"),
        "runtime": request.runtime.model_dump(mode="json"),
        "seed": request.seed,
        "requested_proposals": request.requested_proposals,
        "raw_occurrence_count": len(proposals),
        "valid_sequence_count": sum(item.valid_sequence for item in proposals),
        "records": [item.model_dump(mode="json") for item in proposals],
        "pocket_definition_sha256": sha256_file(pocket_path),
        "internal_score_filtering_enabled": False,
        "all_raw_occurrences_retained": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--receptor-pdb", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--gpu-index", type=int, required=True)
    args = parser.parse_args()
    request = TargetStructureGenerationRequest.model_validate_json(
        args.request.read_text(encoding="utf-8")
    )
    result = run_pepglad(
        request,
        python_executable=args.python,
        source_root=args.source_root,
        checkpoint_path=args.checkpoint,
        receptor_pdb=args.receptor_pdb,
        run_dir=args.run_dir,
        gpu_index=args.gpu_index,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "generator_id": request.generator_id,
                "target_key": request.target.target_key,
                "raw_occurrence_count": result["raw_occurrence_count"],
            }
        )
    )


if __name__ == "__main__":
    main()
