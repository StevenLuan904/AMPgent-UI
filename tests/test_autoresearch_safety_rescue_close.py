from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    analysis_dir = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis_dir))
    spec = importlib.util.spec_from_file_location(
        "_autoresearch_safety_rescue_close",
        analysis_dir / "autoresearch_safety_rescue_close.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_branch_selects_exact_requested_branch() -> None:
    module = _load_module()
    payload = {"branches": {"acea": {"current": "a"}, "vegfa": {"current": "v"}}}

    assert module._archive_branch(payload, "vegfa") == {"current": "v"}


def test_archive_branch_rejects_missing_branch() -> None:
    module = _load_module()
    with pytest.raises(ValueError, match="archive update has no branch vegfa"):
        module._archive_branch({"branches": {"acea": {}}}, "vegfa")
