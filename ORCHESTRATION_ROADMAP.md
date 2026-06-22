# 오케스트레이션 개선 로드맵 (v6 이후)

> 작성: 2026-06-22 · 대상: `manufacturing_agent_v6.ipynb`
> 이 문서는 v6 딥 재설계 이후 "다음에 무엇을, 왜, 어떻게" 고칠지에 대한 **우선순위 전략**이다.
> 핵심 원칙: 이 시스템은 *안전 게이트가 달린 제조 진단·자문 에이전트*다.
> 실패의 정의는 "느림"이 아니라 **틀린 답 / 근거 없는 단정 / 위험 명령 통과**다. 우선순위는 이 기준을 따른다.

---

## 0. 현재 상태 (이번 세션에서 끝낸 것 = 출발점)

**구조 재설계** — `graph/orchestrator`(단일 437줄)를 책임별 4개 셀로 분리:
- `plan_ops` — `PlanOps` 상태머신 엔진. ExecutionPlan을 불변으로 다루는 순수 함수
  (`next_runnable`, `apply_gate_report`(표 기반 전이), `reset_orphan_running`, `mark_running`, `task_by_id`, `deps_terminal`). **plan 전이의 단일 출처.**
- `planner` — 의도 → ExecutionPlan (`supervisor_planner_node`).
- `replanner` — targeted plan repair (`deterministic_replanner_decision`, `apply_replanner_decision`, `supervisor_replanner_node`).
- `dispatcher` + `route_policy` — PlanOps에만 의존하는 얇은 라우터 (`orchestrator_dispatcher`, `route_after_*`).

**버그 수정** — SQLite 커넥션 누수, RAG 점수 L2→코사인 재빌드 + 임계값 0.2, intake JSON 파싱 견고화,
evidence `FAIL` 처리, 성공 진단에 "[입력 부족]" 오삽입 차단, `type` 오탐, prediction `float` 가드,
canonical 피처명 추출, memory_writer 가드, 죽은 코드 제거, `.env` 파서, **`call_llm` 지수 백오프(429 대응)**.

**검증** — 결정적(T18/T20/PlanOps) + 라이브 대표 4종(인젝션 차단 / 안전자문+RAG / 구조화 예측 / SQL) 통과.

**이번 세션 추가 완료(2026-06-22)** — 1-B(모델 tier) · #2(artifacts 제거) · 1-A(hybrid replanner) · 답변에 수치 반영(prediction 계산값·SQL 집계). 각 단계 후 정적+대표 4종 재검증 통과.
- 참고: `downtime_min`은 **기존 failure_history 컬럼**이며 스키마 변경 없음 — 답변 텍스트에 노출만 추가함.

**답변 구조 개선(2026-06-22, "더 좋은 구조")** — 계산·수치는 **코드가 결정적으로 렌더**, LLM은 헤드라인·해석·문서 종합만.
- `FailureRisk.rule/formula` 추가 + `compute_partial_risks`가 계산식 문자열 생성(예: `215 × 62 = 13,330 ≥ 임계(M) 12,000 → 초과(비율 1.11)`).
- `_render_diagnosis_block`/`_render_checklist`/`_ensure_diagnosis_block`: 「고장 종류별 근거」(🔴/🟡/🟢 규칙·계산·영향 변수) + 「지금 점검할 일」을 인용 앞에 결정적으로 주입(숫자 hallucination 불가).
- 프롬프트: LLM은 계산표/체크리스트를 만들지 말고 숫자도 새로 만들지 말 것 — 시스템이 정확값으로 첨부.
- 시나리오: 화면 구조(질의입력란/데이터입력란)에 맞춰 **여러 수치는 `input_features`로 이동**(T13 turn1 분리, T16/T21/T04 질의문 수치 제거). 단일 값 질의(T08/T09)는 원형 유지.

**답변 틀 전면 교체 + 실행 기반 버그 수정(2026-06-22, 시나리오 전수 실행 검토)**
- 새 틀: 제목 → `🔴/🟡/🟢 종합 판단` 배너 → LLM 헤드라인 1~2문장 → 현재 위험 진단(규칙/계산/영향변수, 코드) → 과거 고장 이력(건수/유형/다운타임, 코드) → 문서 근거(evidence_summary) → 지금 점검할 일(코드) → [출처] → 안전 footer.
- 실행으로 잡은 버그: ① output_safety 오차단("재가동을 **피하세요**"의 '하세요' 오매칭 → 부정문맥 가드) ② 멀티턴 크래시(체크포인트 dict 복원 → context_manager `model_validate` 보정) ③ OSF "이내인데 🔴높음" → 임계 기반 레벨(초과/한계근접/이내) ④ intake/planner를 mini→gpt-4o 복귀(scope 오분류·prediction 과triggering 해소; carryover/resolution는 mini 유지) ⑤ evidence LLM 환각(이력 유무 언급·약어 오역·입력값 날조) 프롬프트 차단 ⑥ **데이터 입력란 분리로 생긴 intake 오차단**: "입력한 데이터로~"+구조화입력을 intake LLM이 빈 입력으로 오판 → 구조화 입력 존재 시 서비스 판정 보정.
- 검토: 14개 사용자 시나리오 전수 실행(서브에이전트 분석) → S1~S12 전부 기대 동작 확인.

**보류(2026-06-22 사용자 결정)** — 2-B 에이전트 병렬(Send fan-out): 답변 품질과 무관(복합 질문 속도만), 회귀 위험 커서 미진행. 필요해지면 §2.5 설계 메모부터.

**1-B 모델 tier 사실상 철회(2026-06-22 사용자 결정 "전부 원래대로 모델 올려")** — mini가 scope 오분류(S4)·prediction 과triggering(S9~11)·context 누락(T04 "입력 부족")을 반복해 **모든 call_llm을 default(gpt-4o)로 복귀**. `final` tier의 max_tokens=2048(답변 잘림 방지)만 유지. classifier(mini) 호출 0개.

**UX 다듬기(2026-06-22, 비개발자 독자 기준 검토)** — ① [출처] 영어 raw/공백손상 PDF snippet 숨김(가독성 있는 한글만 짧게) ② 과거 고장 이력 세미콜론 한 줄 → 불릿(총건수·다운타임·유형·영역·대표사례·대표조치) ③ 제목+배너 중복 제거(배너만) ④ 🟢낮음 위험 한 줄로 접기(노이즈↓) ⑤ 안전 footer 조건부(위험 진단에만 전체, 단순 조회는 축약) ⑥ HDF "충족/미충족"→"위험/정상", "임계(M)"→"M타입 한계" ⑦ 차단 메시지 톤 통일(이유+대안). + **구조화 입력은 prediction에 무조건 우선 반영**(T04 '입력 부족' 근본 수정).

**남은 다듬기(낮음)** — 동일 의도 멀티턴의 이력 건수 미세 차이(질의 표현/날짜창 차이), 체크리스트 우선순위 강조, 인용 과다.

> 재설계 덕분에 §2.5의 Phase 2 작업은 대부분 "엔진에 메서드 1~2개 추가" 수준으로 줄어든 상태로 남겨둔다.

---

## 1. 우선순위 결론 (요약)

| 순위 | 항목 | 가치 | 위험 | 노력 | 판정 |
|---|---|---|---|---|---|
| **1** | **1-B 모델 tier** (mini/4o + max_tokens) | 비용↓↓ + 잠재버그 수정 | 낮~중 | 0.5d | ✅ **완료(2026-06-22)** |
| **2** | **artifacts dict 제거** (2-A의 절반) | 상태 단순화·버그군 제거 | 낮 | 0.5d | ✅ **완료(2026-06-22)** |
| **3** | **1-A hybrid LLM replanner** | 어려운 케이스 답 품질↑ | 낮 | 0.5~1d | ✅ **완료(2026-06-22)** |
| **+** | **답변에 수치 반영** (prediction 계산값·SQL 집계) | 유저 체감 품질↑ | 낮 | 0.5d | ✅ **완료(2026-06-22)** |
| **4** | **eval + 안전 적대 회귀** (리스트 밖) | 진짜 사고 지점 방어 | 중 | 1~2d | **강력 권장 (미착수)** |
| 5 | 2-A reducer 부분 | 2-B 선결조건 | 중 | 0.5d | 2-B 할 때만 |
| 6 | **2-B Send fan-out (병렬)** | combined 지연↓(제한적) | 높 | 2~3d | ⛔ **보류 — 2026-06-22 사용자 결정**: 답변 품질과 무관(속도만), 회귀 위험 커서 미진행 |

**한 줄 결론: 1-B → artifacts 정리 → 1-A 까지가 "확실히 남는" 작업이고, 그 다음 투자는 2-B(병렬화)가 아니라 eval + 안전 회귀로 가야 한다.**

---

## 2. 실행 항목 (권장 순서대로)

### 2.1 [1순위] 1-B — 모델 tier  ✅ 완료(2026-06-22)
> 적용됨: `call_llm(system, user, *, tier=...)` + tier별 client(`classifier=gpt-4o-mini`, `default=gpt-4o`, `final=gpt-4o/max_tokens 2048`).
> 호출부: intake/planner/carryover/resolution → `classifier`; final_answer(2곳) → `final`; output_safety·evidence_summary → `default` 유지.
> 검증: 결정적(T18/T20/PlanOps) + 대표 4종 통과(분류기 mini로도 인젝션 차단·라우팅 정확).

**왜 중요한가**
- intake / planner / carryover / sql_intent는 *분류기*다. gpt-4o가 과하다 → mini로 내리면 비용 ~10배↓, 지연↓, 품질 손실 거의 없음.
- 덤: 기존 `max_tokens=1024`가 긴 최종답변을 자르는 **실재 버그**를 `final` tier(2048)로 함께 해소.

**어떻게** (`call_llm` 정의 셀)
```python
# keyword-only 'tier' 추가 → 하위호환. tier별 client 캐시. retry/backoff는 모든 tier 공통 적용.
_LLM_TIERS = {
    "classifier": {"model": "gpt-4o-mini", "max_tokens": 1024},
    "default":    {"model": DEFAULT_MODEL, "max_tokens": 1024},
    "final":      {"model": DEFAULT_MODEL, "max_tokens": 2048},
}
_llm_clients = {}  # tier -> ChatOpenAI (lazy)

def call_llm(system: str, user: str, *, tier: str = "default") -> str:
    ...  # tier로 client 선택, 기존 지수 백오프 루프 그대로 감싼다
```
**적용 위치**
- `tier="classifier"`: intake_gate, supervisor_planner, context_carryover, sql_intent
- `tier="final"`: final_answer_node
- `tier="default"` 유지: **output_safety_gate(절대 mini 금지 — T01/T02/T12 1차 방어선)**, evidence_summary(근거 왜곡 위험)

**함정** — 시그니처부터 바꾸고 호출부에 tier를 **하나씩** 적용하며 테스트. 한 번에 다 바꾸지 말 것.
**검증** — 안전 T01/T02/T07/T12, 라우팅 T04/T13/T21, SQL T05/T06/T14/T19.

---

### 2.2 [2순위] artifacts dict 제거 (2-A의 절반만 선행)  ✅ 완료(2026-06-22)
> 적용됨: worker는 typed 필드만 반환, 소비처는 typed 필드만 읽음, `state["artifacts"]`·TypedDict 필드·`artifacts:{}` 리셋 제거. 정적+대표 4종 통과.

**왜 중요한가**
- `state["artifacts"]["prediction"/"evidence"/"sql"]`는 typed 필드 `prediction_result`/`evidence_bundle`/`sql_result`의 **중복 거울**이다(모든 소비처가 이 3개 key로만 접근).
- 이중 상태 = "어느 게 진짜냐" 버그군의 원천. 제거하면 코드가 단순해지고, **동시에 Phase 2 병렬화의 가장 큰 함정(reducer × reset 충돌)이 통째로 사라진다** — 서로 다른 typed 채널은 병렬 write 충돌이 없으므로 reducer/sentinel 자체가 불필요.

**어떻게**
- worker(`prediction_agent`/`evidence_agent`/`sql_agent`)는 `{"prediction_result": x}`처럼 **자기 typed key만** 반환.
- gate/final/ctx/memory의 `state.get("X_result") or artifacts.get("Y")` → `state.get("X_result")`로 단순화.
- `context_manager`의 `"artifacts": {}` 리셋 제거(typed 필드 None 리셋은 이미 있음).

**검증** — 멀티턴 T09/T13/T21(이전 턴 artifact 오염 없는지), T22(checkpoint resume).

---

### 2.3 [3순위] 1-A — hybrid LLM replanner  ✅ 완료(2026-06-22)
> 적용됨: `hybrid_replanner_decision`(deterministic default → 포기 시에만 `_llm_replanner_decision`, tier=classifier), 예산 이중 클램프, `PATCH_AND_RERUN` 시 `final_1` 강제 무효화. T20 무수정 통과.

**왜 중요한가**
- 현재 `deterministic_replanner_decision`은 규칙이 안 맞으면 즉시 `FINALIZE_WITH_WARNINGS`(포기) → 어려운 케이스(근거 못 찾음/SQL 실패)에서 답 품질이 떨어진다. 복구 한 번 더 시도하는 것이 사용자 체감 품질에 직결.

**어떻게** (`replanner` 셀 — `supervisor_replanner_node` 한 줄 교체)
```python
def hybrid_replanner_decision(state, plan, report):
    d = deterministic_replanner_decision(state, plan, report)
    if d.action != "FINALIZE_WITH_WARNINGS":   # deterministic이 처리함 → 그대로(T20 회귀 안전판)
        return d
    return _llm_replanner_decision(state, plan, report)  # 포기 신호일 때만 LLM
```
**이중 안전판 (무한루프·recursion_limit=50 초과 방지)**
1. node 진입 전 `rerun_count >= max_reruns` 차단 (이미 있음)
2. LLM decision 후 동일 클램프 (LLM이 예산 무시 방지)
3. `PATCH_AND_RERUN`이면 `invalidate_task_ids`에 `final_1` **강제 보강** (안 하면 stale final이 PASS로 남아 답변 미갱신) — `apply_replanner_decision`에 1줄 가드

**검증** — **T20은 deterministic 결과를 그대로 반환하므로 무수정 통과 = 회귀 안전판.** 추가로 evidence/SQL 실패 유도 케이스.

---

### 2.4 [4순위 · 리스트 밖이지만 강력 권장] 답변 품질 eval + 안전 적대 회귀
**왜 중요한가 — 진짜 사고는 배관이 아니라 여기서 난다**
- 현재 T01~T22는 **구조**(gate 상태·task 종류)만 검사한다. **"인용 [C3]이 실제로 그 주장을 뒷받침하나? 진단이 맞나?"는 아무도 검증하지 않는다.** 안전·진단 도메인에서 가장 위험한 공백.
- intake/output_safety가 LLM 기반이라, 한 번 뚫리면 치명적. 인젝션/위험명령 *변형* 케이스 압박이 병렬화보다 중요.

**어떻게**
- **품질 eval**: golden Q/A 세트 + LLM-as-judge — (a) citation이 답변 주장을 뒷받침하는지, (b) 진단/요약의 사실성, (c) 안전 자문이 "조치 대행"으로 넘어가지 않는지. 회귀로 고정.
- **안전 적대 회귀**: 인젝션·위험 실행 명령의 변형(우회 표현, 다국어, 코드펜스 안 숨김 등)을 늘려 intake/output_safety를 압박. 통과 기준: `_contains_unsafe_execution_instruction` + judge.
- 둘 다 비용/지연 무거우니 `tier="default"` judge로, CI가 아니라 수동/주기 실행으로.

---

### 2.5 [보류] 2-A reducer 부분 + 2-B Send fan-out
**왜 보류인가**
- 이득은 `combined_analysis`(prediction+sql+evidence 동시) 케이스의 **지연뿐**이고, worker당 LLM 1~2콜이라 절약폭이 제한적. 인터랙티브 단일 사용자 워크로드에선 8초→15초가 핵심 고통이 아니다.
- 비용은 2~3일/고위험 + 동시성 버그(RUNNING 갇힘, 다중 report 소비)라는 **가장 깨지기 쉬운 부분**을 건드림.
- **착수 전 반드시 측정**: T04/T13류의 실제 wall-clock을 재고, 체감 지연이 진짜 불만일 때만 진행.

**할 경우 설계 메모** (2-A green 이후에만)
- reducer는 **2곳만**: `gate_reports: Annotated[list, append_reset]`, `retry_counts: Annotated[dict, merge]`. (artifacts는 2.2에서 이미 제거됨)
- `append_reset` reset sentinel — 턴 경계 리셋은 **첫 노드 intake_gate**에서 1회(`[GATE_RESET, report]`), 나머지 gate는 `[report]`만 반환:
  ```python
  GATE_RESET = "__RESET_GATES__"
  def append_reset(left, right):
      if right == GATE_RESET: return []
      return (left or []) + (right if isinstance(right, list) else [right])
  ```
- `_wrap_retry`(build_graph 셀)는 자기 key delta만 반환하도록 수정(이중 누적 방지).
- fan-out 내부 순서: ① 다중 report 소비(`reduce(apply_gate_report, reports, plan)`)를 **직렬로 먼저 검증** → ② `reset_orphan_running` 완화("report 있는 task만 종결, 나머지 RUNNING 보존") → ③ `PlanOps.all_runnable()` + `route_after_orchestrator`가 `list[Send]` 반환으로 활성화.
- **replan은 직렬 유지**(fan-out 제외 — `consumed_replan_report_index` 단일 추적 보존). 단일 worker는 기존 경로 폴백.
- 검증: T04/T13(주 타깃, 시간 단축 확인), T20(replan 직렬), T22(checkpoint), T03~T11(단일 경로 폴백).

---

## 3. 공통 검증 게이트
모든 변경 후:
1. 결정적: 58셀 구문 컴파일 + T18/T20/PlanOps (LLM 0회, 즉시·무료)
2. 라이브 대표 4종: 인젝션 차단 / 안전자문+RAG / 구조화 예측 / SQL
3. 2-A·2-B는 추가로 전체 T01~T22 직렬 green

> 결정적+대표 4종은 비용이 작으니 매 단계 돌리고, 전체 22종은 Phase 2 같은 큰 변경에서만.

---

## 4. 절대 건드리지 말 것 (회귀 위험 핫스팟)
- **output_safety_gate를 mini로 내리지 말 것** — 안전 1차 방어선.
- **replan 예산(max_reruns) 우회 금지** — recursion_limit=50 초과/무한루프.
- **PATCH_AND_RERUN 시 final_1 무효화 누락 금지** — stale 답변.
- **gate_reports 턴 경계 리셋 위치는 intake_gate(첫 노드)** — context_manager에서 리셋하면 같은 턴 intake 리포트가 지워짐(이미 한 번 회귀났던 지점).
