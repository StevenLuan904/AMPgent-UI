import runpy
from collections.abc import Callable
from pathlib import Path

_MODULE = runpy.run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "analysis"
        / "autoresearch_cumulative_family_manifest.py"
    ),
    run_name="autoresearch_cumulative_family_manifest_test",
)
_diversity_qualified = _MODULE["_diversity_qualified"]
assert isinstance(_diversity_qualified, Callable)


def test_diversity_gate_prefers_explicit_value() -> None:
    assert _diversity_qualified(
        {
            "diversity_qualified": "true",
            "new_family_relative_to_all_references": "false",
        }
    )
    assert not _diversity_qualified(
        {
            "diversity_qualified": "false",
            "new_family_relative_to_all_references": "true",
        }
    )


def test_diversity_gate_falls_back_when_newer_fields_are_blank() -> None:
    assert _diversity_qualified(
        {
            "diversity_qualified": "",
            "new_family_relative_to_all_references": "",
            "new_family_relative_to_postgresql_history": "true",
        }
    )
    assert not _diversity_qualified(
        {
            "diversity_qualified": "",
            "new_family_relative_to_all_references": "false",
            "new_family_relative_to_postgresql_history": "true",
        }
    )
