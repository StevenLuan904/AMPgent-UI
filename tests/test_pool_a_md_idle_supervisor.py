from pathlib import Path


def test_pool_a_md_remote_scripts_are_resumable_and_strictly_idle():
    root=Path(__file__).parents[1]
    runner=(root/"deploy/remote/run_pool_a_md_openmm.py").read_text()
    supervisor=(root/"deploy/remote/supervise_pool_a_md_idle.py").read_text()
    assert "loadCheckpoint" in runner and "append=append" in runner
    assert "CUDA_VISIBLE_DEVICES" in supervisor
    assert "mem>256 or util>5" in supervisor
    assert "manifest.json" in supervisor
