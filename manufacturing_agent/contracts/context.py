from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403

# ---------- contracts/context.py ----------
class ConversationTurn(BaseModel):
    role: str
    content: str
    created_at: str

class MachineValue(BaseModel):
    name: str
    value: float | str
    unit: Optional[str] = None
    source: str                       # "current" | "active_context" | "history_context"
    is_current: bool
    is_stale: bool = False

ContextMode = Literal[
    "CURRENT_ONLY",
    "USE_ACTIVE",
    "PATCH_ACTIVE",
    "SELECT_HISTORY",
    "REFER_ACTIVE_RESULT",
]

class DiagnosisContext(BaseModel):
    """진단에 실제 사용된 feature 묶음의 재사용 가능한 snapshot."""
    id: str
    turn_id: str
    user_id: str
    thread_id: str
    features: dict[str, Any] = Field(default_factory=dict)
    failure_types: list[str] = Field(default_factory=list)
    prediction_summary: str = ""
    created_at: str
    is_safe_to_reuse: bool = True

class ContextState(BaseModel):
    active_context_id: Optional[str] = None
    recent_contexts: list[DiagnosisContext] = Field(default_factory=list)

class ContextResolution(BaseModel):
    """이번 턴에서 이전 진단 context를 어떻게 사용할지에 대한 결정."""
    mode: ContextMode = "CURRENT_ONLY"
    current_values: dict[str, Any] = Field(default_factory=dict)
    base_context_id: Optional[str] = None
    patch_values: dict[str, Any] = Field(default_factory=dict)
    resolved_features: dict[str, Any] = Field(default_factory=dict)
    changed_features: list[str] = Field(default_factory=list)
    reused_features: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reason: str = ""

# ---------- contracts/results.py ----------
class FailureRisk(BaseModel):
    """규칙 기반 부분 위험 (PredictionAgent 전용)."""
    failure_type: str                 # HDF | PWF | OSF | TWF
    level: str                        # low | medium | high
    score: float                      # 0.0 ~ 1.0
    detail: str = ""
    rule: str = ""
    formula: str = ""
    contributing_features: list[str] = Field(default_factory=list)
    evidence_query_terms: list[str] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)

class EvidenceHint(BaseModel):
    failure_type: str
    priority: int
    queries: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)

class SafetyHint(BaseModel):
    risk_level: str
    reason: str = ""
    avoid_actions: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)

class PredictionResult(BaseModel):
    """prediction 이름은 유지하되, 내부 의미는 rule-based diagnostic / partial risk assessment다."""
    status: Literal["OK", "PARTIAL", "SKIPPED", "NEEDS_INPUT", "FAIL"] = "SKIPPED"
    available_features: list[str] = Field(default_factory=list)
    missing_features: list[str] = Field(default_factory=list)
    risk_flags: list[dict] = Field(default_factory=list)
    failure_types: list[str] = Field(default_factory=list)
    cause_features: list[str] = Field(default_factory=list)
    evidence_hints: list[EvidenceHint] = Field(default_factory=list)
    safety_hints: list[SafetyHint] = Field(default_factory=list)
    used_stale_features: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    limitations: list[str] = Field(default_factory=list)
    summary: str = ""
    context_mode: str = "CURRENT_ONLY"
    base_context_id: Optional[str] = None
    changed_features: list[str] = Field(default_factory=list)
    reused_features: list[str] = Field(default_factory=list)
    # legacy compatibility: 기존 셀/데모가 참조하던 필드 유지
    full_prediction_available: bool = False
    partial_risks: list[FailureRisk] = Field(default_factory=list)

class ContextCarryoverDecision(BaseModel):
    """멀티턴 후속 질문이 이전 artifact를 어떻게 참조하는지 LLM이 판단한 결과."""
    is_followup: bool = False
    uses_previous_prediction: bool = False
    uses_previous_evidence: bool = False
    uses_previous_sql: bool = False
    inferred_time_range: Optional[dict] = None
    referenced_artifacts: list[Literal["prediction", "sql", "evidence"]] = Field(default_factory=list)
    reason_summary: str = ""

class SupervisorPlannerDecision(BaseModel):
    """LLM 기반 supervisor planning 결과. Regex keyword routing을 대체한다."""
    intent: Literal[
        "prediction_diagnosis", "document_qa", "history_lookup", "combined_analysis",
        "safety_guidance", "general_manufacturing",
    ] = "general_manufacturing"
    needs_prediction: bool = False
    needs_evidence: bool = False
    needs_sql: bool = False
    evidence_required: bool = False
    sql_query_intents: list[Literal["similar_incidents", "failure_history", "corrective_actions", "repeated_patterns"]] = Field(default_factory=list)
    evidence_focus: list[str] = Field(default_factory=list)
    reason_summary: str = ""
    confidence: float = 0.0

class SQLIntentDecision(BaseModel):
    """SQL Agent 실행 전 LLM이 판단한 정형 이력 조회 의도."""
    query_types: list[Literal["similar_incidents", "failure_history", "corrective_actions", "repeated_patterns"]] = Field(default_factory=list)
    failure_type: Optional[str] = None
    time_range: Optional[dict] = None
    filters: dict = Field(default_factory=dict)
    requires_clarification: bool = False
    reason_summary: str = ""

class ContextPacket(BaseModel):
    current_question: str
    recent_turns_summary: str = ""
    current_values: dict[str, Any] = Field(default_factory=dict)
    context_resolution: Optional[ContextResolution] = None
    selected_machine_values: dict[str, MachineValue] = Field(default_factory=dict)
    previous_prediction_result: Optional[PredictionResult] = None
    previous_prediction_summary: Optional[str] = None
    previous_evidence_summary: Optional[str] = None
    previous_sql_summary: Optional[str] = None
    context_carryover: Optional[ContextCarryoverDecision] = None
    user_constraints: dict = Field(default_factory=dict)
    context_warnings: list[str] = Field(default_factory=list)

class AgentContextPacket(BaseModel):
    agent_name: str
    current_question: str
    selected_context: dict = Field(default_factory=dict)
    prior_results: dict = Field(default_factory=dict)

class EvidenceArtifact(BaseModel):
    status: Literal["OK", "EMPTY", "LOW_RELEVANCE", "FAIL"] = "EMPTY"
    retrieval_profile: str = ""
    user_query: str = ""
    queries: list[str] = Field(default_factory=list)
    documents: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    evidence_summary: str = ""
    limitations: list[str] = Field(default_factory=list)
    # legacy/RAG compatibility fields
    mode: str = ""
    search_query: str = ""
    tags: list[str] = Field(default_factory=list)
    doc_whitelist: Optional[list[str]] = None
    failure_types: list[str] = Field(default_factory=list)
    failure_ko: list[str] = Field(default_factory=list)
    is_prediction_based: bool = False
    supervisor_intent: Optional[str] = None
    feedback: Optional[str] = None
    is_retry: bool = False

EvidenceBundle = EvidenceArtifact

class SQLQueryResult(BaseModel):
    query_type: Literal["similar_incidents", "failure_history", "corrective_actions", "repeated_patterns"]
    status: Literal["OK", "EMPTY", "INVALID_REQUEST", "BLOCKED", "FAIL"] = "EMPTY"
    sql: Optional[str] = None
    rows: list[dict] = Field(default_factory=list)
    summary: str = ""
    limitations: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None

class SQLHistoryArtifact(BaseModel):
    status: Literal["OK", "EMPTY", "INVALID_REQUEST", "BLOCKED", "FAIL"] = "EMPTY"
    query_type: Optional[Literal["similar_incidents", "failure_history", "corrective_actions", "repeated_patterns"]] = None
    sql: Optional[str] = None
    rows: list[dict] = Field(default_factory=list)
    results: list[SQLQueryResult] = Field(default_factory=list)
    summary: str = ""
    limitations: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None

class FinalAnswer(BaseModel):
    answer: str
    citations: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)

# ---------- contracts/routing.py ----------
class InputFlags(BaseModel):
    """라우팅용 아님 — Input Guardrail 최소 보안 관측용."""
    is_empty: bool = False
    is_injection: bool = False
    is_control_command: bool = False
    is_manufacturing: bool = True

class InputDecision(BaseModel):
    """Intake Gate의 backward-compatible 차단 판정."""
    blocked: bool = False
    reason: str = "none"          # none|empty|injection|gibberish|out_of_scope|dangerous_request|human_handoff
    layer: str = "pass"           # regex|llm|hybrid|pass
    block_message: str = ""
    is_manufacturing: bool = True

class IntakeDecision(BaseModel):
    """초반 단일 LLM intake 판정: 서비스 가능 여부 + 요청 안전성."""
    service_allowed: bool = True
    input_reason: Literal["none", "empty", "injection", "gibberish", "out_of_scope"] = "none"
    safety_action: Literal["ALLOW", "ANSWER_SAFELY", "BLOCK_DANGEROUS_EXECUTION", "HUMAN_HANDOFF"] = "ALLOW"
    safety_reason: str = ""
    output_constraints: list[str] = Field(default_factory=list)

class OutputSafetyDecision(BaseModel):
    """최종 답변 직후 위험 표현 억제 판정."""
    pass_output: bool = True
    reason: Literal["ok", "empty", "unsafe_instruction", "overconfident_safety", "policy_violation"] = "ok"
    safe_answer: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)

class MachineFeatureInput(BaseModel):
    """프론트엔드 구조화 수치 입력 계약."""
    model_config = {"extra": "forbid"}
    type: Optional[Literal["L", "M", "H"]] = None
    air_temperature: float
    process_temperature: float
    rotational_speed: float
    torque: float
    tool_wear: float
    def to_features(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}

class TaskSpec(BaseModel):
    task_id: str
    task_type: Literal["prediction", "evidence", "sql", "final_answer"]
    status: Literal[
        "PENDING", "RUNNING", "PASS", "PASS_WITH_WARNINGS", "FAIL", "SKIPPED",
        "NEEDS_USER_INPUT", "BLOCKED",
    ] = "PENDING"
    depends_on: list[str] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2
    rerun_count: int = 0
    max_reruns: int = 2
    reason: str = ""
    params: dict = Field(default_factory=dict)
    success_criteria: dict = Field(default_factory=dict)
    feedback_history: list[str] = Field(default_factory=list)
    plan_revision: int = 0
    invalidated_by: Optional[str] = None

class ExecutionPlan(BaseModel):
    intent: Literal[
        "prediction_diagnosis", "document_qa", "history_lookup", "combined_analysis",
        "safety_guidance", "general_manufacturing",
    ]
    tasks: list[TaskSpec] = Field(default_factory=list)
    created_by: Literal["rule", "llm", "hybrid"] = "hybrid"
    reason_summary: str = ""
    confidence: float = 0.0
    plan_revision: int = 0
    replan_count: int = 0
    replan_history: list[dict] = Field(default_factory=list)

class TaskPatch(BaseModel):
    task_id: str
    params_update: dict = Field(default_factory=dict)
    success_criteria_update: dict = Field(default_factory=dict)
    reason: str = ""

class SupervisorReplannerDecision(BaseModel):
    action: Literal["PATCH_AND_RERUN", "FINALIZE_WITH_WARNINGS", "ASK_USER", "BLOCK"]
    target_task_ids: list[str] = Field(default_factory=list)
    task_patches: list[TaskPatch] = Field(default_factory=list)
    invalidate_task_ids: list[str] = Field(default_factory=list)
    reason_summary: str = ""

class OrchestratorDecision(BaseModel):
    action: Literal["DISPATCH_TASK", "RETRY_TASK", "REPLAN", "FINALIZE", "WAIT_USER_INPUT", "BLOCKED"] = "DISPATCH_TASK"
    next_node: Literal["prediction_agent", "evidence_agent", "sql_agent", "final_answer", "supervisor_replanner"]
    active_task_id: Optional[str] = None
    reason_summary: str = ""

class RouteDecision(BaseModel):
    next_node: str
    reason: str
    stop: bool = False

class GateReport(BaseModel):
    task_id: Optional[str] = None
    gate_name: str
    status: Literal[
        "PASS", "PASS_WITH_WARNINGS", "RETRYABLE_FAIL", "NON_RETRYABLE_FAIL",
        "PLAN_REPAIR_REQUIRED", "NEEDS_USER_INPUT", "BLOCK",
    ] = "PASS"
    route_hint: Optional[str] = None
    reason: str = ""
    feedback: Optional[str] = None
    diagnostics: dict = Field(default_factory=dict)
    # input guardrail compatibility
    block: bool = False
    block_reason: Optional[str] = None
    layer: Optional[str] = None
    message: str = ""
    flags: Optional[InputFlags] = None

class RunTrace(BaseModel):
    request_id: str
    events: list[dict] = Field(default_factory=list)

