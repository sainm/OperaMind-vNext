from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
GRAPH_SCRIPT = ROOT / "src/operamind/web/static/task-graph.js"


def test_svg_graph_model_marks_critical_path_and_block_propagation() -> None:
    tasks = [
        _task("task-1", 1, "completed"),
        _task("task-2", 2, "blocked", ("task-1",)),
        _task("task-3", 3, "ready", ("task-2",)),
        _task("task-4", 4, "ready", ("task-3",)),
        _task("task-side", 5, "completed", ("task-1",)),
    ]

    model = _build_model(tasks)
    nodes = {node["id"]: node for node in model["nodes"]}
    edges = {(edge["from"], edge["to"]): edge for edge in model["edges"]}

    assert model["criticalPath"] == ["task-1", "task-2", "task-3", "task-4"]
    assert nodes["task-2"]["state"] == "blocked"
    assert nodes["task-3"]["blockedBy"] == ["task-2"]
    assert nodes["task-4"]["blockedBy"] == ["task-2"]
    assert nodes["task-side"]["blockedBy"] == []
    assert edges[("task-2", "task-3")]["blocking"] is True
    assert edges[("task-3", "task-4")]["blocking"] is True
    assert edges[("task-1", "task-2")]["blocking"] is False
    assert edges[("task-3", "task-4")]["critical"] is True
    assert model["blockedPropagationCount"] == 2


def test_svg_graph_model_exposes_missing_predecessor_and_cycles() -> None:
    tasks = [
        _task("task-a", 1, "ready", ("task-b", "outside-task")),
        _task("task-b", 2, "ready", ("task-a",)),
    ]

    model = _build_model(tasks)
    nodes = {node["id"]: node for node in model["nodes"]}

    assert nodes["outside-task"]["external"] is True
    assert set(model["cycleNodeIds"]) == {"task-a", "task-b"}
    assert any(edge["cycle"] for edge in model["edges"])
    assert model["width"] > 0
    assert model["height"] > 0


def _build_model(tasks: list[dict[str, object]]) -> dict[str, object]:
    javascript = """
const graph = require(process.argv[1]);
const tasks = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(graph.buildTaskDependencyGraph(tasks)));
"""
    result = subprocess.run(
        ["node", "-e", javascript, str(GRAPH_SCRIPT), json.dumps(tasks)],
        check=True,
        capture_output=True,
        text=True,
    )
    value: object = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _task(
    task_id: str,
    sequence: int,
    state: str,
    dependencies: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "orchestration_task_id": task_id,
        "automation_run_id": "run-1",
        "sequence": sequence,
        "title": task_id,
        "state": state,
        "effective_state": state,
        "dependencies": list(dependencies),
    }
