from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403
from manufacturing_agent.contracts.context import ExecutionPlan, TaskSpec

# ---------- graph/plan_ops.py — Plan-and-Execute 상태머신 엔진 ----------
# 본 시스템은 LangGraph 기반 Gate-driven Manufacturing Plan-and-Execute 구조다.
# 책임 분리:
#   - plan_ops   : ExecutionPlan(task graph) 상태 전이의 단일 출처(pure, side-effect 없음)
#   - planner    : 사용자 의도 -> 최초 ExecutionPlan
#   - replanner  : gate가 요청한 targeted plan repair
#   - dispatcher : 다음 실행 task 선택 + 라우팅 (plan_ops에만 의존)
# 이 셀은 그중 "plan을 어떻게 전이시키는가"만 담당한다. 라우팅/노드 wiring은 모른다.

TASK_TO_NODE = {
    "prediction": "prediction_agent",
    "evidence": "evidence_agent",
    "sql": "sql_agent",
    "final_answer": "final_answer",
}
WORKER_GATE_TO_TASK = {
    "prediction_gate": "prediction",
    "evidence_gate": "evidence",
    "sql_gate": "sql",
}
TERMINAL_TASK_STATUSES = {
    "PASS", "PASS_WITH_WARNINGS", "FAIL", "SKIPPED", "NEEDS_USER_INPUT", "BLOCKED",
}
# gate report status -> task status. 예산(retry) 없는 단순 전이만 표로 둔다.
# RETRYABLE_FAIL 은 retry 예산을 따지므로 PlanOps.apply_gate_report 에서 별도 처리한다.
_GATE_STATUS_TO_TASK_STATUS = {
    "PASS": "PASS",
    "PASS_WITH_WARNINGS": "PASS_WITH_WARNINGS",
    "NEEDS_USER_INPUT": "NEEDS_USER_INPUT",
    "BLOCK": "BLOCKED",
    "NON_RETRYABLE_FAIL": "FAIL",
    "PLAN_REPAIR_REQUIRED": "PENDING",
}


def _last_report(state, gate_name=None) -> Optional[dict]:
    """가장 최근 gate report(옵션: 특정 gate)를 돌려준다."""
    for r in reversed(state.get("gate_reports", []) or []):
        if gate_name is None or r.get("gate_name") == gate_name:
            return r
    return None


class PlanOps:
    """ExecutionPlan 위의 순수 연산. 입력 plan을 변형하지 않고 항상 새 plan을 반환한다."""

    @staticmethod
    def task_by_id(plan: ExecutionPlan, task_id: Optional[str]) -> Optional[TaskSpec]:
        return next((t for t in plan.tasks if t.task_id == task_id), None) if task_id else None

    @staticmethod
    def deps_terminal(plan: ExecutionPlan, task: TaskSpec) -> bool:
        return all(
            (PlanOps.task_by_id(plan, dep_id) is not None
             and PlanOps.task_by_id(plan, dep_id).status in TERMINAL_TASK_STATUSES)
            for dep_id in task.depends_on
        )

    @staticmethod
    def with_task(plan: ExecutionPlan, task_id: str, **updates) -> ExecutionPlan:
        """task_id 하나만 갱신한 새 plan. 나머지 task는 그대로 공유한다."""
        tasks = [t.model_copy(update=updates) if t.task_id == task_id else t for t in plan.tasks]
        return plan.model_copy(update={"tasks": tasks})

    @staticmethod
    def _resolve_reported_task(plan: ExecutionPlan, report: dict) -> Optional[TaskSpec]:
        by_id = PlanOps.task_by_id(plan, report.get("task_id"))
        if by_id is not None:
            return by_id
        gate_task_type = WORKER_GATE_TO_TASK[report["gate_name"]]
        return next((t for t in plan.tasks if t.task_type == gate_task_type and t.status == "RUNNING"), None)

    @staticmethod
    def apply_gate_report(plan: ExecutionPlan, report: Optional[dict]) -> ExecutionPlan:
        """worker gate report를 받아 대상 task 상태를 전이한다(retry 예산 포함)."""
        if not report or report.get("gate_name") not in WORKER_GATE_TO_TASK:
            return plan
        task = PlanOps._resolve_reported_task(plan, report)
        if task is None:
            return plan
        updates: dict = {}
        if report.get("feedback"):
            updates["feedback_history"] = list(task.feedback_history) + [str(report["feedback"])]
        status = report.get("status")
        if status == "RETRYABLE_FAIL":
            if task.retry_count < task.max_retries:
                updates.update(retry_count=task.retry_count + 1, status="PENDING")
            else:
                updates["status"] = "FAIL"
        elif status in _GATE_STATUS_TO_TASK_STATUS:
            updates["status"] = _GATE_STATUS_TO_TASK_STATUS[status]
        return PlanOps.with_task(plan, task.task_id, **updates) if updates else plan

    @staticmethod
    def reset_orphan_running(plan: ExecutionPlan, active_task_id: Optional[str],
                             last_report: Optional[dict]) -> ExecutionPlan:
        """직전 gate가 active task를 종결하지 않은 채 남은 RUNNING task를 PENDING으로 되돌린다.
        (worker가 중간 실패/중단됐을 때 plan이 영원히 RUNNING에 갇히는 것을 막는 안전장치.)"""
        if (last_report and last_report.get("task_id") == active_task_id
                and last_report.get("gate_name") in WORKER_GATE_TO_TASK):
            return plan
        tasks = [t.model_copy(update={"status": "PENDING"}) if t.status == "RUNNING" else t
                 for t in plan.tasks]
        return plan.model_copy(update={"tasks": tasks})

    @staticmethod
    def next_runnable(plan: ExecutionPlan) -> Optional[TaskSpec]:
        """의존성이 모두 종결된 첫 PENDING task. worker가 모두 끝났으면 final_answer task."""
        for task in plan.tasks:
            if task.status == "PENDING" and PlanOps.deps_terminal(plan, task):
                return task
        final = next((t for t in plan.tasks if t.task_type == "final_answer"), None)
        if final and final.status not in TERMINAL_TASK_STATUSES and PlanOps.deps_terminal(plan, final):
            return final
        return None

    @staticmethod
    def mark_running(plan: ExecutionPlan, task_id: str) -> ExecutionPlan:
        return PlanOps.with_task(plan, task_id, status="RUNNING")


