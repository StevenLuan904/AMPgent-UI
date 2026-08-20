from __future__ import annotations

import numpy as np
import pytest

from pepagent.ood_calibration_cli import _nearest_cosine, _quantiles


def test_nearest_cosine_excludes_self() -> None:
    torch = pytest.importorskip("torch")
    matrix = torch.tensor([[1.0, 0.0], [0.8, 0.6]], dtype=torch.float32)
    observed = _nearest_cosine(matrix, matrix, chunk_size=1, exclude_self=True)
    assert np.allclose(observed, [0.8, 0.8])


def test_quantiles_are_named_and_monotone() -> None:
    observed = _quantiles(np.arange(100, dtype=np.float32))
    assert list(observed) == ["p50", "p90", "p95", "p99"]
    assert observed["p50"] < observed["p90"] < observed["p99"]
