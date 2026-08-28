from __future__ import annotations

import copy
from types import SimpleNamespace
from uuid import UUID

import pytest

from pepagent.autoresearch_formal_submit_cli import AutoResearchFormalBranch
from pepagent.autoresearch_reconcile_cli import _validate_and_bind_run
from pepagent.provenance.hashing import sha256_json


def _branch() -> AutoResearchFormalBranch:
    return AutoResearchFormalBranch(
        branch_key="angpt1",
        target_id=UUID("11111111-1111-1111-1111-111111111111"),
        target_sequence="AAAA",
        target_sequence_sha256="a" * 64,
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        workflow_id="workflow-angpt1",
        formal_submission_key="b" * 64,
        parent_run_id=UUID("33333333-3333-3333-3333-333333333333"),
        seed={},
        continuation_policy={},
        request={},
        request_bytes=b"{}",
        request_sha256="c" * 64,
        request_template_sha256="d" * 64,
        run_identity_sha256="e" * 64,
    )


def _run(branch: AutoResearchFormalBranch, spec: dict, *, status: str, temporal_run_id=None):
    return SimpleNamespace(
        id=branch.run_id,
        target_id=branch.target_id,
        formal_submission_key=branch.formal_submission_key,
        temporal_workflow_id=branch.workflow_id,
        temporal_run_id=temporal_run_id,
        parent_run_id=branch.parent_run_id,
        spec_json=copy.deepcopy(spec),
        spec_sha256=sha256_json(spec),
        status=status,
    )


def test_reconciliation_binds_only_created_null_run() -> None:
    branch = _branch()
    spec = {"identity": "exact"}
    run = _run(branch, spec, status="created")

    assert _validate_and_bind_run(
        run,
        branch=branch,
        expected_spec=spec,
        temporal_run_id="temporal-exact",
    )
    assert run.status == "running"
    assert run.temporal_run_id == "temporal-exact"


def test_reconciliation_preserves_exact_running_or_terminal_run() -> None:
    branch = _branch()
    spec = {"identity": "exact"}
    for status in ("running", "failed", "succeeded"):
        run = _run(branch, spec, status=status, temporal_run_id="temporal-exact")
        assert not _validate_and_bind_run(
            run,
            branch=branch,
            expected_spec=spec,
            temporal_run_id="temporal-exact",
        )
        assert run.status == status


def test_reconciliation_rejects_durable_or_temporal_identity_drift() -> None:
    branch = _branch()
    spec = {"identity": "exact"}
    drifted = _run(branch, {"identity": "wrong"}, status="created")
    with pytest.raises(ValueError, match="reservation identity drifted"):
        _validate_and_bind_run(
            drifted,
            branch=branch,
            expected_spec=spec,
            temporal_run_id="temporal-exact",
        )

    wrong_temporal = _run(
        branch,
        spec,
        status="running",
        temporal_run_id="temporal-wrong",
    )
    with pytest.raises(ValueError, match="Temporal identity drifted"):
        _validate_and_bind_run(
            wrong_temporal,
            branch=branch,
            expected_spec=spec,
            temporal_run_id="temporal-exact",
        )
