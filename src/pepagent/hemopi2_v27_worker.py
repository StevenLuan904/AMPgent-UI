from __future__ import annotations

import os
import socket
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

REQUIRED_ENVIRONMENT = {
    "PYTHONHASHSEED": "0",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}
NUMERIC_MODULE_PREFIXES = ("numpy", "pandas", "scipy", "sklearn")


def require_preimport_environment(
    loaded_module_names: Iterable[str] | None = None,
) -> None:
    mismatches = {
        key: os.environ.get(key)
        for key, expected in REQUIRED_ENVIRONMENT.items()
        if os.environ.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"v27 deterministic environment mismatch: {sorted(mismatches)}")
    module_names = sys.modules if loaded_module_names is None else loaded_module_names
    imported = sorted(
        name
        for name in module_names
        if any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in NUMERIC_MODULE_PREFIXES
        )
    )
    if imported:
        raise RuntimeError("numeric libraries were imported before v27 environment checks")


def _network_forbidden(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise RuntimeError("network access is forbidden during HemoPI2 v27 smoke")


@contextmanager
def network_disabled() -> Iterator[None]:
    original_socket = socket.socket
    original_connection = socket.create_connection
    socket.socket = _network_forbidden  # type: ignore[assignment]
    socket.create_connection = _network_forbidden  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.create_connection = original_connection


def _smoke_is_authorized(manifest_path: Path) -> bool:
    marker = "\nexecution_status: smoke_authorized\n"
    return marker in f"\n{manifest_path.read_text(encoding='utf-8')}"


def main() -> int:
    require_preimport_environment()
    root = Path(__file__).resolve().parents[2]
    manifest_path = root / "config/benchmarks/amp_designer_safety_validation_v27.yaml"
    if not _smoke_is_authorized(manifest_path):
        raise RuntimeError("v27 smoke is not authorized by the current execution status")
    extracted_root = (
        root
        / "var/external-models/hemopi2/zenodo-14676712/rf-only-extracted-v1"
    )
    with network_disabled():
        from pepagent.hemopi2_v27_inference import run_fixed_v27_smoke_once

        payload = run_fixed_v27_smoke_once(
            extracted_root / "Model", extracted_root / "Model/Data"
        )
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
