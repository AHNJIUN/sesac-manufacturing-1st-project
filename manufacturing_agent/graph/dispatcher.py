from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403
from manufacturing_agent.contracts.context import OrchestratorDecision, RouteDecision, TaskSpec
from manufacturing_agent.contracts.state import ManufacturingState
from manufacturing_agent.graph.plan_ops import PlanOps, TASK_TO_NODE, _last_report

# ---------- graph/dispatcher.py — Orchestrator Dispatcher (다음 실행 선택) ----------
# plan_ops에만 의존한다. plan 상태를 직접 if/elif로 만지지 않고 PlanOps에 위임한다.
#   1) (아직 소비 안 한) gate report를 plan에 반영
#   2) 끊긴 RUNNING task 정리
#   3) gate가 plan repair를 요청했으면 replanner로
#   4) 다음 실행 가능한 task를 골라 worker/final로, 없으면 종료

def _agent_feedback_from(report: Optional[dict]) -> dict:
    if report and report.get("feedback") and report.get("route_hint"):
        return {report["route_hint"]: report["feedback"]}
    return {}


def _dispatch_action(task: TaskSpec) -> str:
    if task.task_type == "final_answer":
        return "FINALIZE"
    return "RETRY_TASK" if (task.retry_count or task.rerun_count) else "DISPATCH_TASK"


def orchestrator_dispatcher(state: ManufacturingState, config: RunnableConfig = None) -> dict:
    plan = state.get("execution_plan")
    if plan is None:
        raise ValueError("orchestrator_dispatcher requires execution_plan. Route through supervisor_planner_node first.")

    last = _last_report(state)
    last_index = (len(state.get("gate_reports", []) or []) - 1) if last else None
    replan_consumed = last_index is not None and state.get("consumed_replan_report_index") == last_index

    if not replan_consumed:
        plan = PlanOps.apply_gate_report(plan, last)
    plan = PlanOps.reset_orphan_running(plan, state.get("active_task_id"), last)

    # (3) gate가 targeted plan repair를 요청 -> replanner로 위임
    if last and last.get("status") == "PLAN_REPAIR_REQUIRED" and not replan_consumed:
        task_id = last.get("task_id")
        decision = OrchestratorDecision(
            action="REPLAN", next_node="supervisor_replanner", active_task_id=task_id,
            reason_summary=f"{last.get('gate_name')} requested targeted plan repair: {last.get('reason')}",
        )
        return {"execution_plan": plan, "orchestrator_decision": decision, "active_task_id": task_id,
                "route": RouteDecision(next_node="supervisor_replanner", reason=decision.reason_summary)}

    agent_feedback = _agent_feedback_from(last)

    # (4) 다음 실행 task 선택
    nxt = PlanOps.next_runnable(plan)
    if nxt is None:
        decision = OrchestratorDecision(action="FINALIZE", next_node="final_answer",
                                        reason_summary="실행 가능한 task가 없어 최종 답변으로 종료")
        return {"execution_plan": plan, "orchestrator_decision": decision,
                "route": RouteDecision(next_node="final_answer", reason=decision.reason_summary),
                "agent_feedback": agent_feedback}

    plan = PlanOps.mark_running(plan, nxt.task_id)
    node = TASK_TO_NODE[nxt.task_type]
    decision = OrchestratorDecision(action=_dispatch_action(nxt), next_node=node, active_task_id=nxt.task_id,
                                    reason_summary=f"{nxt.task_id}({nxt.task_type}) 실행")
    return {"execution_plan": plan, "orchestrator_decision": decision, "active_task_id": nxt.task_id,
            "route": RouteDecision(next_node=node, reason=decision.reason_summary),
            "agent_feedback": agent_feedback}


# ---------- graph/route_policy.py — 조건부 엣지 라우팅 ----------
def route_after_intake(state) -> str:
    rep = _last_report(state, "intake_gate")
    return "context_manager" if rep and rep["status"] == "PASS" else "final_answer"


def route_after_orchestrator(state) -> str:
    route = state.get("route")
    nxt = route.next_node if route else "final_answer"
    allowed = set(TASK_TO_NODE.values()) | {"supervisor_replanner"}
    return nxt if nxt in allowed else "final_answer"


def route_after_output_safety(state) -> str:
    return "memory_writer"


