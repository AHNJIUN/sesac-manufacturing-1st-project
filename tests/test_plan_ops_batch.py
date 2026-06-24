from manufacturing_agent.contracts.context import ExecutionPlan, TaskSpec
from manufacturing_agent.graph.plan_ops import PlanOps


def _plan(*tasks: TaskSpec) -> ExecutionPlan:
    return ExecutionPlan(intent="combined_analysis", tasks=list(tasks))


def _t(tid, ttype, status="PENDING", deps=None):
    return TaskSpec(task_id=tid, task_type=ttype, status=status, depends_on=deps or [])


def test_batch_returns_independent_workers():
    plan = _plan(
        _t("prediction_1", "prediction"),
        _t("sql_1", "sql"),
        _t("evidence_1", "evidence"),
        _t("final_1", "final_answer", deps=["prediction_1", "sql_1", "evidence_1"]),
    )
    batch = PlanOps.next_runnable_batch(plan)
    assert [t.task_id for t in batch] == ["prediction_1", "sql_1", "evidence_1"]


def test_batch_excludes_final_when_workers_pending():
    plan = _plan(
        _t("prediction_1", "prediction"),
        _t("final_1", "final_answer", deps=["prediction_1"]),
    )
    batch = PlanOps.next_runnable_batch(plan)
    assert [t.task_id for t in batch] == ["prediction_1"]


def test_batch_returns_final_only_when_workers_done():
    plan = _plan(
        _t("prediction_1", "prediction", status="PASS"),
        _t("final_1", "final_answer", deps=["prediction_1"]),
    )
    batch = PlanOps.next_runnable_batch(plan)
    assert [t.task_id for t in batch] == ["final_1"]


def test_batch_respects_depends_on():
    plan = _plan(
        _t("prediction_1", "prediction"),
        _t("sql_1", "sql", deps=["prediction_1"]),
        _t("evidence_1", "evidence", deps=["prediction_1"]),
    )
    batch = PlanOps.next_runnable_batch(plan)
    assert [t.task_id for t in batch] == ["prediction_1"]


def test_batch_limit():
    plan = _plan(_t("prediction_1", "prediction"), _t("sql_1", "sql"), _t("evidence_1", "evidence"))
    batch = PlanOps.next_runnable_batch(plan, limit=2)
    assert len(batch) == 2


def test_mark_running_batch_idempotent():
    plan = _plan(_t("prediction_1", "prediction"), _t("sql_1", "sql"))
    p1 = PlanOps.mark_running_batch(plan, ["prediction_1", "sql_1"])
    p2 = PlanOps.mark_running_batch(p1, ["prediction_1", "sql_1"])
    assert all(t.status == "RUNNING" for t in p2.tasks)