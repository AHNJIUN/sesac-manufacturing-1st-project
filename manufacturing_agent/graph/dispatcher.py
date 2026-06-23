from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403
from manufacturing_agent.config import MAX_PARALLEL_WORKERS, PARALLEL_DISPATCH_ENABLED
from manufacturing_agent.contracts.context import OrchestratorDecision, RouteDecision, TaskSpec
from manufacturing_agent.contracts.state import ManufacturingState
from manufacturing_agent.graph.plan_ops import PlanOps, TASK_TO_NODE, _last_report, unprocessed_reports
from langgraph.types import Send

# ---------- graph/dispatcher.py — Orchestrator Dispatcher (다음 실행 선택) ----------
# plan_ops에만 의존한다. plan 상태를 직접 if/elif로 만지지 않고 PlanOps에 위임한다.
#   1) (아직 소비 안 한) gate report 일괄 적용
#   2) 끊긴 RUNNING task 정리 (active_task_ids 기반)
#   3) gate가 plan repair를 요청했으면 replanner로
#   4) 다음 실행 가능한 batch(또는 단일) task를 골라 worker/final로, 없으면 종료
#   * PARALLEL_DISPATCH_ENABLED=0이면 limit=1로 직렬 호환 동작

def _reset_orphan_running_batch(plan, active_ids: list[str], pending_reports):
      reported_task_ids = {rep.get("task_id") for _, rep in pending_reports}
      keep = set(active_ids) | reported_task_ids
      tasks = [t.model_copy(update={"status": "PENDING"})
               if (t.status == "RUNNING" and t.task_id not in keep) else t
               for t in plan.tasks]
      return plan.model_copy(update={"tasks": tasks})

def orchestrator_dispatcher(state: ManufacturingState, config: RunnableConfig = None) -> dict:
    plan = state.get("execution_plan")
    if plan is None:
        raise ValueError("orchestrator_dispatcher requires execution_plan. Route through supervisor_planner_node first.")

    # (1) 미처리 gate report 일괄 적용
    pending = unprocessed_reports(state, plan)
    new_consumed = list(state.get("consumed_replan_report_indices") or [])
    replan_report = None
    for idx, rep in pending:  
        plan = PlanOps.apply_gate_report(plan, rep)
        if rep.get("status") == "PLAN_REPAIR_REQUIRED" and replan_report is None:
            replan_report = (idx, rep)

    # (2) 끊긴 RUNNING 회수   
    plan = _reset_orphan_running_batch(plan, state.get("active_task_ids") or [], pending)

    # (3) PLAN_REPAIR면 replanner 라우팅
    if replan_report is not None:
        idx, rep = replan_report
        new_consumed.append(idx)
        decision = OrchestratorDecision(
            action="REPLAN", next_node="supervisor_replanner",
            active_task_id=rep.get("task_id"),
            dispatched_task_ids=[rep.get("task_id")],
            reason_summary=f"{rep.get('gate_name')} requested targeted plan repair: {rep.get('reason')}",
        )
        return {
            "execution_plan": plan,
            "orchestrator_decision": decision,
            "active_task_id": rep.get("task_id"),
            "active_task_ids": [rep.get("task_id")],
            "consumed_replan_report_indices": new_consumed,
            "route": RouteDecision(next_node="supervisor_replanner", reason=decision.reason_summary),
        }

    # (4) 실행 가능한 batch 선택 (flag로 직렬/병렬 토글)
    limit = MAX_PARALLEL_WORKERS if PARALLEL_DISPATCH_ENABLED else 1
    batch = PlanOps.next_runnable_batch(plan, limit=limit)
    if not batch:
        decision = OrchestratorDecision(
            action="FINALIZE", next_node="final_answer",
            reason_summary="실행 가능한 task가 없어 최종 답변으로 종료",
        ) 
        return {"execution_plan": plan, "orchestrator_decision": decision,
                "route": RouteDecision(next_node="final_answer", reason=decision.reason_summary)}

    if batch[0].task_type == "final_answer":
        plan = PlanOps.mark_running(plan, batch[0].task_id)
        decision = OrchestratorDecision(
            action="FINALIZE", next_node="final_answer",
            active_task_id=batch[0].task_id,
            dispatched_task_ids=[batch[0].task_id],
            reason_summary=f"{batch[0].task_id} (final_answer) 실행",
        )
        return {"execution_plan": plan, "orchestrator_decision": decision,
                "active_task_id": batch[0].task_id,
                "active_task_ids": [batch[0].task_id],
                "route": RouteDecision(next_node="final_answer", reason=decision.reason_summary)}

    plan = PlanOps.mark_running_batch(plan, [t.task_id for t in batch])
    action = "DISPATCH_BATCH" if len(batch) > 1 else (
        "RETRY_TASK" if (batch[0].retry_count or batch[0].rerun_count) else "DISPATCH_TASK"
    )
    decision = OrchestratorDecision(
        action=action,
        next_node=TASK_TO_NODE[batch[0].task_type],
        active_task_id=batch[0].task_id,
        dispatched_task_ids=[t.task_id for t in batch],
        reason_summary=f"{[t.task_id for t in batch]} 실행",
    )
    return {
        "execution_plan": plan,
        "orchestrator_decision": decision,
        "active_task_id": batch[0].task_id,
        "active_task_ids": [t.task_id for t in batch],
        "route": RouteDecision(next_node=TASK_TO_NODE[batch[0].task_type], reason=decision.reason_summary),
    }

# ---------- graph/route_policy.py — 조건부 엣지 라우팅 ----------
def route_after_intake(state) -> str:
    rep = _last_report(state, "intake_gate")
    return "context_manager" if rep and rep["status"] == "PASS" else "final_answer"


def route_after_orchestrator(state):
    decision = state.get("orchestrator_decision")
    if decision is None:
        return "final_answer"
    if decision.action == "REPLAN":
        return "supervisor_replanner"
    if decision.action == "FINALIZE":
        return "final_answer"
    plan = state.get("execution_plan")
    tasks = [PlanOps.task_by_id(plan, tid) for tid in (decision.dispatched_task_ids or [])]
    tasks = [t for t in tasks if t is not None]
    if not tasks:
        return "final_answer"
    if len(tasks) == 1:
        return TASK_TO_NODE[tasks[0].task_type]  # 직렬 호환
    return [Send(TASK_TO_NODE[t.task_type], state) for t in tasks]


def route_after_output_safety(state) -> str:
    return "memory_writer"