# Manufacturing Agent v6 전체 흐름 정리

이 문서는 `manufacturing_agent_v6.ipynb` 기준의 현재 LangGraph 제조업 Agent 구조를 설명한다. 핵심은 ReAct가 아니라 `SupervisorPlan` 기반의 **Gate-driven Manufacturing Plan-and-Execute** 구조다. LLM은 자유롭게 tool을 반복 선택하지 않고, 정해진 graph node 안에서 typed decision, 근거 요약, safety 판단 등에 제한적으로 사용된다. 테스트 통과를 위해 특정 시나리오를 고정 문구로 덮어쓰는 방식은 사용하지 않는다. gate가 실패하면 실패 상태와 reason을 남기고, 같은 params retry로 해결되기 어려운 경우에만 targeted replan을 수행한다.

## 1. 전체 실행 흐름

```text
START
→ intake_gate
→ context_manager
→ supervisor_planner
→ orchestrator_dispatcher
   ├─ prediction_agent → prediction_gate → orchestrator_dispatcher
   ├─ sql_agent        → sql_gate        → orchestrator_dispatcher
   ├─ evidence_agent   → evidence_gate   → orchestrator_dispatcher
   ├─ supervisor_replanner → orchestrator_dispatcher
   └─ final_answer
→ output_safety_gate
→ memory_writer
→ END
```

예외 흐름:

- `intake_gate`가 입력을 차단하면 `context_manager`로 가지 않고 바로 `final_answer`로 이동한다.
- 각 worker는 직접 최종 답변을 만들지 않고 typed artifact만 생성한다.
- 각 gate는 worker를 직접 재실행하지 않고 `GateReport`만 남긴다.
- 재실행 여부와 다음 node 선택은 `orchestrator_dispatcher`가 `ExecutionPlan`, task status, `GateReport`, retry/replan count를 보고 결정한다.
- `GateReport.status == PLAN_REPAIR_REQUIRED`이면 dispatcher는 worker를 바로 재실행하지 않고 `supervisor_replanner`로 보낸다.

## 2. 현재 구조 판정: 오케스트레이터인가?

결론부터 말하면, 현재 `manufacturing_agent_v6.ipynb`는 단순 Router가 아니라 **Gate-driven Plan-and-Execute 변형 구조**로 보는 것이 맞다.

다만 아직 **Hierarchical Supervisor**나 **LLM Compiler 기반 병렬 fan-out 구조**까지 구현된 것은 아니다. 현재 구조는 `supervisor_planner`가 task와 task별 `params/success_criteria`를 만들고, `orchestrator_dispatcher`가 graph route를 순차적으로 결정하며, 각 worker와 gate가 artifact/report를 주고받는다. Gate가 “같은 params retry로는 어렵다”고 판단하면 `supervisor_replanner`가 실패 task만 patch하고 downstream final task를 invalidate한다.

### 2.1 Orchestrator로 볼 수 있는 이유

현재 구조가 Router가 아니라 Orchestrator인 이유는 다음과 같다.

| 판정 기준 | 현재 구현 여부 | 근거 |
|---|---:|---|
| 요청을 task로 분해한다 | 예 | `supervisor_planner`가 `prediction`, `sql`, `evidence`, `final_answer` task를 만든다 |
| task 실행 파라미터를 고정한다 | 예 | `TaskSpec.params`에 SQL query type, evidence focus, retrieval profile 등을 저장한다 |
| task 상태를 가진다 | 예 | `ExecutionPlan.tasks[*].status`가 `PENDING`, `PASS`, `FAIL`, `NEEDS_USER_INPUT` 등을 가진다 |
| worker artifact를 수집한다 | 예 | `prediction_result`, `evidence_bundle`, `sql_result`, `artifacts`에 typed artifact 저장 |
| gate 결과를 반영한다 | 예 | `prediction_gate`, `sql_gate`, `evidence_gate`, `output_safety_gate`가 `GateReport`를 남긴다 |
| retry/recovery를 graph 레벨에서 판단한다 | 예 | gate가 직접 재실행하지 않고 `orchestrator_dispatcher`가 retry 여부와 다음 route를 결정한다 |
| worker가 최종 답변을 직접 만들지 않는다 | 예 | worker는 artifact만 만들고, `final_answer`가 answer_context를 만든 뒤 최종 문장을 작성한다 |
| LLM이 매번 자유롭게 다음 tool을 고르지 않는다 | 예 | `orchestrator_dispatcher`는 LLM을 쓰지 않는 deterministic state manager다 |

따라서 현재 구조는 다음으로 분류하는 것이 가장 정확하다.

```text
Custom LangGraph Workflow
+ SupervisorPlanner
+ SupervisorPlan-based OrchestratorDispatcher
+ Targeted SupervisorReplanner
+ Worker Subgraphs/Nodes
+ Evaluator Gates
+ Typed Artifacts
+ SQL/RAG/Prediction specialized workers
```

### 2.2 단순 Router가 아닌 이유

단순 Router라면 `supervisor` 또는 `router`가 매번 `next_node` 하나만 고르고 끝난다.

현재 구조는 다르다.

- `next_node`를 직접 고르는 것이 아니라 `ExecutionPlan`의 task 상태를 기준으로 다음 실행을 결정한다.
- `prediction`, `sql`, `evidence`가 모두 필요한 복합 질문이면 여러 worker를 순차 실행한다.
- worker 완료 여부는 artifact 존재 여부가 아니라 gate report와 task status로 판단한다.
- 실패한 worker는 gate가 아니라 dispatcher가 retry/recovery 대상으로 본다.
- 최종 답변 진입은 “어떤 artifact가 생겼는가”가 아니라 “필수 task가 완료/차단/입력요청 상태로 수렴했는가”에 의해 결정된다.

즉 현재 `orchestrator_dispatcher`는 단순 routing 함수가 아니라 **task lifecycle manager**에 가깝다. Plan-and-Execute 관점에서는 `SupervisorPlanner`가 최초 plan을 만들고, dispatcher가 execute 상태를 관리하며, gate가 replan 필요성을 보고하고, `SupervisorReplanner`가 특정 task만 patch한다.

### 2.3 God Supervisor가 아닌 이유

현재 구조는 God Supervisor도 아니다.

God Supervisor라면 supervisor가 다음을 모두 직접 판단하거나 생성한다.

- 위험 진단
- 문서 검색
- SQL 생성/실행
- safety 판단
- 최종 답변 생성
- retry 판단

현재는 책임이 분리되어 있다.

| 책임 | 담당 |
|---|---|
| 요청 intake와 1차 안전 판단 | `intake_gate` |
| 의미 기반 task 생성 | `supervisor_planner` |
| route와 task 상태 전이 | `orchestrator_dispatcher` |
| rule-based 위험 진단 | `prediction_agent` |
| 정형 이력 조회 | `sql_agent` |
| 문서 RAG와 citation | `evidence_agent` |
| artifact 품질 검증 | 각 worker별 gate |
| 최종 답변 조합 | `final_answer` |
| 답변 표현 안전성 검사 | `output_safety_gate` |
| 장기 저장 | `memory_writer` |

따라서 중앙 orchestrator는 “모든 판단을 직접 수행하는 agent”가 아니라 “task 상태와 route를 관리하는 graph controller”로 제한되어 있다.

### 2.4 ReAct 구조가 아닌 이유

현재 구조는 ReAct Agent가 아니다.

- LLM이 `thought → action → observation` 루프를 돌지 않는다.
- LLM이 임의의 tool을 반복 선택하지 않는다.
- tool 결과를 보고 같은 LLM agent가 계속 다음 tool을 고르는 구조가 아니다.
- 실행 가능한 node는 LangGraph edge와 `TASK_TO_NODE` mapping 안에 제한된다.
- task retry도 LLM tool loop가 아니라 gate report와 retry count로 제어된다.

LLM은 다음 위치에서만 typed decision 또는 요약에 사용된다.

- `intake_gate`: 요청 처리 가능 여부와 위험 요청 판단
- `context_manager`: 멀티턴 context carryover 판단
- `supervisor_planner`: 필요한 task 목록과 task별 params/success criteria 판단
- `sql_agent`: PydanticAI Text-to-SQL로 read-only SQLite SELECT 조회문 생성
- `evidence_agent`: 검색 문서 요약. `docs=0` 또는 `LOW_RELEVANCE`에서는 요약 LLM 미사용
- `output_safety_gate`: 최종 답변 안전성 판단

### 2.5 아직 부족한 점

현재 구조는 오케스트레이터 구조로 볼 수 있지만, 다음 한계가 남아 있다.

| 항목 | 현재 상태 | 개선 방향 |
|---|---|---|
| 병렬 실행 | 아직 순차 dispatcher 중심 | `prediction`, `sql`, `evidence` dependency가 없을 때 LangGraph fan-out 병렬화 가능 |
| Hierarchical Supervisor | 아님 | SQL/RAG/Safety가 더 커지면 각 subgraph 내부에 local planner/gate 추가 |
| LLM Compiler | 전체 구조에는 미적용 | evidence query expansion, SQL multi-query planning 같은 제한 영역에만 적용 가능 |
| state 직렬화 | Pydantic 객체가 checkpointer warning 유발 | state에는 dict 저장, node 진입 시 Pydantic 재검증 |
| quality evaluation | gate 중심 | 답변 품질용 `quality_gate`를 별도 추가 가능 |
| recovery 정책 | 기본 retry 중심 | retry 실패 시 제한 답변, 추가 입력 요청, escalation policy를 더 명확히 분리 |

정리하면, 현재 구조는 **Router Pattern을 넘어선 Orchestrator-Worker 구조**가 맞다. 다만 “고도화된 계층형 멀티 에이전트 시스템”이라기보다는, 현 시점에서는 **명시적 LangGraph workflow 위에 SupervisorPlan 기반 orchestrator를 얹은 1단계 실무형 구조**로 보는 것이 가장 정확하다.

## 3. 주요 저장 계층

| 저장 위치 | 용도 | 대표 데이터 |
|---|---|---|
| LangGraph `state` | 한 번의 graph 실행 중 노드 간 공유되는 working memory | `context_packet`, `execution_plan`, `prediction_result`, `evidence_bundle`, `sql_result`, `gate_reports`, `final_answer` |
| `artifacts` dict in state | 장기적으로 worker 산출물을 통합 관리하기 위한 표준 artifact map | `artifacts["prediction"]`, `artifacts["evidence"]`, `artifacts["sql"]` |
| LangGraph `messages` | checkpointer가 복원할 수 있는 대화 메시지 히스토리 | `HumanMessage`, `AIMessage` |
| `ConversationStore` SQLite | 도메인 장기 메모리 | user/assistant turn, thread별 설비 feature, prediction/evidence/sql 요약 |
| `RunStore` SQLite | 실행 관측/감사 로그 | `request_id`, `user_id`, `thread_id`, gate report, retry count |
| LangGraph checkpointer | 동일 `thread_id` 세션의 working state 복원 | graph state snapshot |
| Chroma Vector DB | 문서 RAG 검색 인덱스 | Haas/OSHA/KOSHA 문서 chunk, embedding |
| SQLite failure history DB | 제조 고장 사례 조회용 정형 DB | `failure_history` |

현재 SQLite mock 데이터는 `failure_history` 단일 테이블에 들어 있다.

```text
failure_history: 샘플 고장 사례, 원인, 조치, 재발 방지, downtime, 관련 feature JSON
```

## 4. State 구조

`ManufacturingState`는 `MessagesState`를 상속한다. 즉, LangGraph의 `messages` 누적 기능을 그대로 사용하면서 제조 도메인 필드를 추가한다.

주요 필드:

| State 필드 | 의미 |
|---|---|
| `request_id`, `thread_id`, `user_id` | 실행/세션/사용자 식별자 |
| `user_message` | 현재 사용자 질문 |
| `input_features` | 프론트나 테스트에서 들어온 구조화 설비 feature |
| `input_decision`, `input_flags`, `intake_decision` | intake 단계의 입력/안전 판단 결과 |
| `context_packet` | 현재 요청에 사용할 압축 context |
| `agent_contexts` | worker별로 포장된 context |
| `execution_plan` | task 목록, 상태, params, success criteria를 담은 실행 계획 |
| `supervisor_planner_decision` | LLM SupervisorPlanner의 typed decision |
| `orchestrator_decision`, `active_task_id`, `route` | dispatcher의 다음 실행 판단 |
| `prediction_result` | prediction worker의 rule-based 위험 진단 artifact |
| `evidence_bundle` | RAG 문서 근거 artifact |
| `sql_result` | SQL 이력 조회 artifact |
| `artifacts` | worker artifact 통합 저장소 |
| `gate_reports` | 각 gate의 PASS/FAIL/RETRY 판단 |
| `retry_counts` | worker별 실행 횟수 |
| `final_answer` | 최종 답변 artifact |
| `run_trace` | 실행 추적 확장용 필드 |

## 5. Node별 역할, LLM 사용, 저장 위치

| Node | 역할 | LLM 사용 | State 저장 | Store/DB 저장 |
|---|---|---:|---|---|
| `intake_gate` | 입력 공백, 프롬프트 인젝션, 제조 도메인 여부, 위험 실행 요청을 1차 판정 | 사용함. `_llm_intake()`가 `IntakeDecision` 생성. 단, injection/위험 실행 backstop은 deterministic guardrail도 사용 | `input_decision`, `input_flags`, `intake_decision`, `gate_reports`, 통과 시 `messages`에 user message | 없음 |
| `context_manager` | 장기 메모리와 checkpointer message를 읽어 현재 요청용 context 구성 | 사용함. `_llm_context_carryover()`가 멀티턴 후속 질문과 이전 artifact 참조 여부 판단 | `context_packet`, `agent_contexts` | `ConversationStore`에서 최근 turn, feature, summary 조회 |
| `supervisor_planner` | 사용자 요청을 `prediction`, `sql`, `evidence`, `final_answer` task로 분해하고 task별 `params/success_criteria`를 고정 | 사용함. `_llm_supervisor_planner_decision()` 1회로 `SupervisorPlannerDecision` 생성 후 deterministic validator는 structured output의 계약만 정규화 | `execution_plan`, `supervisor_planner_decision`, `active_task_id`, `artifacts`, `intent` | 없음 |
| `orchestrator_dispatcher` | task 상태, dependency, gate report, retry count를 보고 다음 worker route 결정 | 사용하지 않음. deterministic state manager | `execution_plan`, `orchestrator_decision`, `active_task_id`, `route`, `agent_feedback` | 없음 |
| `prediction_agent` | ML이 아니라 rule-based diagnostic / partial risk assessment 수행 | 사용하지 않음. summary도 deterministic 문장 생성 | `prediction_result`, `artifacts["prediction"]` | 없음 |
| `prediction_gate` | `PredictionResult` 존재 여부, status, 입력 부족 여부 검증 | 사용하지 않음 | `gate_reports` | 없음 |
| `sql_agent` | PydanticAI Text-to-SQL-only adapter로 정형 이력 조회용 read-only SQLite SELECT 생성 | 사용함. route/planning은 `SupervisorPlanner`의 task params를 따르고, PydanticAI는 Text-to-SQL 생성만 담당 | `sql_result`, `artifacts["sql"]` | SQLite history DB 조회 |
| `sql_gate` | SQL artifact와 SQL 안전 정책 검증 | 사용하지 않음 | `gate_reports` | 없음 |
| `evidence_agent` | Chroma RAG 검색, citation 생성, 문서 근거 요약 | 사용함. 검색 자체는 deterministic RAG, score 기준 미달은 `LOW_RELEVANCE`, `docs=0` 또는 `LOW_RELEVANCE`면 요약 LLM 미사용. retrieved doc의 prompt injection 의심 문구는 sanitize | `evidence_bundle`, `artifacts["evidence"]` | Chroma Vector DB 조회 |
| `evidence_gate` | 문서 근거 artifact 품질 검증. 명시적 근거 요청인데 EMPTY면 retry 유도 | 사용하지 않음 | `gate_reports` | 없음 |
| `final_answer` | prediction/evidence/sql artifact를 사용자용 answer_context로 압축한 뒤 최종 답변 생성. raw SQL row, JSON, 내부 state는 출력하지 않음 | 사용함. 단 LLM에는 raw artifact가 아니라 압축된 answer_context만 전달 | `final_answer`, final task status update | 없음 |
| `output_safety_gate` | 최종 답변 직후 위험 실행 표현, 안전장치 우회, 과신 표현을 검사하고 차단 여부를 기록 | 사용함. 단 deterministic unsafe-output backstop을 LLM judge 전/후에 강제 적용 | `final_answer` 갱신 가능, `gate_reports` | 없음 |
| `memory_writer` | 대화, 현재 feature, artifact 요약, 실행 trace 저장 | 사용하지 않음 | `messages`에 assistant answer 추가 | `ConversationStore`, `RunStore`에 저장 |

## 6. Intake와 Output Safety의 분리

현재 구조는 초반 `input_gate`와 `safety_gate`를 하나의 `intake_gate`로 합쳤다.

`intake_gate` 책임:

- 빈 입력 차단
- 프롬프트 인젝션 차단
- 제조 도메인 여부 판단
- 위험 실행 요청인지 판단
- 안전 자문으로 답변 가능한 질문인지 판단
- 통과/차단 결과를 `InputDecision`, `IntakeDecision`, `GateReport`로 남김

`output_safety_gate` 책임:

- 이미 생성된 `final_answer.answer`만 검사
- 점검 없는 재가동 승인, 안전장치 우회, 알람 무시 운전 같은 위험 표현 억제
- 차단 시 `GateReport`에 `BLOCK`과 reason을 남김
- deterministic unsafe-output backstop을 먼저 적용하고, 통과한 경우에만 LLM judge를 호출하며, LLM 이후에도 같은 backstop을 다시 적용
- 대체 답변에도 위험 실행 지시가 남으면 `SAFETY_BLOCK_MESSAGE`로 교체한다. 테스트 통과 목적의 시나리오별 고정 치환은 하지 않음

즉, 앞단은 “요청을 처리해도 되는가”를 보고, 뒷단은 “생성된 답변 표현이 안전한가”를 본다.

## 7. Context 전달 방식

`context_manager`는 세 출처를 합쳐 `ContextPacket`과 `agent_contexts`를 만든다.

1. 현재 입력
   - `user_message`
   - `input_features`
   - 자연어에서 추출한 설비 feature

2. 장기 메모리
   - `ConversationStore.recent_turns()`
   - `ConversationStore.latest_machine_values()`
   - `ConversationStore.latest_summary(kind="prediction"|"evidence"|"sql")`

3. checkpointer message
   - `state["messages"]`에서 최근 `HumanMessage`/`AIMessage`를 추출
   - 장기 store의 recent turns에 보조적으로 합침

LLM은 여기서 `ContextCarryoverDecision`을 만든다. 예를 들어 현재 질문이 이전 SQL/evidence/prediction artifact를 실제로 참조하는지 판단한다. 이 decision은 `referenced_artifacts`, `uses_previous_*`, 추론된 기간과 이전 artifact 참조 여부만 담으며, SQL query type이나 evidence focus 같은 task planning 결과는 만들지 않는다.

SQL DB는 더 이상 설비별 로그 DB가 아니다. 따라서 ContextManager는 이전 대화의 식별자성 표현을 SQL 조건으로 전달하지 않는다. 이전 SQL artifact나 이전 prediction artifact가 현재 질문에 참고되는지만 표시하고, 고장 유형/부품/증상/기간을 어떻게 조회할지는 SupervisorPlanner와 SQLAgent의 typed contract가 담당한다.

이전 맥락은 같은 `thread_id` 안에서 항상 전달한다. `ContextPacket.recent_turns_summary`, `previous_prediction_summary`, `previous_sql_summary`, `previous_evidence_summary`는 SupervisorPlanner와 필요한 worker에 참고 맥락으로 제공된다.

다만 “전달”과 “현재 조건으로 적용”은 분리한다. 이전 feature는 prediction의 stale context로 참고될 수 있지만, SQL은 `failure_history`의 고장 사례 기준으로만 조회한다. 즉 thread 맥락은 항상 보이고, SQL 조회 조건은 failure type, component, symptom, root cause, action, 기간 같은 고장 이력 필드로 제한된다.

## 8. SupervisorPlanner와 OrchestratorDispatcher

`supervisor_planner`는 LLM으로 `SupervisorPlannerDecision`을 생성하고, 그 뒤 deterministic validator는 structured output의 계약만 정규화한다. 이 validator는 사용자 문구를 정규식으로 다시 분류해 `needs_sql`, `needs_evidence`, `sql_query_intents`를 강제로 켜지 않는다. 의미 판단과 계획 수립은 SupervisorPlanner LLM의 책임이다. SQL을 실행하거나 RAG 검색을 하거나 최종 답변을 작성하지 않는다.

예시:

```json
{
  "intent": "combined_analysis",
  "needs_prediction": true,
  "needs_evidence": true,
  "needs_sql": true,
  "evidence_required": true,
  "sql_query_intents": ["similar_incidents", "failure_history", "corrective_actions"],
  "evidence_focus": ["점검 문서 근거", "해결 방법"]
}
```

이 decision은 `ExecutionPlan`으로 변환된다. 중요한 점은 decision의 세부 판단을 `TaskSpec.params`와 `TaskSpec.success_criteria`에 박제한다는 것이다.

```text
prediction_1: PENDING
sql_1: PENDING
evidence_1: PENDING
final_1: PENDING, depends_on=[prediction_1, sql_1, evidence_1]
```

예시 task params:

```text
prediction_1.params:
  diagnosis_mode=current_or_partial
  allow_partial=true
  allow_stale_context=false

sql_1.params:
  query_types=[similar_incidents, failure_history, corrective_actions]
  failure_type=TWF 또는 null
  default_time_window_days=30

evidence_1.params:
  retrieval_profile=prediction_plus_rag
  evidence_required=true
  focus=[점검 절차, 재발 방지, 공구 마모]
  min_docs=2
  require_citation=true
```

`orchestrator_dispatcher`는 LLM을 쓰지 않는다. 다음만 수행한다.

- 마지막 `GateReport`를 `ExecutionPlan.tasks[*].status`에 반영
- `RETRYABLE_FAIL`이면 retry count를 올리고 task를 `PENDING`으로 되돌림
- dependency가 충족된 다음 `PENDING` task를 선택
- `TASK_TO_NODE` mapping으로 route 결정
- 실행 가능한 worker가 없으면 `final_answer`로 보냄

`orchestrator_dispatcher`는 plan이 없을 때 planner를 직접 호출하지 않는다. graph상 `supervisor_planner → orchestrator_dispatcher`가 보장되어야 하며, plan이 없으면 구조 오류로 본다.

### 8.1 Targeted Replan

현재 replan은 전체 plan을 다시 만드는 방식이 아니다. Gate가 `PLAN_REPAIR_REQUIRED`를 남긴 경우에만 `supervisor_replanner`가 호출되고, 실패한 task의 `params`와 `success_criteria`만 patch한다.

```text
WorkerAgent
→ Gate(status=PLAN_REPAIR_REQUIRED)
→ OrchestratorDispatcher(action=REPLAN)
→ SupervisorReplanner
→ patched ExecutionPlan
→ OrchestratorDispatcher
→ failed task targeted rerun
```

Replan 계약:

- `retry_count`: 같은 params로 worker를 다시 실행한 횟수
- `rerun_count`: `supervisor_replanner`가 params를 patch한 뒤 다시 실행한 횟수
- `max_reruns`: targeted replan 무한 루프 방지
- `plan_revision`: plan patch가 발생할 때 증가
- `invalidated_by`: upstream task patch 때문에 다시 생성해야 하는 downstream task 표시

현재 deterministic replanner rule:

| 대상 task | 조건 | patch |
|---|---|---|
| `evidence` | retry 후에도 `EMPTY` 또는 `LOW_RELEVANCE` | `retrieval_profile=fallback_broad`, focus에 gate feedback 추가, `min_docs=1` |
| `sql` | Text-to-SQL 검증/실행 실패 또는 SQL policy 차단 | `repair_hint`, `strict_schema_check=True` 추가 |

`PATCH_AND_RERUN`이 발생하면 `final_1.status`는 다시 `PENDING`이 되고 `invalidated_by`에 upstream task id가 남는다. 즉 upstream artifact가 바뀌면 기존 final answer를 재사용하지 않는다.

중요한 경계:

- `orchestrator_dispatcher`는 replan 내용을 직접 만들지 않는다.
- `supervisor_replanner`는 worker를 직접 실행하지 않는다.
- `supervisor_replanner`는 새 task를 추가하지 않고 기존 task patch만 수행한다.
- Prediction `NEEDS_INPUT`은 replan하지 않고 final answer에서 입력 부족을 안내한다.

핵심 경계:

```text
SupervisorPlanner = 무엇을 할지 결정
OrchestratorDispatcher = 정해진 일을 어떤 순서로 실행할지 관리
Worker = 자기 task params를 읽고 artifact 생성
Gate = task success criteria 기준으로 artifact 검증
FinalAnswer = answer_context 기반 최종 답변 작성
```

현재 graph node 이름은 `supervisor_planner`이며, 별도 호환 alias는 두지 않는다.

## 9. Worker Artifact

### 9.1 PredictionResult

생성 node: `prediction_agent`

저장 위치:

- `state["prediction_result"]`
- `state["artifacts"]["prediction"]`

주요 내용:

- `status`: `OK`, `PARTIAL`, `NEEDS_INPUT`, `SKIPPED`, `FAIL`
- `available_features`, `missing_features`
- `risk_flags`
- `failure_types`
- `cause_features`
- `evidence_hints`
- `safety_hints`
- `used_stale_features`
- `summary`

주의:

- 이름은 `prediction`을 유지하지만 ML 예측이 아니다.
- 현재는 rule-based diagnostic / partial risk assessment다.

### 9.2 EvidenceArtifact

생성 node: `evidence_agent`

저장 위치:

- `state["evidence_bundle"]`
- `state["artifacts"]["evidence"]`

주요 내용:

- `status`: `OK`, `EMPTY`, `LOW_RELEVANCE`, `FAIL`
- `retrieval_profile`
- `queries`
- `documents`
- `citations`
- `evidence_summary`
- `limitations`

RAG 품질 정책:

- 검색 결과가 없으면 `EMPTY`
- 검색 결과가 있어도 score가 `MIN_EVIDENCE_SCORE` 미만이면 `LOW_RELEVANCE`
- `LOW_RELEVANCE`는 OK로 포장하지 않고 제한 답변 또는 retry 대상으로 처리
- citation에는 `source_id`, `source`, `type`, `chunk_index`, `snippet`, `score`, `security_flags`를 포함
- retrieved document에 `ignore previous instructions`, `system prompt`, `이전 지시 무시`, `안전 경고 제거` 같은 prompt injection 의심 문구가 있으면 summary LLM에 전달하기 전에 `[UNTRUSTED_INSTRUCTION_REMOVED]`로 치환

RAG source:

- Chroma Vector DB
- 문서 원천: Haas, OSHA, KOSHA 등

### 9.3 SQLHistoryArtifact

생성 node: `sql_agent`

저장 위치:

- `state["sql_result"]`
- `state["artifacts"]["sql"]`

주요 내용:

- `status`: `OK`, `EMPTY`, `INVALID_REQUEST`, `BLOCKED`, `FAIL`
- `query_type`
- `sql`
- `rows`
- `results`: 복합 SQL 조회 결과 목록
- `summary`: artifact/log/memory용 짧은 상태 요약. 사용자-facing 판단 근거는 아님
- `limitations`
- `error_message`

`SQLQueryResult`도 query type별 `status`, `limitations`, `error_message`를 가진다. 따라서 “최근 고장 이력 + 대응 조치 + 반복 패턴” 같은 복합 요청에서 한쪽은 `OK`, 다른 한쪽은 `EMPTY` 또는 `FAIL`인 상태가 전체 `OK` 뒤에 숨지 않는다. `sql_gate`는 result별 status를 diagnostics에 남기고, 전체 artifact가 `OK`여도 일부 result가 `OK`가 아니면 `PASS_WITH_WARNINGS`로 표시할 수 있다.

SQL Agent는 `TaskSpec.params`에 고정된 query type, 고장 유형 힌트, 기간, filter 조건과 허용 schema/policy를 입력으로 PydanticAI Text-to-SQL-only adapter를 호출한다. 이 worker의 LLM 사용 범위는 `failure_history` 테이블에 대한 SQLite SELECT SQL text 생성에 한정된다. 어떤 조회가 필요한지 계획하는 일은 `supervisor_planner`가 담당하고, 실행 순서와 retry는 LLM-free `orchestrator_dispatcher`가 담당한다.

SQL query type 정책:

- `failure_history`: 최근 고장 사례를 날짜순으로 조회한다.
- `similar_incidents`: 현재 prediction의 failure type 또는 사용자 질문의 고장 유형과 유사한 과거 사례를 조회한다.
- `corrective_actions`: 과거 corrective/preventive action과 root cause를 중심으로 조회한다.
- `repeated_patterns`: failure type, component, root cause 등의 반복 패턴을 집계한다.

중요한 점은 SQL이 더 이상 설비 식별자 기반 이력 조회가 아니라는 것이다. `failure_history`는 고장 사례 단위 테이블이며, SQLAgent는 고장 유형, 부품, 증상, 원인, 대응 조치, 기간 기준으로만 조회한다. 현재 입력 feature 기반 판단은 `prediction_agent`가 담당하고, SQLAgent는 그 판단과 연결 가능한 과거 고장/정비 사례를 구조화해서 가져온다.

생성된 SQL은 실행 전에 `validate_sql_query()`와 `EXPLAIN QUERY PLAN`으로 검증한다. 검증은 SELECT-only, forbidden keyword, allowed table, LIMIT, 실제 schema/column 존재 여부를 계속 강제한다. 실행은 `execute_readonly_sql(sql, params, deps)`를 통해 readonly query로만 수행한다.

SQL source:

- SQLite DB: `agent_data/failure_history.sqlite`
- Schema seed file: `sql/failure_history_schema.sql`

## 10. Gate와 Recovery 정책

| Gate | PASS 기준 | Retry 기준 | Replan 기준 | 최종 답변으로 가는 경우 |
|---|---|---|---|---|
| `prediction_gate` | `PredictionResult.status in {"OK", "PARTIAL"}` | artifact 없음 또는 `FAIL` | 없음 | `NEEDS_INPUT`이면 `final_answer`에서 입력 부족 안내 |
| `sql_gate` | `SQLHistoryArtifact.status == "OK"`이고 result별 SQL policy 위반이 없음 | artifact 없음 등 단순 재시도 가능 실패 | Text-to-SQL 검증/실행 실패 또는 policy 차단이지만 `max_reruns` 여지가 있는 경우 | `EMPTY`, `INVALID_REQUEST`, `BLOCKED`는 제한 답변 또는 추가 입력 요청. 복합 결과 일부가 `OK`가 아니면 `PASS_WITH_WARNINGS` |
| `evidence_gate` | 문서와 citation이 있는 `OK` | 명시적 근거 요청인데 `EMPTY`/`LOW_RELEVANCE`이고 아직 retry가 아닌 경우 | retry 후에도 `EMPTY`/`LOW_RELEVANCE`이고 `max_reruns` 여지가 있는 경우 | replan 후에도 부족하면 경고 포함 답변 |
| `output_safety_gate` | 위험 실행 표현이 없는 최종 답변 | 해당 없음 | 해당 없음 | block 시 `GateReport`에 실패를 남기고, 안전한 대체 답변이 있을 때만 교체 |

Gate는 직접 worker를 호출하지 않는다. `GateReport`만 추가하고, dispatcher가 재실행 또는 다음 route를 결정한다.

Gate는 상위 `supervisor_planner_decision`을 직접 참조하지 않고, active task의 `params/success_criteria`를 기준으로 검증한다. 예를 들어 `evidence_gate`는 `evidence_required`, `min_docs`, `require_citation`을 기준으로 `PASS`, `PASS_WITH_WARNINGS`, `RETRYABLE_FAIL`, `PLAN_REPAIR_REQUIRED`를 결정한다.

## 11. Final Answer 조립 규칙

`final_answer_node`는 먼저 artifact를 deterministic하게 `answer_context`로 압축한 뒤, 최종 답변 작성용 LLM에 전달한다. 이때 LLM에는 raw SQL row, JSON, 내부 state, raw document chunk를 넣지 않는다.

작성 규칙:

- 답변은 결론, 현재 판단, 최근 이력 요약, 우선 점검 순서, 문서 근거, 주의사항 흐름으로 작성
- `prediction_result.status == OK`는 “위험 없음”이 아니라 “진단 완료”로 해석
- 위험 진단 artifact가 없으면 “현재 위험 진단은 별도로 수행되지 않음”이라고 표현하고, 위험 없음으로 단정하지 않음
- SQL은 `SQLQueryResult.rows`를 raw row로 나열하지 않고 핵심 패턴과 최대 2개 이하의 예시로 압축
- citation이 있으면 `[C1]`, `[C2]` 형태로 문서 근거에 반영
- 문서 근거가 부족하면 “현재 검색된 문서 근거만으로는 단정하기 어렵습니다”라고 표현
- 위험 운전 지속, 안전장치 우회, 점검 없는 재가동 승인 표현은 금지

## 12. Memory Writer 저장 내용

`memory_writer_node`는 마지막에 다음을 저장한다.

`ConversationStore`:

- `turns`
  - 현재 user message
  - 최종 assistant answer
- `machine_values`
  - 현재 입력에서 추출/전달된 feature만 저장
  - stale로 보완된 이전 값은 다시 저장하지 않음
- `summaries`
  - `prediction`: `PredictionResult.summary`
  - `evidence`: citation/source/query/summary를 압축한 문자열
  - `sql`: query type/table/row count/sample rows를 압축한 문자열. 멀티턴 context 회수용이며 최종 판단은 새 실행의 SQL rows를 우선 사용

`RunStore`:

- `request_id`
- `user_id`
- `thread_id`
- `gate_reports`
- `retry_counts`

LangGraph `messages`:

- 최종 assistant answer를 `AIMessage`로 추가
- 같은 `thread_id`로 invoke할 때 checkpointer 기반 대화 복원에 사용

## 13. RunnableConfig와 실패 후 이어 실행

노트북 실행 헬퍼는 `RunnableConfig`를 한 곳에서 만든다.

```python
make_runnable_config(
    user_id=user_id,
    thread_id=thread_id,
    request_id=request_id,
    checkpoint_ns="",
    recursion_limit=50,
)
```

핵심은 `thread_id`를 세션 checkpoint key로 안정적으로 유지하는 것이다. `request_id`는 실행 관측/감사용 식별자이며, 같은 turn을 이어 실행할 때도 같은 config 계열을 사용한다.

실행 중 OpenAI 호출, SQL 실행, RAG 요약 등에서 중간 실패가 발생하면 LangGraph checkpointer는 마지막 성공 superstep을 저장한다. 이때 새 state를 다시 넣으면 이전 checkpoint state를 덮어쓸 수 있으므로, 이어 실행은 다음 방식으로 한다.

```python
app.invoke(None, config=same_config)
```

노트북에는 이를 감싼 헬퍼가 있다.

- `checkpoint_status(thread_id, user_id, request_id)`: 현재 checkpoint의 남은 node, request, gate 수 확인
- `run_turn(..., resume_on_error=True, max_resume_attempts=1)`: 실행 중 실패하면 같은 config로 checkpoint resume 1회 시도
- `resume_turn(user_id, thread_id, request_id="resume")`: 이미 실패한 thread를 새 input 없이 checkpoint에서 이어 실행

주의할 점:

- 새 사용자 요청은 `make_initial_state(...)`로 시작한다.
- 실패한 동일 요청을 이어 실행할 때는 새 state를 넣지 않고 `None` input으로 resume한다.
- `checkpoint_ns`는 branch를 나누고 싶을 때만 사용한다. 기본값은 기존 checkpoint와 호환되도록 빈 문자열이다.

## 14. LLM 사용 위치 요약

| 위치 | LLM 사용 목적 | 자유로운 tool loop 여부 |
|---|---|---:|
| `intake_gate` | 입력/안전 intake 판단 | 없음 |
| `context_manager` | 후속 질문과 이전 artifact 참조 판단 | 없음 |
| `supervisor_planner` | 필요한 task 목록과 task params typed planning | 없음 |
| `sql_agent` | PydanticAI Text-to-SQL로 read-only SQLite SELECT 생성 | 없음. graph node 안에서만 실행 |
| `evidence_agent` | 검색 문서 요약. `docs=0` 또는 `LOW_RELEVANCE`이면 LLM 호출 없음 | 없음 |
| `output_safety_gate` | 최종 답변 안전성 판단 | 없음 |
| `prediction_agent` | 사용하지 않음 | 없음 |
| `orchestrator_dispatcher` | 사용하지 않음 | 없음 |
| `final_answer` | answer_context 기반 최종 답변 작성 | 없음 |
| `memory_writer` | 사용하지 않음 | 없음 |

## 14. 현재 검증 시나리오

`scripts/run_manufacturing_scenarios.py`는 기존 기능 시나리오에 더해 구조 경계 회귀 테스트를 포함한다. 노트북 하단 시나리오 셀은 기존 기능 시나리오를 실행한다.

커버 범위:

- prompt injection 방어
- 위험 실행 요청 차단
- 안전 자문 처리
- 현재 위험 진단 + 과거 이력 + 문서 근거 복합 질문
- SQL-only 고장 이력/조치 조회
- 고장 유형별 반복 패턴 조회
- out-of-scope 차단
- 입력 feature 부족
- 멀티턴 stale context
- output safety 직접 검증
- 입력 feature + 유사 고장 이력 + 문서 근거
- 특정 failure type 이력/절차 근거
- unknown failure empty result
- 정비 문서 요청 안의 prompt injection
- 멀티턴 combined follow-up
- SQL artifact follow-up
- Evidence artifact follow-up

최근 검증 결과:

```text
Scenario result: 17/17 passed
```

## 15. 주의할 기술부채

현재 LangGraph checkpointer 실행 중 커스텀 Pydantic 객체 역직렬화 warning이 발생할 수 있다.

의미:

- 지금 실행 실패는 아니다.
- 향후 `LANGGRAPH_STRICT_MSGPACK=true` 같은 strict 환경에서는 문제가 될 수 있다.

개선 방향:

- checkpointer에 저장되는 Pydantic 객체를 `model_dump()` 기반 dict로 직렬화
- 복원 시 필요한 노드에서 다시 Pydantic model로 validate
- 특히 `ExecutionPlan`, `ContextPacket`, `PredictionResult`, `EvidenceArtifact`, `SQLHistoryArtifact`, `FinalAnswer`를 우선 검토
