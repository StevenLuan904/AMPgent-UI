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
