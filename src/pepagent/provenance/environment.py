from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
from typing import Any

from pepagent.provenance.hashing import sha256_json


def runtime_manifest() -> dict[str, Any]:
    packages = sorted(
        {
            distribution.metadata["Name"].lower(): distribution.version
            for distribution in importlib.metadata.distributions()
            if distribution.metadata.get("Name")
        }.items()
    )
    accelerator: dict[str, Any] = {}
    try:
        import torch

        accelerator = {
            "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
    except ImportError:
        accelerator = {"torch": None, "cuda_available": False}
    return {
        "application": {
            "name": "pepagent-platform",
            "version": importlib.metadata.version("pepagent-platform"),
            "release_sha256": os.getenv("PEPAGENT_PLATFORM_RELEASE_SHA256"),
        },
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "accelerator": accelerator,
    }


def fingerprint_runtime() -> tuple[str, dict[str, Any]]:
    manifest = runtime_manifest()
    return sha256_json(manifest), manifest
