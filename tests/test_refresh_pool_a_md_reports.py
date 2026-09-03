from pathlib import Path

from analysis.refresh_pool_a_md_reports import commands, subprocess_environment


def test_refresh_pipeline_preserves_dependency_order(tmp_path: Path):
    pipeline = commands(
        tmp_path / "snapshot.json",
        tmp_path / "evidence",
        tmp_path / "reports",
        python="python-test",
    )
    scripts = [Path(command[1]).name for command in pipeline]
    assert scripts == [
        "summarize_pool_a_md_results.py",
        "summarize_pool_a_key_contacts.py",
        "summarize_pool_a_residue_decomposition.py",
        "analyze_pool_s_frontier.py",
        "build_pool_s_candidate_dossiers.py",
        "build_pool_a_md_gap_manifest.py",
        "analyze_rosetta_md_concordance.py",
        "verify_pool_a_md_full_completion.py",
    ]
    assert pipeline[-1][-1] == "--allow-incomplete"
    assert all(command[0] == "python-test" for command in pipeline)


def test_refresh_subprocesses_can_import_analysis_namespace(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "existing-path")
    environment = subprocess_environment()
    repository_root = str(Path(__file__).resolve().parents[1])
    assert environment["PYTHONPATH"].split(__import__("os").pathsep) == [
        repository_root,
        "existing-path",
    ]
