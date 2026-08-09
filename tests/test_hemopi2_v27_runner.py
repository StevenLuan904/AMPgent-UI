from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pepagent import hemopi2_v27_worker as worker
from pepagent.hemopi2_v27_inference import official_hc50_um
from pepagent.hemopi2_v27_smoke import main as smoke_main

ROOT = Path(__file__).parents[1]


def test_official_hc50_transform_matches_archived_reporting_contract() -> None:
    raw = np.asarray([-4.502443524567688, -3.516668728178445])
    expected = np.asarray(
        [round(math.exp(-value), 3) for value in raw], dtype=np.float64
    )
    assert official_hc50_um(raw).tolist() == expected.tolist()
    assert official_hc50_um(raw).tolist() == [90.237, 33.672]


def test_official_hc50_transform_rejects_invalid_raw_values() -> None:
    with pytest.raises(ValueError, match="finite vector"):
        official_hc50_um(np.asarray([[1.0]]))
    with pytest.raises(ValueError, match="finite vector"):
        official_hc50_um(np.asarray([np.nan]))


def test_worker_import_has_no_numeric_library_side_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    module_names = ("numpy", "pandas", "scipy", "sklearn")
    worker_path = ROOT / "src/pepagent/hemopi2_v27_worker.py"
    source = worker_path.read_text(encoding="utf-8")
    top_level = source.split("def main()", maxsplit=1)[0]
    assert not any(f"import {name}" in top_level for name in module_names)
    for key, value in worker.REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    worker.require_preimport_environment(loaded_module_names=())
    with pytest.raises(RuntimeError, match="numeric libraries"):
        worker.require_preimport_environment(loaded_module_names=("numpy",))


def test_v27_passed_status_prevents_smoke_rerun() -> None:
    with pytest.raises(RuntimeError, match="not authorized"):
        smoke_main()


def test_smoke_runner_has_no_formal_cohort_path_or_shell_execution() -> None:
    source = (ROOT / "src/pepagent/hemopi2_v27_smoke.py").read_text(
        encoding="utf-8"
    )
    assert "amp_generator_v25_candidate_metrics" not in source
    assert "shell=False" in source
    assert "shell=True" not in source
    assert source.count("_run_fresh_worker(root)") == 2
