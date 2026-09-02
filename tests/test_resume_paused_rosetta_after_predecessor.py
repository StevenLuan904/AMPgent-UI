from pathlib import Path

import pytest

from deploy.remote.resume_paused_rosetta_after_predecessor import (
    process_uses_root,
    validate_root,
)


def test_process_identity_requires_exact_batch_root() -> None:
    root = Path("/data1/huangyueshan/pepagent/data/run-cache/batch-v7")
    assert process_uses_root(f"python runner.py --root {root}", root)
    assert not process_uses_root("python unrelated.py", root)


def test_root_is_restricted_to_host19_ampgent_cache() -> None:
    root = Path("/data1/huangyueshan/pepagent/data/run-cache/batch-v7")
    assert validate_root(root) == root.resolve()
    with pytest.raises(ValueError, match="outside"):
        validate_root(Path("/tmp/batch-v7"))
