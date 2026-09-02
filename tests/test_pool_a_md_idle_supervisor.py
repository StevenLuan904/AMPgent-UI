import importlib.util
from pathlib import Path


def test_pool_a_md_remote_scripts_are_resumable_and_strictly_idle():
    root = Path(__file__).parents[1]
    runner = (root / "deploy/remote/run_pool_a_md_openmm.py").read_text()
    supervisor = (root / "deploy/remote/supervise_pool_a_md_idle.py").read_text()
    compact = "".join(supervisor.split())
    assert "loadCheckpoint" in runner and "append=append" in runner
    assert "CUDA_VISIBLE_DEVICES" in supervisor
    assert "mem>256orutil>5" in compact
    assert "manifest.json" in supervisor
    assert "attempts" in supervisor and "<3" in compact
    assert "retry-cooldown-seconds" in supervisor
    assert "failure_receipt.json" in supervisor
    assert "source-manifest" in supervisor and "staged_relative_path" in supervisor
    assert 'choices=("CUDA", "OpenCL"), default="CUDA"' in supervisor
    assert 'if a.platform == "CUDA"' in supervisor
    assert '"--platform"' in supervisor


def test_md_runner_resolves_heavy_atoms_before_stripping_explicit_cysteine_hydrogen():
    runner = (
        Path(__file__).parents[1] / "deploy/remote/run_pool_a_md_openmm.py"
    ).read_text()
    assert runner.index("fixer.addMissingAtoms()") < runner.index("stripped.delete")


def test_md_runner_validates_and_atomically_writes_completion_manifest():
    runner = (
        Path(__file__).parents[1] / "deploy/remote/run_pool_a_md_openmm.py"
    ).read_text()
    assert "def completed_manifest" in runner
    assert 'payload.get("status") == "succeeded"' in runner
    assert "temporary.replace(manifest_path)" in runner


def test_md_runner_supports_explicit_cuda_or_opencl_without_changing_default():
    runner = (
        Path(__file__).parents[1] / "deploy/remote/run_pool_a_md_openmm.py"
    ).read_text()
    assert 'choices=("CUDA", "OpenCL"), default="CUDA"' in runner
    assert 'mm.Platform.getPlatformByName(platform_name)' in runner
    assert 'mm.Platform.getPlatformByName(a.platform)' in runner
    assert '"compute_platform": a.platform' in runner
    assert '"precision": "mixed"' in runner


def test_md_runner_preserves_md_std_physical_protocol():
    runner = (
        Path(__file__).parents[1] / "deploy/remote/run_pool_a_md_openmm.py"
    ).read_text()
    compact = "".join(runner.split())
    for token in (
        'default=1.0',
        'default=50.0',
        'default=5000',
        'round(ns*500_000)',
        '300*unit.kelvin',
        '1/unit.picosecond',
        '2*unit.femtoseconds',
        '1*unit.bar',
        'padding=1.0*unit.nanometer',
        'ionicStrength=0.15*unit.molar',
        '"amber14/protein.ff14SB.xml"',
        '"amber14/tip3p.xml"',
        'nonbondedMethod=app.PME',
        'nonbondedCutoff=1.0*unit.nanometer',
        'constraints=app.HBonds',
    ):
        assert token in compact


def test_analysis_supervisor_requires_completed_md_manifest():
    source = (
        Path(__file__).parents[1] / "deploy/remote/supervise_pool_a_md_analysis.py"
    ).read_text()
    assert 'glob("*/*/manifest.json")' in source
    assert 'candidate / "analysis/interface"' in source
    assert 'analysis/interface/interface_analysis.json' in source


def test_mmgbsa_supervisor_is_sparse_and_manifest_gated():
    source = (Path(__file__).parents[1] / "deploy/remote/supervise_pool_a_mmgbsa.py").read_text()
    compact = "".join(source.split())
    assert 'glob("*/*/manifest.json")' in source
    assert '"--interval","125"' in compact
    assert "mmgbsa_analysis.json" in source


def test_summary_supervisor_refreshes_compact_reports_without_gpu_work():
    source = (
        Path(__file__).parents[1] / "deploy/remote/supervise_pool_a_md_summary.py"
    ).read_text()
    assert "summarize_pool_a_md_results.py" not in source
    assert '"--snapshot"' in source and '"--evidence-root"' in source
    assert "nvidia" not in source.casefold() and "cuda" not in source.casefold()


def test_cross_host_source_locator_uses_exact_target_and_sequence_hash():
    source = (Path(__file__).parents[1] / "deploy/remote/locate_pool_a_md_sources.py").read_text()
    compact = "".join(source.split())
    assert '(x["target_key"],x["sequence_sha256"])' in compact
    assert "best_decoy" in source


def test_cross_host_stager_copies_without_source_deletion():
    source = (Path(__file__).parents[1] / "deploy/remote/stage_pool_a_md_sources.py").read_text()
    assert "shutil.copy2" in source
    assert "unlink" not in source and "remove(" not in source


def test_successor_waits_for_current_supervisor_before_exact_once_rescan():
    source = (
        Path(__file__).parents[1] / "deploy/remote/launch_pool_a_md_successor_when_ready.sh"
    ).read_text()
    assert 'while kill -0 "$wait_pid"' in source
    assert 'exec "$@"' in source


def test_supervisor_recognizes_exact_live_output_dir(tmp_path):
    source = Path(__file__).parents[1] / "deploy/remote/supervise_pool_a_md_idle.py"
    spec = importlib.util.spec_from_file_location("pool_a_md_supervisor", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proc_root = tmp_path / "proc"
    (proc_root / "123").mkdir(parents=True)
    out = tmp_path / "results" / "acea" / "candidate-a"
    (proc_root / "123" / "cmdline").write_bytes(
        b"python\0run_pool_a_md_openmm.py\0--output-dir\0" + str(out).encode() + b"\0"
    )
    assert module.output_is_running(out, proc_root)
    assert not module.output_is_running(out.parent / "candidate-b", proc_root)


def test_supervisor_round_robins_targets_without_changing_within_target_order():
    source = Path(__file__).parents[1] / "deploy/remote/supervise_pool_a_md_idle.py"
    spec = importlib.util.spec_from_file_location("pool_a_md_supervisor_round_robin", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tasks = [
        {"target_key": "acea", "pool_a_rank": 1},
        {"target_key": "acea", "pool_a_rank": 2},
        {"target_key": "gyra", "pool_a_rank": 1},
        {"target_key": "gyra", "pool_a_rank": 2},
        {"target_key": "pbp2a", "pool_a_rank": 1},
    ]
    result = module.target_round_robin(tasks)
    assert [(x["target_key"], x["pool_a_rank"]) for x in result] == [
        ("acea", 1),
        ("gyra", 1),
        ("pbp2a", 1),
        ("acea", 2),
        ("gyra", 2),
    ]
