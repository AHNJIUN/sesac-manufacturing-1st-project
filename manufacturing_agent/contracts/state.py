from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403
from manufacturing_agent.contracts.context import ContextPacket, EvidenceArtifact, ExecutionPlan, FinalAnswer, InputDecision, InputFlags, IntakeDecision, MachineFeatureInput, OrchestratorDecision, PredictionResult, RouteDecision, RunTrace, SQLHistoryArtifact, SQLIntentDecision, SupervisorPlannerDecision, SupervisorReplannerDecision
from operator import add
from typing import Annotated, Optional
from manufacturing_agent.contracts.reducers import dict_merge_max

# ---------- contracts/state.py ----------
class ManufacturingState(MessagesState, total=False):
    # (상속) messages: Annotated[list[BaseMessage], add_messages]
    request_id: str
    thread_id: str
    user_id: str
    user_message: str
    input_features: Optional[MachineFeatureInput]

    input_decision: Optional[InputDecision]
    input_flags: Optional[InputFlags]
    intake_decision: Optional[IntakeDecision]

    context_packet: Optional[ContextPacket]
    agent_contexts: dict

    execution_plan: Optional[ExecutionPlan]
    supervisor_planner_decision: Optional[SupervisorPlannerDecision]
    supervisor_replanner_decision: Optional[SupervisorReplannerDecision]
    sql_intent_decision: Optional[SQLIntentDecision]
    orchestrator_decision: Optional[OrchestratorDecision]
    active_task_id: Optional[str]
    route: Optional[RouteDecision]
    intent: Optional[str]
    agent_feedback: dict
    consumed_replan_report_index: Optional[int]

    prediction_result: Optional[PredictionResult]
    evidence_bundle: Optional[EvidenceArtifact]
    sql_result: Optional[SQLHistoryArtifact]

    gate_reports: list
    retry_counts: dict

    final_answer: Optional[FinalAnswer]
    run_trace: Optional[RunTrace]

    # 다중 writer (동시 쓰기 가능) — reducer 필수
    gate_reports: Annotated[list, add]
    retry_counts: Annotated[dict, dict_merge_max]

    # 단일 writer (전체 리스트 반환) — reducer 없음
    active_task_ids: list[str]                  # 신규
    consumed_replan_report_indices: list[int]   # 신규

    # 호환용 — deprecated
    active_task_id: Optional[str]
    consumed_replan_report_index: Optional[int]

    # agent_feedback도 어노테이션 없이 그대로 (dispatcher 단일 writer)
