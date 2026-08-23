import pytest

from pepagent.workers import v38_activities


@pytest.mark.asyncio
async def test_round_yield_resolves_full_ids_from_admission_reference(monkeypatch) -> None:
    durable_admission = {
        "mature_core_candidate_ids": ["00000000-0000-0000-0000-000000000001"],
        "exploration_candidate_ids": ["00000000-0000-0000-0000-000000000002"],
        "rejected_candidate_ids": ["00000000-0000-0000-0000-000000000003"],
    }

    async def resolve(_reference):
        return {"admission": durable_admission}

    monkeypatch.setattr(v38_activities, "_resolve_v38_admission", resolve)
    observed = await v38_activities._resolve_v39_round_admission(
        {
            "admission": {
                "mature_core_count": 1,
                "exploration_count": 1,
                "rejected_count": 1,
            },
            "admission_reference": {"schema_version": "test"},
        }
    )

    assert observed == durable_admission


@pytest.mark.asyncio
async def test_round_yield_rejects_summary_artifact_count_drift(monkeypatch) -> None:
    async def resolve(_reference):
        return {
            "admission": {
                "mature_core_candidate_ids": [],
                "exploration_candidate_ids": [],
                "rejected_candidate_ids": [],
            }
        }

    monkeypatch.setattr(v38_activities, "_resolve_v38_admission", resolve)
    with pytest.raises(ValueError, match="differs from durable artifact"):
        await v38_activities._resolve_v39_round_admission(
            {
                "admission": {
                    "mature_core_count": 1,
                    "exploration_count": 0,
                    "rejected_count": 0,
                },
                "admission_reference": {"schema_version": "test"},
            }
        )
