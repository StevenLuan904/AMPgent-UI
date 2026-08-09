from __future__ import annotations

import hashlib
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pepagent.hemopi2_adapter import run_fixed_smoke_once


def _network_forbidden(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise RuntimeError("network access is forbidden during HemoPI2 smoke")


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


def main() -> int:
    manifest_path = Path(
        "config/benchmarks/amp_designer_safety_validation_v26.yaml"
    )
    manifest_text = manifest_path.read_text(encoding="utf-8")
    if "\nexecution_status: archive_pending\n" not in f"\n{manifest_text}":
        raise RuntimeError("v26 smoke is not authorized by the current execution status")
    extracted_root = Path(
        "var/external-models/hemopi2/zenodo-14676712/rf-only-extracted-v1"
    )
    model_root = extracted_root / "Model"
    data_root = model_root / "Data"
    with network_disabled():
        first = run_fixed_smoke_once(model_root, data_root)
        second = run_fixed_smoke_once(model_root, data_root)
    first_sha = hashlib.sha256(first).hexdigest()
    second_sha = hashlib.sha256(second).hexdigest()
    if first != second:
        raise RuntimeError("HemoPI2 repeated smoke output bytes differ")
    print(first.decode(), end="")
    print(f"smoke_sha256={first_sha}")
    print(f"repeat_smoke_sha256={second_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
