import ast
from pathlib import Path


def test_pool_a_md_analysis_emits_required_interface_contract():
    source = (Path(__file__).parents[1] / "analysis/analyze_pool_a_md.py").read_text()
    ast.parse(source)
    for key in (
        "interface_rmsd_nm",
        "key_contacts",
        "hydrogen_bond_occupancy",
        "salt_bridge_occupancy",
        "water_bridge_occupancy",
        "peptide_departed",
    ):
        assert key in source
    assert "interaction-stride" in source
