from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import pepagent.v38_persistence as persistence
from pepagent.db.models import ExperimentRun, ToolCall
from pepagent.domain.enums import EvaluationStatus
from pepagent.v38_persistence import (
    GeneratorCellToolBinding,
    persist_score_all_proposal_cohort,
)
from pepagent.v38_science_execution import (
    RawProposal,
    build_default_v38_sequence_contract,
    build_score_all_proposal_cohort,
)


def _sequence(index: int) -> str:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    digits: list[str] = []
    value = index
    for _ in range(11):
        digits.append(alphabet[value % len(alphabet)])
        value //= len(alphabet)
    return "K" + "".join(digits)


def _cohort_and_bindings():
    contract = build_default_v38_sequence_contract()
    proposals = [
        RawProposal(
            generator_id=cell.generator_id,
            seed=cell.seed,
            raw_rank=rank,
            sequence=_sequence(cell.ordinal * 100 + rank),
        )
        for cell in contract.cells
        for rank in range(1, 101)
    ]
    cohort = build_score_all_proposal_cohort(contract, proposals)
    bindings = tuple(
        GeneratorCellToolBinding(
            cell_ordinal=cell.ordinal,
            generator_id=cell.generator_id,
            seed=cell.seed,
            tool_call_id=uuid4(),
            opaque_arm_label=f"generator-cell-{cell.ordinal}",
        )
        for cell in contract.cells
    )
    return contract, cohort, bindings


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeRepository:
    candidates: list[SimpleNamespace] = []
    occurrences: list[dict] = []
    events: list[tuple] = []

    def __init__(self, session):
        self.session = session

    async def add_candidate(self, **kwargs):
        candidate = SimpleNamespace(id=uuid4(), **kwargs)
        self.candidates.append(candidate)
        return candidate

    async def record_candidate_occurrence(self, **kwargs):
        self.occurrences.append(kwargs)
        return SimpleNamespace(id=uuid4(), **kwargs)

    async def append_event(self, *args):
        self.events.append(args)
        return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_score_all_persistence_materializes_every_valid_sequence(monkeypatch) -> None:
    contract, cohort, bindings = _cohort_and_bindings()
    run_id = uuid4()
    calls = {
        item.tool_call_id: SimpleNamespace(
            id=item.tool_call_id,
            run_id=run_id,
            status=EvaluationStatus.SUCCEEDED,
        )
        for item in bindings
    }
    session = AsyncMock()

    async def get(model, identity):
        if model is ExperimentRun:
            return SimpleNamespace(id=run_id)
        if model is ToolCall:
            return calls.get(identity)
        raise AssertionError(f"unexpected get: {model}")

    session.get.side_effect = get
    session.execute.return_value = _Result([])
    _FakeRepository.candidates = []
    _FakeRepository.occurrences = []
    _FakeRepository.events = []
    monkeypatch.setattr(persistence, "ExperimentRepository", _FakeRepository)

    receipt = await persist_score_all_proposal_cohort(
        session,
        run_id=run_id,
        contract=contract,
        cohort=cohort,
        bindings=bindings,
    )

    assert receipt.action == "persisted"
    assert receipt.raw_occurrence_count == 900
    assert receipt.promoted_candidate_count == 900
    assert len(_FakeRepository.candidates) == 900
    assert len(_FakeRepository.occurrences) == 900
    assert all(item["candidate_id"] is not None for item in _FakeRepository.occurrences)
    assert _FakeRepository.events[0][2] == "v38.score_all_cohort.persisted"


@pytest.mark.asyncio
async def test_score_all_persistence_rejects_partial_existing_set() -> None:
    contract, cohort, bindings = _cohort_and_bindings()
    run_id = uuid4()
    calls = {
        item.tool_call_id: SimpleNamespace(
            id=item.tool_call_id,
            run_id=run_id,
            status=EvaluationStatus.SUCCEEDED,
        )
        for item in bindings
    }
    session = AsyncMock()

    async def get(model, identity):
        if model is ExperimentRun:
            return SimpleNamespace(id=run_id)
        if model is ToolCall:
            return calls.get(identity)
        raise AssertionError(f"unexpected get: {model}")

    session.get.side_effect = get
    session.execute.return_value = _Result([SimpleNamespace(id=uuid4())])

    with pytest.raises(ValueError, match="partial score-all"):
        await persist_score_all_proposal_cohort(
            session,
            run_id=run_id,
            contract=contract,
            cohort=cohort,
            bindings=bindings,
        )


def test_score_all_persistence_requires_one_distinct_call_per_cell() -> None:
    contract, cohort, bindings = _cohort_and_bindings()
    duplicate_call = bindings[1].model_copy(update={"tool_call_id": bindings[0].tool_call_id})
    with pytest.raises(ValueError, match="distinct ToolCall"):
        persistence._validate_score_all_bindings(
            contract,
            (bindings[0], duplicate_call, *bindings[2:]),
        )

    assert cohort.raw_occurrence_count == 900
