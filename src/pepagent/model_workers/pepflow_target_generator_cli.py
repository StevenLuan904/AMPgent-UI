from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from pepagent.target_structure_generation import (
    TargetStructureGenerationRequest,
    collect_pepflow_proposals,
    sha256_file,
    write_pepflow_case,
)

PEPFLOW_SEEDED_LAUNCH = r'''
import builtins
import gc
import io
import json
import sys
import types
from copy import deepcopy
from pathlib import Path

import torch

# The pinned model imports torch_scatter through a borrowed utility module but
# never calls it. Keep that import explicit: if the dead dependency becomes live,
# fail rather than silently changing its numerical behavior.
try:
    import torch_scatter  # noqa: F401
except ModuleNotFoundError:
    torch_scatter = types.ModuleType('torch_scatter')
    def unavailable_torch_scatter(*args, **kwargs):
        raise RuntimeError('PepFlow invoked its previously unused torch_scatter dependency')
    torch_scatter.scatter = unavailable_torch_scatter
    torch_scatter.scatter_add = unavailable_torch_scatter
    sys.modules['torch_scatter'] = torch_scatter

seed = int(sys.argv[1])
checkpoint = Path(sys.argv[2])
device = sys.argv[3]
config_path = sys.argv[4]
manifest_path = Path(sys.argv[5])
output_dir = Path(sys.argv[6])
num_steps = int(sys.argv[7])
batch_size = int(sys.argv[8])

# The pinned upstream source opens an author-local names.txt during module import.
# It is only a training/test exclusion list, so make that one unavailable path an
# empty stream without modifying the pinned checkout.
real_open = builtins.open
blocked_names_path = (
    '/datapool/data2/home/ruihan/data/jiahan/ResProj/PepDiff/'
    'pepflowww/Data/names.txt'
)
def patched_open(file, *args, **kwargs):
    if str(file) == blocked_names_path:
        return io.StringIO('')
    return real_open(file, *args, **kwargs)
builtins.open = patched_open
try:
    from models_con.pep_dataloader import preprocess_structure
    from models_con.flow_model import FlowModel
finally:
    builtins.open = real_open

from models_con.torsion import full_atom_reconstruction, get_heavyatom_mask
from models_con.utils import process_dic
from pepflow.modules.protein.writers import save_pdb
from pepflow.utils.data import PaddingCollate
from pepflow.utils.misc import load_config, seed_all
from pepflow.utils.train import recursive_to

seed_all(seed)
config, _ = load_config(config_path)
model = FlowModel(config.model).to(device)
# The official model2 artifact is a full trusted checkpoint containing an
# EasyDict config; PyTorch 2.6 otherwise changes this legacy call to weights-only.
state = torch.load(checkpoint, map_location=device, weights_only=False)
model.load_state_dict(process_dic(state['model']))
model.eval()
collate = PaddingCollate(eight=False)
cases = json.loads(manifest_path.read_text(encoding='utf-8'))
output_dir.mkdir(parents=True, exist_ok=False)
summary_path = output_dir / 'sequences.jsonl'
alphabet = 'ACDEFGHIKLMNPQRSTVWY'
raw_rank = 0

with summary_path.open('w', encoding='utf-8') as summary:
    for case in cases:
        item = preprocess_structure({'id': case['case_id'], 'pdb_path': case['case_dir']})
        if item is None:
            raise RuntimeError(f"PepFlow rejected prepared case {case['case_id']}")
        remaining = int(case['proposal_count'])
        while remaining:
            count = min(batch_size, remaining)
            batch = recursive_to(collate([deepcopy(item) for _ in range(count)]), device)
            final = model.sample(
                batch,
                num_steps=num_steps,
                sample_bb=True,
                sample_ang=True,
                sample_seq=True,
            )[-1]
            batch_cpu = recursive_to(batch, 'cpu')
            pos_heavy, _, _ = full_atom_reconstruction(
                R_bb=final['rotmats'],
                t_bb=final['trans'],
                angles=final['angles'],
                aa=final['seqs'],
            )
            pos_heavy = torch.nn.functional.pad(pos_heavy, pad=(0, 0, 0, 1), value=0.0)
            generated = batch_cpu['generate_mask']
            pos_new = torch.where(
                generated[:, :, None, None], pos_heavy, batch_cpu['pos_heavyatom']
            )
            generated_atom_mask = get_heavyatom_mask(final['seqs'])
            mask_new = torch.where(
                generated[:, :, None], generated_atom_mask, batch_cpu['mask_heavyatom']
            )
            for sample_index in range(count):
                raw_rank += 1
                structure_name = f'sample_{raw_rank:04d}.pdb'
                chain_ids = [row[sample_index] for row in batch_cpu['chain_id']]
                insertion_codes = [row[sample_index] for row in batch_cpu['icode']]
                save_pdb(
                    {
                        'chain_nb': batch_cpu['chain_nb'][sample_index],
                        'chain_id': chain_ids,
                        'resseq': batch_cpu['resseq'][sample_index],
                        'icode': insertion_codes,
                        'aa': final['seqs'][sample_index],
                        'mask_heavyatom': mask_new[sample_index],
                        'pos_heavyatom': pos_new[sample_index],
                    },
                    path=str(output_dir / structure_name),
                )
                sequence = ''.join(
                    alphabet[int(value)]
                    for value in final['seqs'][sample_index][generated[sample_index]]
                )
                summary.write(json.dumps({
                    'raw_rank': raw_rank,
                    'sequence': sequence,
                    'peptide_length': len(sequence),
                    'structure_file_name': structure_name,
                    'case_id': case['case_id'],
                }) + '\n')
            remaining -= count
            del batch, batch_cpu, final, pos_heavy, pos_new, mask_new
            torch.cuda.empty_cache()
            gc.collect()
'''


def _require_file_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(f"{label} sha256 mismatch: expected {expected}, got {observed}")


def run_pepflow(
    request: TargetStructureGenerationRequest,
    *,
    python_executable: Path,
    source_root: Path,
    checkpoint_path: Path,
    receptor_pdb: Path,
    run_dir: Path,
    gpu_index: int,
    num_steps: int = 100,
    batch_size: int = 16,
) -> dict[str, object]:
    if request.generator_id != "pepflow":
        raise ValueError("PepFlow adapter received a different generator")
    if gpu_index < 0:
        raise ValueError("physical GPU index must be non-negative")
    if num_steps < 2:
        raise ValueError("PepFlow requires at least two integration steps")
    if batch_size < 1:
        raise ValueError("PepFlow batch size must be positive")
    if not python_executable.is_file():
        raise FileNotFoundError(python_executable)
    config_path = source_root / "configs" / "learn_angle.yaml"
    if not (source_root / "models_con" / "flow_model.py").is_file():
        raise FileNotFoundError(source_root / "models_con" / "flow_model.py")
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    _require_file_hash(checkpoint_path, request.runtime.checkpoint_sha256, "PepFlow checkpoint")
    _require_file_hash(receptor_pdb, request.target.structure_sha256, "target coordinate")
    if run_dir.exists():
        raise FileExistsError(f"PepFlow run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    lengths = list(range(request.peptide_length_min, request.peptide_length_max_exclusive))
    quotient, remainder = divmod(request.requested_proposals, len(lengths))
    cases: list[dict[str, object]] = []
    peptide_chain_id: str | None = None
    for ordinal, length in enumerate(lengths):
        proposal_count = quotient + int(ordinal < remainder)
        if not proposal_count:
            continue
        case_id = f"{request.target.target_key}-pepflow-L{length:02d}"
        case = write_pepflow_case(
            request.target,
            receptor_pdb,
            run_dir / "cases" / case_id,
            peptide_length=length,
        )
        if peptide_chain_id is None:
            peptide_chain_id = str(case["peptide_chain_id"])
        elif peptide_chain_id != case["peptide_chain_id"]:
            raise RuntimeError("PepFlow peptide chain identity drifted across length cases")
        cases.append(
            {
                **case,
                "case_id": case_id,
                "proposal_count": proposal_count,
            }
        )
    if peptide_chain_id is None:
        raise RuntimeError("PepFlow request produced no length cases")
    case_manifest = run_dir / "case_manifest.json"
    case_manifest.write_text(json.dumps(cases, indent=2), encoding="utf-8")

    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONHASHSEED": str(request.seed),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "CUDA_VISIBLE_DEVICES": str(gpu_index),
        }
    )
    command = [
        str(python_executable),
        "-c",
        PEPFLOW_SEEDED_LAUNCH,
        str(request.seed),
        str(checkpoint_path),
        "cuda:0",
        str(config_path),
        str(case_manifest),
        str(run_dir / "generated"),
        str(num_steps),
        str(batch_size),
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
            f"PepFlow launcher failed with exit code {completed.returncode}; "
            f"see {run_dir / 'launcher.stderr.log'}"
        )
    proposals = collect_pepflow_proposals(
        request,
        run_dir / "generated",
        peptide_chain_id=peptide_chain_id,
    )
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
        "case_manifest_sha256": sha256_file(case_manifest),
        "case_preprocessing": cases,
        "num_steps": num_steps,
        "batch_size": batch_size,
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
    parser.add_argument("--num-steps", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    request = TargetStructureGenerationRequest.model_validate_json(
        args.request.read_text(encoding="utf-8")
    )
    result = run_pepflow(
        request,
        python_executable=args.python,
        source_root=args.source_root,
        checkpoint_path=args.checkpoint,
        receptor_pdb=args.receptor_pdb,
        run_dir=args.run_dir,
        gpu_index=args.gpu_index,
        num_steps=args.num_steps,
        batch_size=args.batch_size,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "generator_id": request.generator_id,
        "target_key": request.target.target_key,
        "raw_occurrence_count": result["raw_occurrence_count"],
    }))


if __name__ == "__main__":
    main()
