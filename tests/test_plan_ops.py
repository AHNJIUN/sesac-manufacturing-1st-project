"""Tests for manufacturing_agent.graph.plan_ops.PlanOps (pure state-machine ops)."""
from manufacturing_agent.graph.plan_ops import PlanOps, _GATE_STATUS_TO_TASK_STATUS
from manufacturing_agent.contracts.context import ExecutionPlan, TaskSpec


def _task(task_id, task_type, status="PENDING", depends_on=None, **kw):
    return TaskSpec(
        task_id=task_id,
        task_type=task_type,
        status=status,
        depends_on=list(depends_on or []),
        **kw,
    )


def _plan(tasks):
    return ExecutionPlan(intent="combined_analysis", tasks=tasks)


# ---------- next_runnable ----------

def test_next_runnable_returns_first_pending_with_terminal_deps():
    plan = _plan([
        _task("t1", "prediction", status="PASS"),
        _task("t2", "sql", status="PENDING", depends_on=["t1"]),
        _task("final", "final_answer", status="PENDING", depends_on=["t2"]),
    ])
    nxt = PlanOps.next_runnable(plan)
    assert nxt is not None and nxt.task_id == "t2"


def test_next_runnable_skips_pending_with_unmet_deps():
    # t2 depends on t1 which is still RUNNING (not terminal) -> not runnable.
    plan = _plan([
        _task("t1", "prediction", status="RUNNING"),
        _task("t2", "sql", status="PENDING", depends_on=["t1"]),
    ])
    assert PlanOps.next_runnable(plan) is None


def test_next_runnable_returns_final_only_when_others_terminal():
    # All workers terminal -> the final_answer task becomes runnable.
    plan = _plan([
        _task("t1", "prediction", status="PASS"),
        _task("t2", "sql", status="FAIL"),
        _task("final", "final_answer", status="PENDING", depends_on=["t1", "t2"]),
    ])
    nxt = PlanOps.next_runnable(plan)
    assert nxt is not None and nxt.task_type == "final_answer"


def test_next_runnable_none_when_nothing_runnable():
    plan = _plan([
        _task("t1", "prediction", status="PASS"),
        _task("final", "final_answer", status="PASS", depends_on=["t1"]),
    ])
    assert PlanOps.next_runnable(plan) is None


# ---------- deps_terminal ----------

def test_deps_terminal_true_and_false():
    plan = _plan([
        _task("a", "prediction", status="PASS"),
        _task("b", "sql", status="RUNNING"),
        _task("c", "evidence", status="PENDING", depends_on=["a"]),
        _task("d", "evidence", status="PENDING", depends_on=["a", "b"]),
    ])
    c = PlanOps.task_by_id(plan, "c")
    d = PlanOps.task_by_id(plan, "d")
    assert PlanOps.deps_terminal(plan, c) is True   # dep a is PASS (terminal)
    assert PlanOps.deps_terminal(plan, d) is False  # dep b is RUNNING (not terminal)


def test_deps_terminal_empty_deps_is_true():
    plan = _plan([_task("a", "prediction", status="PENDING")])
    assert PlanOps.deps_terminal(plan, PlanOps.task_by_id(plan, "a")) is True


# ---------- apply_gate_report ----------

def test_apply_gate_report_retryable_with_budget_sets_pending_and_increments():
    plan = _plan([_task("t1", "prediction", status="RUNNING", retry_count=0, max_retries=2)])
    report = {"gate_name": "prediction_gate", "status": "RETRYABLE_FAIL", "feedback": "retry me"}
    out = PlanOps.apply_gate_report(plan, report)
    t = PlanOps.task_by_id(out, "t1")
    assert t.status == "PENDING"
    assert t.retry_count == 1
    assert t.feedback_history == ["retry me"]


def test_apply_gate_report_retryable_budget_exhausted_sets_fail():
    plan = _plan([_task("t1", "prediction", status="RUNNING", retry_count=2, max_retries=2)])
    report = {"gate_name": "prediction_gate", "status": "RETRYABLE_FAIL"}
    out = PlanOps.apply_gate_report(plan, report)
    t = PlanOps.task_by_id(out, "t1")
    assert t.status == "FAIL"
    assert t.retry_count == 2  # not incremented once exhausted


def test_apply_gate_report_pass_sets_pass():
    plan = _plan([_task("s1", "sql", status="RUNNING")])
    out = PlanOps.apply_gate_report(plan, {"gate_name": "sql_gate", "status": "PASS"})
    assert PlanOps.task_by_id(out, "s1").status == "PASS"


def test_apply_gate_report_plan_repair_required_maps_to_pending():
    # Per _GATE_STATUS_TO_TASK_STATUS, PLAN_REPAIR_REQUIRED -> PENDING.
    assert _GATE_STATUS_TO_TASK_STATUS["PLAN_REPAIR_REQUIRED"] == "PENDING"
    plan = _plan([_task("e1", "evidence", status="RUNNING")])
    out = PlanOps.apply_gate_report(plan, {"gate_name": "evidence_gate", "status": "PLAN_REPAIR_REQUIRED"})
    assert PlanOps.task_by_id(out, "e1").status == "PENDING"


def test_apply_gate_report_resolves_running_task_via_gate_name_map():
    # report has no task_id; resolver finds the RUNNING task whose type matches the gate.
    plan = _plan([
        _task("p1", "prediction", status="PASS"),
        _task("s1", "sql", status="RUNNING"),
    ])
    out = PlanOps.apply_gate_report(plan, {"gate_name": "sql_gate", "status": "PASS"})
    assert PlanOps.task_by_id(out, "s1").status == "PASS"
    assert PlanOps.task_by_id(out, "p1").status == "PASS"  # unaffected


def test_apply_gate_report_ignores_non_worker_gate():
    plan = _plan([_task("t1", "prediction", status="RUNNING")])
    out = PlanOps.apply_gate_report(plan, {"gate_name": "some_other_gate", "status": "PASS"})
    assert out is plan  # untouched (returns same plan)


# ---------- mark_running / reset_orphan_running ----------

def test_mark_running_sets_status():
    plan = _plan([_task("t1", "sql", status="PENDING")])
    out = PlanOps.mark_running(plan, "t1")
    assert PlanOps.task_by_id(out, "t1").status == "RUNNING"


def test_reset_orphan_running_reverts_running_to_pending():
    # last_report does NOT close the active task -> orphan RUNNING is reset.
    plan = _plan([_task("t1", "sql", status="RUNNING")])
    out = PlanOps.reset_orphan_running(plan, active_task_id="t1", last_report=None)
    assert PlanOps.task_by_id(out, "t1").status == "PENDING"


def test_reset_orphan_running_keeps_running_when_report_closes_active_task():
    plan = _plan([_task("t1", "sql", status="RUNNING")])
    last_report = {"task_id": "t1", "gate_name": "sql_gate", "status": "PASS"}
    out = PlanOps.reset_orphan_running(plan, active_task_id="t1", last_report=last_report)
    assert PlanOps.task_by_id(out, "t1").status == "RUNNING"  # not orphaned


# ---------- purity ----------

def test_apply_gate_report_does_not_mutate_input_plan():
    plan = _plan([_task("t1", "prediction", status="RUNNING", retry_count=0)])
    out = PlanOps.apply_gate_report(plan, {"gate_name": "prediction_gate", "status": "RETRYABLE_FAIL"})
    assert PlanOps.task_by_id(plan, "t1").status == "RUNNING"  # original untouched
    assert PlanOps.task_by_id(plan, "t1").retry_count == 0
    assert out is not plan
    assert PlanOps.task_by_id(out, "t1").status == "PENDING"


def test_mark_running_does_not_mutate_input_plan():
    plan = _plan([_task("t1", "sql", status="PENDING")])
    PlanOps.mark_running(plan, "t1")
    assert PlanOps.task_by_id(plan, "t1").status == "PENDING"
