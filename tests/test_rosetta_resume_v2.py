from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from pepagent.model_workers import rosetta_cli
from pepagent.provenance.hashing import sha256_file


def _request(*, nstruct: int) -> dict[str, Any]:
    return {
        "receptor_chains": ["A"],
        "peptide_chain": "B",
        "nstruct": nstruct,
        "parallel_decoys": 1,
        "seed": 302608293,
        "score_function": "ref2015",
        "source_structure_sha256": "a" * 64,
        "source_tool_call_id": "3d78a63d-69ef-49fa-b9ca-b0f872c02184",
    }


def _checkpoint(
    work_dir: Path,
    *,
    index: int,
    base_seed: int,
    prepacked_sha256: str,
) -> None:
    decoy_dir = work_dir / "decoys"
    decoy_dir.mkdir(parents=True, exist_ok=True)
    pdb = decoy_dir / f"decoy_{index + 1:04d}.pdb"
    pdb.write_text(f"ATOM {index}\n", encoding="ascii")
    metric = decoy_dir / f"decoy_{index + 1:04d}.json"
    metric.write_text(
        json.dumps(
            {
                "index": index + 1,
                "seed": base_seed + index + 1,
                "structure": str(pdb.relative_to(work_dir)),
                "structure_sha256": sha256_file(pdb),
                "peptide_bb_rmsd": 1.0,
                "dG_separated": -10.0 - index,
                "total_score": -20.0 - index,
                "reweighted_sc": -21.0 - index,
                "_checkpoint_prepacked_sha256": prepacked_sha256,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("target", "attempt", "saved_decoys", "observed_restart_decoys"),
    [
        ("angpt1", 3, 200, 55),
        ("gyra", 2, 104, 32),
        ("acea", 2, 0, 40),
    ],
)
def test_real_heartbeat_retry_regressions_resume_saved_decoys(
    tmp_path: Path,
    target: str,
    attempt: int,
    saved_decoys: int,
    observed_restart_decoys: int,
) -> None:
    """Model the strict pre-timeout evidence from the 2026-08-28 retries."""

    base_seed = 302608293
    prepacked_sha256 = "b" * 64
    work_dir = tmp_path / target / f"attempt-{attempt}"
    for index in range(saved_decoys):
        _checkpoint(
            work_dir,
            index=index,
            base_seed=base_seed,
            prepacked_sha256=prepacked_sha256,
        )

    reusable = [
        index
        for index in range(200)
        if rosetta_cli._load_decoy_checkpoint(
            index=index,
            seed=base_seed,
            work_dir=work_dir,
            prepacked_sha256=prepacked_sha256,
        )
        is not None
    ]

    assert len(reusable) == saved_decoys
    assert 200 - len(reusable) == 200 - saved_decoys
    if saved_decoys:
        assert observed_restart_decoys < saved_decoys


def test_resumable_cli_reuses_partial_and_completed_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = _request(nstruct=3)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    input_path = tmp_path / "input.pdb"
    input_path.write_text("ATOM input\n", encoding="ascii")
    work_dir = tmp_path / "engine"
    output_path = tmp_path / "result.json"
    calls: list[str] = []

    def prepare(source: Path, destination: Path, *args: Any) -> dict[str, int]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(source).read_bytes())
        return {"receptor": 1, "peptide": 1}

    def run_child(command: list[str]) -> None:
        stage = command[command.index("--stage") + 1]
        calls.append(stage)
        stage_seed = command[command.index("--seed") + 1]
        output = Path(command[command.index("--output-structure") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"ATOM {stage} {stage_seed}\n", encoding="ascii")
        if stage == "refine":
            metric = Path(command[command.index("--output-json") + 1])
            metric.write_text(
                json.dumps(
                    {
                        "total_score": -20.0,
                        "peptide_score": -2.0,
                        "dG_separated": -10.0,
                        "dG_separated_per_dSASA_x100": -1.0,
                        "dSASA_int": 500.0,
                        "interface_hbonds": 3.0,
                        "packstat": 0.5,
                        "interface_score": -5.0,
                        "reweighted_sc": -27.0,
                        "delta_unsat_hbonds": 1.0,
                    }
                ),
                encoding="utf-8",
            )

    monkeypatch.setattr(rosetta_cli, "prepare_protein_peptide_pdb", prepare)
    monkeypatch.setattr(rosetta_cli, "_run_child", run_child)
    monkeypatch.setattr(
        rosetta_cli,
        "peptide_backbone_rmsd_after_receptor_alignment",
        lambda *args: 1.25,
    )
    args = argparse.Namespace(
        request=request_path,
        output=output_path,
        work_dir=work_dir,
        input_structure=input_path,
    )

    rosetta_cli._run(args)
    assert calls == ["prepack", "refine", "refine", "refine"]
    assert len(json.loads(output_path.read_text(encoding="utf-8"))["decoys"]) == 3

    rosetta_cli._run(args)
    assert calls == ["prepack", "refine", "refine", "refine"]

    output_path.unlink()
    (work_dir / "decoys" / "decoy_0003.json").unlink()
    (work_dir / "decoys" / "decoy_0003.pdb").unlink()
    rosetta_cli._run(args)
    assert calls == ["prepack", "refine", "refine", "refine", "refine"]
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(result["decoys"]) == 3
    assert rosetta_cli.RUN_LOCK_NAME not in result["artifacts"]
    assert rosetta_cli.RUN_MANIFEST_NAME not in result["artifacts"]


def test_run_manifest_input_drift_fails_closed(tmp_path: Path) -> None:
    work_dir = tmp_path / "engine"
    first = {
        "schema_version": rosetta_cli.RUN_MANIFEST_SCHEMA,
        "request_sha256": "a" * 64,
        "input_structure_sha256": "b" * 64,
    }
    rosetta_cli._bind_run_manifest(work_dir, first)
    with pytest.raises(ValueError, match="different input identity"):
        rosetta_cli._bind_run_manifest(
            work_dir,
            {**first, "request_sha256": "c" * 64},
        )


def test_candidate_seed_file_lock_serializes_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / ".run.lock"
    script = (
        "import sys,time; from pathlib import Path; "
        "from pepagent.model_workers.rosetta_cli import _exclusive_run_lock; "
        "ctx=_exclusive_run_lock(Path(sys.argv[1])); ctx.__enter__(); "
        "print('acquired',flush=True); time.sleep(float(sys.argv[2])); ctx.__exit__(None,None,None)"
    )
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path), "0.6"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert first.stdout is not None
    assert first.stdout.readline().strip() == "acquired"
    second = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path), "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.15)
    assert second.poll() is None
    first.communicate(timeout=5)
    second_stdout, second_stderr = second.communicate(timeout=5)
    assert second.returncode == 0, second_stderr
    assert second_stdout.strip() == "acquired"


def test_two_processes_same_scope_compute_once(tmp_path: Path) -> None:
    lock_path = tmp_path / ".run.lock"
    result_path = tmp_path / "result.json"
    script = (
        "import sys,time; from pathlib import Path; "
        "from pepagent.model_workers.rosetta_cli import _exclusive_run_lock; "
        "lock=Path(sys.argv[1]); result=Path(sys.argv[2]); "
        "ctx=_exclusive_run_lock(lock); ctx.__enter__(); "
        "state='reuse' if result.exists() else 'compute'; "
        "time.sleep(0.3) if state=='compute' else None; "
        "result.write_text('complete') if state=='compute' else None; "
        "ctx.__exit__(None,None,None); print(state,flush=True)"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(lock_path), str(result_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        outputs.append(stdout.strip())
    assert sorted(outputs) == ["compute", "reuse"]
