import ast
from pathlib import Path


def test_cross_queue_activities_use_target_queue_default_build() -> None:
    workflow_source = Path("src/pepagent/workflows/design.py").read_text(encoding="utf-8")
    tree = ast.parse(workflow_source)
    cross_queue_calls = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "execute_activity":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        task_queue = keywords.get("task_queue")
        if not (
            isinstance(task_queue, ast.Constant)
            and isinstance(task_queue.value, str)
            and task_queue.value.startswith(("pepagent-gpu-", "pepagent-cpu-"))
        ):
            continue
        cross_queue_calls += 1
        assert ast.unparse(keywords["versioning_intent"]) == (
            "workflow.VersioningIntent.DEFAULT"
        )

    assert cross_queue_calls == 8


def test_bulk_candidate_validation_honors_configured_boltz_seed_count() -> None:
    workflow_source = Path("src/pepagent/workflows/design.py").read_text(encoding="utf-8")
    tree = ast.parse(workflow_source)
    bulk_workflow = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BulkCandidateEvaluationWorkflow"
    )
    rendered = ast.unparse(bulk_workflow)

    assert "boltz_seeds_per_candidate" in rendered
    assert "for seed_index in range(seed_count)" in rendered
    assert "int(request['seed']) + seed_index" in rendered
    assert "'structures': structures" in rendered
