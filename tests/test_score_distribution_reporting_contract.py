from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_score_distribution_reporting_contract_is_project_wide() -> None:
    documents = [
        (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
        (ROOT / "docs" / "ampgent-acea-execution-protocol.md").read_text(
            encoding="utf-8"
        ),
        (ROOT / "docs" / "ampgent-long-horizon-goals.zh-CN.md").read_text(
            encoding="utf-8"
        ),
    ]
    for document in documents:
        assert "P10/P25/P75/P90" in document
        assert "OOD" in document
        assert "最好" in document or "best" in document
        assert "最差" in document or "worst" in document
        assert "平均" in document or "mean" in document
        assert "Candidate/Evaluation" in document or "打分器" in document


def test_reporting_contract_preserves_prediction_semantics() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    protocol = (
        ROOT / "docs" / "ampgent-acea-execution-protocol.md"
    ).read_text(encoding="utf-8")
    normalized_agents = " ".join(agents.split())
    assert (
        "Never call the highest predicted score an experimentally best peptide"
        in normalized_agents
    )
    assert "预测最优不得称实验最优" in protocol
    assert "不能成为安全硬门" in protocol
