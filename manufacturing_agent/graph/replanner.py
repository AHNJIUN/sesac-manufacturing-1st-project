from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403
from manufacturing_agent.contracts.context import ExecutionPlan, RouteDecision, SupervisorReplannerDecision, TaskPatch, TaskSpec
from manufacturing_agent.contracts.state import ManufacturingState
from manufacturing_agent.graph.plan_ops import PlanOps, _last_report
from manufacturing_agent.util import _json_object

# ---------- graph/replanner.py — SupervisorReplanner (targeted plan repair) ----------
# gate가 PLAN_REPAIR_REQUIRED를 올렸을 때만 호출된다.
# worker 자체 retry(같은 params 재실행)로 풀 수 없는 실패를, params/criteria를 보정해 재실행한다.

def _patch_task_for_replan(task: TaskSpec, patch: TaskPatch, plan_revision: int) -> TaskSpec:
    feedback = list(task.feedback_history) + ([patch.reason] if patch.reason else [])
    return task.model_copy(update={
        "status": "PENDING",
        "params": {**(task.params or {}), **(patch.params_update or {})},
        "success_criteria": {**(task.success_criteria or {}), **(patch.success_criteria_update or {})},
        "retry_count": 0,
        "rerun_count": task.rerun_count + 1,
        "plan_revision": plan_revision,
        "feedback_history": feedback,
    })


def _replan_evidence(task: TaskSpec, feedback: str) -> Optional[SupervisorReplannerDecision]:
    focus = list(task.params.get("focus") or [])
    if feedback:
        focus.append(feedback)
    return SupervisorReplannerDecision(
        action="PATCH_AND_RERUN", target_task_ids=[task.task_id],
        task_patches=[TaskPatch(
            task_id=task.task_id,
            params_update={"retrieval_profile": "fallback_broad", "focus": focus, "min_docs": 1, "repair_hint": feedback},
            success_criteria_update={"min_docs": 1},
            reason="문서 근거 부족으로 retrieval_profile을 넓히고 focus를 보강함",
        )],
        invalidate_task_ids=["final_1"],
        reason_summary="Evidence task targeted replan",
    )


def _replan_sql(task: TaskSpec, sql_status: Optional[str], feedback: str) -> SupervisorReplannerDecision:
    if sql_status == "INVALID_REQUEST":
        return SupervisorReplannerDecision(action="ASK_USER", target_task_ids=[task.task_id],
                                           reason_summary="SQL 조회에 필요한 조건이 부족함")
    return SupervisorReplannerDecision(
        action="PATCH_AND_RERUN", target_task_ids=[task.task_id],
        task_patches=[TaskPatch(
            task_id=task.task_id,
            params_update={"repair_hint": feedback, "strict_schema_check": True},
            reason="SQL 검증/실행 실패로 schema 준수 hint를 보강함",
        )],
        invalidate_task_ids=["final_1"],
        reason_summary="SQL task targeted replan",
    )


def deterministic_replanner_decision(state: ManufacturingState, plan: ExecutionPlan,
                                     report: Optional[dict]) -> SupervisorReplannerDecision:
    task = PlanOps.task_by_id(plan, report.get("task_id")) if report else None
    if task is None:
        return SupervisorReplannerDecision(action="FINALIZE_WITH_WARNINGS", reason_summary="replan 대상 task를 찾지 못함")
    if task.rerun_count >= task.max_reruns:
        return SupervisorReplannerDecision(action="FINALIZE_WITH_WARNINGS", target_task_ids=[task.task_id],
                                           reason_summary=f"{task.task_id} max_reruns 초과")
    feedback = str(report.get("feedback") or report.get("reason") or "") if report else ""
    if task.task_type == "evidence":
        ev = state.get("evidence_bundle")
        if getattr(ev, "status", None) in {"EMPTY", "LOW_RELEVANCE"}:
            return _replan_evidence(task, feedback)
    if task.task_type == "sql":
        sql = state.get("sql_result")
        sql_status = getattr(sql, "status", None)
        if sql_status in {"FAIL", "BLOCKED", "INVALID_REQUEST"} or (report and report.get("gate_name") == "sql_gate"):
            return _replan_sql(task, sql_status, feedback)
    return SupervisorReplannerDecision(action="FINALIZE_WITH_WARNINGS", target_task_ids=[task.task_id],
                                       reason_summary="적용 가능한 deterministic replan rule 없음")


# replanner action -> 대상 task에 적용할 상태/메타 (PATCH_AND_RERUN 은 patch map으로 별도 처리)
_REPLAN_ACTION_STATUS = {
    "FINALIZE_WITH_WARNINGS": "PASS_WITH_WARNINGS",
    "ASK_USER": "NEEDS_USER_INPUT",
    "BLOCK": "BLOCKED",
}


def apply_replanner_decision(plan: ExecutionPlan, decision: SupervisorReplannerDecision,
                             report: Optional[dict]) -> ExecutionPlan:
    next_revision = plan.plan_revision + 1
    patch_map = {p.task_id: p for p in decision.task_patches}
    targets = set(decision.target_task_ids)
    invalidated = set(decision.invalidate_task_ids)
    if decision.action == "PATCH_AND_RERUN":
        invalidated.add("final_1")  # rerun 시 stale final이 PASS로 남지 않도록 강제 무효화
    forced_status = _REPLAN_ACTION_STATUS.get(decision.action)

    tasks = []
    for task in plan.tasks:
        if decision.action == "PATCH_AND_RERUN" and task.task_id in patch_map:
            tasks.append(_patch_task_for_replan(task, patch_map[task.task_id], next_revision))
        elif decision.action == "PATCH_AND_RERUN" and task.task_id in invalidated:
            tasks.append(task.model_copy(update={"status": "PENDING",
                                                 "invalidated_by": ",".join(decision.target_task_ids),
                                                 "plan_revision": next_revision}))
        elif forced_status and task.task_id in targets:
            tasks.append(task.model_copy(update={"status": forced_status, "plan_revision": next_revision,
                                                 "feedback_history": list(task.feedback_history) + [decision.reason_summary]}))
        else:
            tasks.append(task)
    history_item = {"plan_revision": next_revision, "action": decision.action,
                    "target_task_ids": decision.target_task_ids, "reason": decision.reason_summary,
                    "source_gate_report": report}
    return plan.model_copy(update={"tasks": tasks, "plan_revision": next_revision,
                                   "replan_count": plan.replan_count + 1,
                                   "replan_history": list(plan.replan_history) + [history_item]})


# ---------- replanner LLM 적응 경로 (1-A hybrid) ----------
REPLANNER_SYS = (
    "너는 제조 LangGraph Agent의 SupervisorReplanner다. 한 worker task가 실패/부족해 보정 재실행이 필요한지 판단한다. "
    "답변을 만들지 말고, 실패한 task를 어떻게 복구할지만 JSON으로 결정한다.\n"
    "가능한 action:\n"
    "- PATCH_AND_RERUN: params를 보정해 같은 task를 다시 실행(검색 범위 확대, 시간창 확대, 필터/조건 완화 등). 복구 가능성이 있을 때만.\n"
    "- ASK_USER: 사용자 입력 없이는 진행 불가할 때.\n"
    "- FINALIZE_WITH_WARNINGS: 추가 시도가 의미 없을 때(현재 정보로 제한 답변).\n"
    "- BLOCK: 정책상 막아야 할 때.\n"
    "params_update는 해당 task에 합칠 보정 파라미터다. evidence면 retrieval_profile(fallback_broad 등)/focus/min_docs, "
    "sql이면 시간창/필터 완화/repair_hint 등. SQLAgent는 failure_history 단일 테이블만 조회한다.\n"
    "반드시 JSON만 출력하라: "
    '{"action": "PATCH_AND_RERUN|ASK_USER|FINALIZE_WITH_WARNINGS|BLOCK", "params_update": {}, "reason_summary": "짧은 이유"}'
)
_REPLANNER_ACTIONS = {"PATCH_AND_RERUN", "ASK_USER", "FINALIZE_WITH_WARNINGS", "BLOCK"}


def _parse_replanner_decision(raw: str, task: TaskSpec) -> SupervisorReplannerDecision:
    data = _json_object(raw)
    action = str(data.get("action", "FINALIZE_WITH_WARNINGS")).strip().upper()
    if action not in _REPLANNER_ACTIONS:
        action = "FINALIZE_WITH_WARNINGS"
    patches = []
    if action == "PATCH_AND_RERUN":
        pu = data.get("params_update")
        patches = [TaskPatch(task_id=task.task_id,
                             params_update=pu if isinstance(pu, dict) else {},
                             reason=str(data.get("reason_summary", "")))]
    return SupervisorReplannerDecision(
        action=action,
        target_task_ids=[task.task_id],
        task_patches=patches,
        invalidate_task_ids=["final_1"] if action == "PATCH_AND_RERUN" else [],
        reason_summary=str(data.get("reason_summary", "")) or "LLM replanner decision",
    )


def _llm_replanner_decision(state: ManufacturingState, plan: ExecutionPlan,
                            report: Optional[dict], task: TaskSpec) -> SupervisorReplannerDecision:
    ev = state.get("evidence_bundle")
    sql = state.get("sql_result")
    payload = {
        "failed_task": {"task_id": task.task_id, "task_type": task.task_type, "params": task.params,
                        "rerun_count": task.rerun_count, "max_reruns": task.max_reruns},
        "gate_reason": (report or {}).get("reason"),
        "gate_feedback": (report or {}).get("feedback"),
        "evidence_status": getattr(ev, "status", None),
        "sql_status": getattr(sql, "status", None),
        "user_message": state.get("user_message", ""),
    }
    try:
        raw = call_llm(REPLANNER_SYS, json.dumps(payload, ensure_ascii=False), tier="default")
        return _parse_replanner_decision(raw, task)
    except Exception as e:
        return SupervisorReplannerDecision(action="FINALIZE_WITH_WARNINGS", target_task_ids=[task.task_id],
                                           reason_summary=f"llm_replanner_error: {type(e).__name__}")


def hybrid_replanner_decision(state: ManufacturingState, plan: ExecutionPlan,
                              report: Optional[dict]) -> SupervisorReplannerDecision:
    """deterministic을 1차로, '포기(FINALIZE_WITH_WARNINGS)' 신호일 때만 LLM 적응 경로로 복구를 시도한다."""
    d = deterministic_replanner_decision(state, plan, report)
    if d.action != "FINALIZE_WITH_WARNINGS":
        return d  # deterministic이 처리함 → 그대로 (T20 회귀 안전판)
    task = PlanOps.task_by_id(plan, report.get("task_id")) if report else None
    if task is None or task.rerun_count >= task.max_reruns:
        return d  # 예산 소진/대상 없음 → LLM 호출 안 함 (무한루프 방지)
    llm_d = _llm_replanner_decision(state, plan, report, task)
    if llm_d.action == "PATCH_AND_RERUN" and task.rerun_count >= task.max_reruns:
        return d  # 사후 예산 클램프
    return llm_d


def supervisor_replanner_node(state: ManufacturingState, config: RunnableConfig = None) -> dict:
    plan = state.get("execution_plan")
    if plan is None:
        raise ValueError("supervisor_replanner requires execution_plan")
    last = _last_report(state)
    report_index = (len(state.get("gate_reports", []) or []) - 1) if last else None
    decision = hybrid_replanner_decision(state, plan, last)
    new_plan = apply_replanner_decision(plan, decision, last)
    return {
        "execution_plan": new_plan,
        "supervisor_replanner_decision": decision,
        "consumed_replan_report_indices": [report_index] if report_index is not None else [],
        "active_task_id": None,
        "active_task_ids": [],
        "route": RouteDecision(next_node="orchestrator_dispatcher", reason=decision.reason_summary),
    }
    
def hybrid_replanner_decision_batch(state, plan, repair_reports: list[dict]) -> SupervisorReplannerDecision:
    """병렬에서 여러 task가 동시에 PLAN_REPAIR_REQUIRED일 때 한 결정으로 합친다."""
    decisions = [hybrid_replanner_decision(state, plan, r) for r in repair_reports]
    patches: list[TaskPatch] = []
    targets: list[str] = []
    reasons: list[str] = []
    for d in decisions:
        if d.action == "PATCH_AND_RERUN":
            patches.extend(d.task_patches)
            targets.extend(d.target_task_ids)
            reasons.append(d.reason_summary)
        elif d.action in {"ASK_USER", "BLOCK"}:
            return d
    if not patches:
        return decisions[-1] if decisions else SupervisorReplannerDecision(
            action="FINALIZE_WITH_WARNINGS", reason_summary="no patch generated")
    return SupervisorReplannerDecision(
        action="PATCH_AND_RERUN", target_task_ids=targets, task_patches=patches,
        invalidate_task_ids=["final_1"],
        reason_summary="; ".join(reasons),
    )