import ast
from pathlib import Path


def test_mmgbsa_runner_has_energy_ci_and_decomposition_contract():
    source = (Path(__file__).parents[1] / "analysis/run_pool_a_mmgbsa.py").read_text()
    ast.parse(source)
    for token in (
        "MMPBSA.py",
        "decomposition.csv",
        "mean_binding_energy_kcal_mol",
        "confidence_interval_95_kcal_mol",
        "moving_block_length_frames",
        "write_amber_topology",
    ):
        assert token in source


def test_mmgbsa_bootstrap_pins_compatible_analysis_runtime():
    source = (
        Path(__file__).parents[1] / "deploy/remote/bootstrap_pool_a_mmgbsa.sh"
    ).read_text()
    assert "python=3.11" in source
    assert "ambertools=26" in source
    assert "openmm=8.3.1" in source
