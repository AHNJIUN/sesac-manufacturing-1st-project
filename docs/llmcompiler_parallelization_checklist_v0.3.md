# LLMCompiler 스타일 DAG 병렬화 적용 체크리스트 (v0.3)

> 대상: 현재 `manufacturing_agent/graph/` 구조 (Plan-and-Execute, 직렬 실행)
> 목표: planner가 만든 worker task 중 **의존성 없는 task를 동시에 실행**
> 핵심 도구: LangGraph **`Send` API** + **State reducer**
> 작성일: 2026-06-23
>
> **v0.3 변경 요약**
> - 시나리오 러너를 **v2(`run_manufacturing_scenarios_v2.py`)** 기준으로 전환. v2가 `DEFINITION_CELLS` 자동 보정을 수행하므로 v1 파일 수정 단계 제거.
> - 시나리오 ID를 v2 명명(`S4-3_diagnosis_history_evidence` 등)으로 정정.
> - 모든 `uv run` 명령에 `--env-file .env` 추가. 베이스라인 측정 명령을 단일 줄 페이스트 안전 형태로 통일.
> - 의존성 동기화(`uv sync --all-extras`) 절차를 Phase 0에 명시.
> - **§2.5 중복 매트릭스 삭제** → ADR-0004 본문 일원화.
> - **§3.2 state.py 어노테이션을 ADR-0004와 정합화**: `active_task_ids` / `consumed_replan_report_indices`의 잘못된 `add` reducer 제거, `agent_feedback`의 불필요 `dict_merge_last_wins` 제거.
> - `dict_merge_last_wins`에 "현재 미사용, 향후 대비" 주석 명시.
> - ADR-0004 코드 블록 닫힘 오류 수정.
> - v0.2의 invalid 베이스라인 측정 값(0.07s / 2.4s)은 폐기, **재측정 placeholder**로 대체.

---

## 0. 사전 이해

LLMCompiler(Kim et al., 2023)의 핵심 두 가지:

1. **Planner가 task DAG를 먼저 만든다** — 이 프로젝트는 이미 `TaskSpec.depends_on`으로 표현 중. 100% 보유.
2. **Executor가 의존성 만족된 task를 병렬로 dispatch** — 이 프로젝트는 dispatcher가 **단수**로 픽업 중. 이 부분만 바꾸면 된다.

즉 **planner 쪽은 거의 손대지 않고, dispatcher + state + LangGraph wiring만 수정**하는 작업이다.

---

## 1. Phase 0 — 사전 조사 & 기준점 확보

### 1.0 의존성 동기화 (선결 조건)

- [ ] **`.venv` 의존성 전체 설치** (langchain_openai 등이 빠지면 모든 측정이 실패)
  ```bash
  uv sync --all-extras
  ```

- [ ] **핵심 모듈 import 가능성 확인**
  ```bash
  uv run --env-file .env python -c "
  import langchain_openai, langgraph
  from langgraph.types import Send
  print('OK langgraph=', langgraph.__version__)
  "
  ```
  → 출력에 `Send`가 정상 import되고 langgraph 버전이 보이면 통과.

### 1.1 현재 동작 베이스라인 측정

**전제**: 시나리오 러너 v2(`scripts/run_manufacturing_scenarios_v2.py`)를 사용한다. v2는 v1을 라이브러리로 import하고 `_corrected_definition_cells()`로 노트북 셀을 자동 탐지하므로, **v1 파일은 수정하지 않는다**. v0.2의 `DEFINITION_CELLS` 수동 패치 단계는 제거됨.

- [ ] **3-worker 시나리오로 직렬 실행 wall-clock 5회 측정**
  ```bash
  mkdir -p docs/_scratch

  # 워밍업 (cold start 비용 제거)
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence > /dev/null 2>&1

  # 5회 측정
  for i in 1 2 3 4 5; do
    /usr/bin/time -p uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence 2>&1 | grep real
  done | tee docs/_scratch/baseline_serial.log

  awk '/real/{s+=$2; n++} END{print "avg:", s/n, "sec"}' docs/_scratch/baseline_serial.log
  ```

  **기대 avg**: 8~20초 (S4-3는 prediction + sql + evidence 3-worker 종합).
  **함정**: avg < 1초면 LLM 초기화 실패 또는 KeyError 종료. 1.0 단계로 돌아가 의존성/`.env`를 점검.

  ```text
  # 실행 결과 (재측정 후 채워 넣을 placeholder)
  real ?
  real ?
  real ?
  real ?
  real ?
  avg: ? sec
  ```

- [ ] **시나리오별 worker 조합 표 정리 (v2 시나리오 기준)**
  ```bash
  uv run python -c "
  import sys; sys.path.insert(0, 'scripts')
  from run_manufacturing_scenarios_v2 import scenarios
  for s in scenarios():
      print(f'{s.sid:40s} mode={s.mode:8s} tags={s.tags}')
      print(f'    └ {s.description}')
  " | tee docs/_scratch/scenario_workers_v2.txt
  ```

  → 병렬화 효과가 가장 큰 후보: `S4-3_diagnosis_history_evidence` (prediction+sql+rag), `S5-3_multiturn_history_evidence`. R 트랙은 회귀 검증용.

- [ ] **기존 시나리오 회귀가 모두 PASS인지 확인 (v2 전체)**
  ```bash
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 | tee docs/_scratch/baseline_regression.log
  grep -E "PASS|FAIL" docs/_scratch/baseline_regression.log | sort | uniq -c
  ```

- [ ] **그룹별 회귀 — 병렬화 영향 가능성이 높은 트랙 집중 확인**
  ```bash
  # B 트랙 (위험 진단 / 멀티턴)
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --group B 2>&1 | tee docs/_scratch/baseline_groupB.log
  # R 트랙 (구조·안전 회귀) — replan / output safety / checkpoint
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --group R 2>&1 | tee docs/_scratch/baseline_groupR.log
  ```

- [ ] **LangSmith trace 캡처 (직렬 구조 박제용)**
  ```bash
  LANGSMITH_TRACING=true LANGCHAIN_TRACING_V2=true \
    uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence > /dev/null 2>&1
  # LangSmith 웹 UI에서 trace 1건을 PNG로 저장 → docs/_scratch/serial_trace_baseline.png
  ```

  ![serial trace baseline](docs/_scratch/serial_trace_baseline.png)

### 1.2 LangGraph 버전 확인

- [ ] **langgraph 버전 확인**
  ```bash
  uv pip show langgraph | grep -E "Name|Version"
  uv pip show langgraph-checkpoint-sqlite | grep -E "Name|Version"
  ```

  → 현재 환경: `langgraph 1.2.5 / langgraph-checkpoint-sqlite 3.1.0` — Send API 안정 버전 한참 위.

- [x] **~~0.2.39 미만이면 사전 업그레이드 PR 분리~~ — 해당 없음 (langgraph 1.2.5)**

- [ ] **`Send`가 import 가능한지 즉시 확인**
  ```bash
  uv run python -c "from langgraph.types import Send; print('Send OK:', Send)"
  ```

### 1.3 코드 그래프 정독

- [ ] **현 dispatcher 단수 라우팅 위치 마킹 (`docs/_scratch/dispatcher_single_route_baseline.txt`)**
  ```bash
  grep -n "next_runnable\|RouteDecision\|next_node" manufacturing_agent/graph/dispatcher.py \
    | tee docs/_scratch/dispatcher_single_route_baseline.txt
  ```

  **해석**: 53번 줄의 `next_runnable` 호출과 78번 줄의 단수 `next_node` 반환 — 이 두 곳이 병렬화의 핵심 변경 대상.

- [ ] **state reducer 어노테이션 현황 출력**
  ```bash
  uv run python -c "
  from typing import get_type_hints
  from manufacturing_agent.contracts.state import ManufacturingState
  for name, t in get_type_hints(ManufacturingState, include_extras=True).items():
      print(f'{name:35s} {t}')
  " | tee docs/_scratch/state_reducer_annotations.log
  ```

  **해석**: 현재 `messages`만 `Annotated[..., add_messages]`. 그 외 필드는 LangGraph default(last-write-wins).
  병렬화 전에 reducer가 필수인 필드: `gate_reports`, `retry_counts`. (자세한 정책은 §2.5 ADR-0004 참조)

---

## 2. Phase 1 — 설계 결정 (코드 변경 전 확정)

각 결정은 `docs/adr/`에 ADR 형식으로 기록한 뒤 진행한다.

### 2.1 병렬 정책 결정 (ADR-0001)

- [ ] **ADR 작성**
  ```bash
  mkdir -p docs/adr
  cat > docs/adr/0001-parallel-policy.md <<'EOF'
  # ADR-0001 병렬 정책

  ## 결정
  prediction → (sql, evidence) 2단계.
  planner가 sql/evidence task의 depends_on=["prediction_1"]을 명시한다.
  prediction이 없으면 sql/evidence가 즉시 병렬.

  ## 이유
  prediction.failure_type을 sql/evidence가 활용하는 케이스(S4-3, S5-3 등)가 존재.
  병렬화 이득보다 cross-worker 정보 사용이 정확도에 더 중요.

  ## 영향
  planner.py의 _sql_task / _evidence_task 빌더 수정.
  EOF
  ```

- [ ] **planner.py에 depends_on 명시 (Phase 4에서 실제 적용)**
  ```python
  # manufacturing_agent/graph/planner.py:_sql_task / _evidence_task 신규 옵션
  def _sql_task(decision, has_prediction: bool) -> TaskSpec:
      return TaskSpec(
          task_id="sql_1", task_type="sql",
          depends_on=["prediction_1"] if has_prediction else [],
          ...
      )
  ```

### 2.2 동시 실행 상한

- [ ] **config.py에 상수 추가**
  ```python
  # manufacturing_agent/config.py 끝부분
  MAX_PARALLEL_WORKERS = int(os.environ.get("MAX_PARALLEL_WORKERS", "3"))
  ```

- [ ] **OpenAI tier 한도 확인 (RPM/TPM)**
  ```bash
  uv run --env-file .env python << 'EOF' | tee docs/_scratch/openai_rate_limits.log
  import os, urllib.request, json
  KEY = os.environ['OPENAI_API_KEY']

  def probe(model):
      body = json.dumps({
          'model': model,
          'messages': [{'role': 'user', 'content': 'ping'}],
          'max_tokens': 1,
      }).encode()
      req = urllib.request.Request(
          'https://api.openai.com/v1/chat/completions',
          data=body, method='POST',
          headers={'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'},
      )
      try:
          resp = urllib.request.urlopen(req)
          h = resp.headers
          rpm = h.get('x-ratelimit-limit-requests', '?')
          tpm = h.get('x-ratelimit-limit-tokens', '?')
          print(f'{model:18s} RPM={rpm:>8s}  TPM={tpm:>12s}')
      except Exception as e:
          print(f'{model:18s} ERROR: {e}')

  for m in ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini']:
      probe(m)
  EOF
  ```

  **현재 측정 결과** (2026-06-23):
  | 모델 | 측정 RPM | 측정 TPM | 비고 |
  |---|---|---|---|
  | gpt-4o | 10,000 | 2,000,000 | Tier 4 추정 |
  | gpt-4o-mini | 10,000 | 10,000,000 | Tier 4~5 |
  | gpt-4.1-mini | 10,000 | 10,000,000 | Tier 4~5 |

  **결론**: 3-worker 동시 호출의 peak TPM 사용률 약 72%, RPM 사용률 < 4%. **rate limit는 병렬화의 제약이 아님.**

### 2.3 부분 실패 정책 (ADR-0002)

- [ ] **ADR 작성**
  ```bash
  cat > docs/adr/0002-partial-failure.md <<'EOF'
  # ADR-0002 병렬 부분 실패

  ## 결정
  병렬 worker 중 일부가 PLAN_REPAIR_REQUIRED여도 나머지는 모두 종결까지 실행.
  dispatcher가 fan-in 시점에 모든 gate report를 종합해 replanner 호출.

  ## 이유
  - 이미 시작된 worker 결과 폐기 비용 > replan 대기 비용
  - LangGraph 0.2.x cancellation API가 안정 미보장
  - checkpoint resume 복잡도 최소화

  ## Rate limit 영향
  2026-06-23 측정 (docs/_scratch/openai_rate_limits.log 참조):
  - gpt-4o:       RPM 10000 / TPM 2M
  - gpt-4o-mini:  RPM 10000 / TPM 10M
  - gpt-4.1-mini: RPM 10000 / TPM 10M

  3 worker 동시 호출 시 peak TPM 사용률 약 72% (gpt-4o 기준 1초 윈도우),
  RPM 사용률 4% 미만. 병렬화 도입에 따른 429 위험은 무시 가능.
  cancellation 정책에서 "끝까지 실행" 결정에 영향 없음.
  EOF
  ```

### 2.4 Replanner 트리거 시점 (ADR-0003)

- [ ] **ADR 작성**
  ```bash
  cat > docs/adr/0003-replanner-trigger.md <<'EOF'
  # ADR-0003 병렬 환경 replanner 호출

  ## 결정
  fan-in 시점에 unprocessed_reports를 plan에 모두 반영한 뒤,
  PLAN_REPAIR_REQUIRED가 하나라도 있으면 replanner를 호출한다.
  replanner는 SupervisorReplannerDecision.task_patches에
  여러 TaskSpec patch를 한 번에 담아 반환한다.

  ## 이유
  - 직렬 시절 정책("gate 1개당 dispatcher 1회 호출 → replanner")이 병렬에서는
    성립하지 않음. fan-in 한 번에 보고서가 N개 누적되기 때문.
  - 보고서를 한 건씩 처리하면 같은 turn에 replanner가 N번 호출되어
    plan_revision이 의미 없이 증가하고 LLM fallback 비용도 N배.
  - patch를 한 번에 묶으면 replanner 호출 1회로 다중 task 보정이 끝나고,
    final_1 invalidate도 1회로 충분.

  ## 적용 단계 (rollout)
  - **PR 5 (feat/dispatcher-send-fanout)** — 보수적 시작.
    dispatcher가 첫 번째 PLAN_REPAIR_REQUIRED 1건만 replanner로 넘김.
    나머지 unprocessed report는 다음 dispatcher 사이클에서 처리.
  - **PR 6 (feat/replanner-batch)** — 본 ADR의 종착 상태.
    hybrid_replanner_decision_batch를 통해 다중 patch를 한 결정으로 통합.
    dispatcher는 PLAN_REPAIR_REQUIRED 보고서 전체를 모아 한 번에 replanner 호출.

  ## 영향
  - graph/dispatcher.py: unprocessed_reports 일괄 처리 루프 도입.
  - graph/replanner.py: hybrid_replanner_decision_batch 헬퍼 추가
    (deterministic_replanner_decision은 task-by-task 호출 후 patch 누적).
  - contracts/state.py: consumed_replan_report_indices(list) 신규 필드.
    단수 consumed_replan_report_index는 호환 유지(deprecated).
  - ADR-0002와 관계: in-flight worker를 끝까지 실행(0002)하므로
    fan-in 시점에 보고서 N개가 모이는 것은 의도된 결과.
  EOF
  ```

### 2.5 State 일관성 정책 (ADR-0004)

> v0.2에 있던 중복 매트릭스(6행)는 삭제. **ADR-0004 본문(14행)이 유일한 진실 출처**.

- [ ] **ADR-0004 작성**
  ```bash
  cat > docs/adr/0004-state-write-matrix.md <<'EOF'
  # ADR-0004 State 필드별 쓰기 권한 매트릭스

  ## 결정
  병렬 worker 도입 후 ManufacturingState의 각 필드는
  다음 매트릭스에 따라 reducer를 지정한다.

  | 필드 | 쓰기 주체 | 충돌 가능성 | reducer |
  |---|---|---|---|
  | `execution_plan` | dispatcher / replanner | 단일 (둘 다 직렬 진입점) | 기본 (last-write) |
  | `gate_reports` | 각 worker gate | **다중 동시 append** | `add` |
  | `retry_counts` | 각 worker(`_wrap_retry`) | **다중 동시 +1** | `dict_merge_max` |
  | `agent_feedback` | dispatcher | 단일 (dispatcher 1점) | 기본 |
  | `prediction_result` | prediction_agent | 워커별 1점 | 기본 |
  | `evidence_bundle` | evidence_agent | 워커별 1점 | 기본 |
  | `sql_result` | sql_agent | 워커별 1점 | 기본 |
  | `active_task_id` (deprecated) | dispatcher | 단일 | 기본 |
  | `active_task_ids` (신규) | dispatcher | 단일 (전체 리스트 반환) | 기본 |
  | `consumed_replan_report_index` (deprecated) | replanner | 단일 | 기본 |
  | `consumed_replan_report_indices` (신규) | dispatcher / replanner | 단일 (전체 리스트 반환) | 기본 |
  | `final_answer` | final_answer_node | 단일 (마지막 노드) | 기본 |
  | `input_decision` / `intake_decision` | intake_gate | 단일 | 기본 |
  | `context_packet` | context_manager | 단일 | 기본 |
  | `messages` | 상속(MessagesState) | 다중 가능 | `add_messages` (LangChain 자동) |

  ## 이유
  - LangGraph는 어노테이션이 없으면 last-write-wins로 덮어쓰기.
    병렬 fan-in 시점에 동시 쓰기가 발생하는 필드만 reducer가 필요.
  - `gate_reports`는 각 gate가 자기 1건만 반환 → `add`로 누적해야 손실 방지.
  - `retry_counts`는 각 worker가 자기 카운터만 키 1개씩 갱신 → `dict_merge_max`로
    병렬 갱신을 모두 보존(같은 키 충돌은 더 큰 값 채택).
  - "전체 리스트를 반환하는 단일 writer 필드"에는 절대 `add`를 쓰지 않는다.
    `[a,b]` + `[a,b,c]` 가 `[a,b,a,b,c]` 가 되어 중복 누적되기 때문.

  ## 영향
  - contracts/state.py: 위 표대로 **2개 필드만** reducer 어노테이션.
    ```python
    gate_reports: Annotated[list, add]
    retry_counts: Annotated[dict, dict_merge_max]
    # 그 외는 어노테이션 추가하지 않는다.
    ```
  - contracts/reducers.py: `dict_merge_max`만 필수. `dict_merge_last_wins`는
    현재 매트릭스 기준 사용처가 없으나 향후 다중 writer 필드 등장 대비로
    같이 추가해두는 것은 무방(주석으로 미사용 명시).
  - ADR-0001/0002/0003과의 정합성: 본 매트릭스는 그들 결정의 기술적 전제 조건.
  EOF
  ```

---

## 3. Phase 2 — State 스키마 변경

### 3.1 reducer 모듈 신규 생성

- [ ] **`contracts/reducers.py` 신규 파일**
  ```python
  # manufacturing_agent/contracts/reducers.py
  """병렬 worker 동시 쓰기를 안전하게 합치는 reducer 모음."""
  from __future__ import annotations
  from typing import Any


  def dict_merge_max(left: dict[str, int] | None, right: dict[str, int] | None) -> dict[str, int]:
      """retry_counts용. 같은 키는 max로. 병렬 worker가 각자 +1 한 결과를 손실 없이 합친다."""
      if not left:
          return dict(right or {})
      if not right:
          return dict(left)
      out = dict(left)
      for k, v in right.items():
          out[k] = max(out.get(k, 0), v)
      return out


  def dict_merge_last_wins(left: dict | None, right: dict | None) -> dict:
      """[현재 미사용 — ADR-0004 기준] 향후 다중 writer dict 필드 등장 시 사용 예약.
      agent_feedback은 dispatcher 단일 writer라 본 reducer 불필요."""
      out = dict(left or {})
      out.update(right or {})
      return out


  def replace_if_present(left: Any, right: Any) -> Any:
      """단일 객체용 명시 reducer. right가 None이면 left를 유지(부분 업데이트)."""
      return right if right is not None else left
  ```

- [ ] **단위 테스트 신규 추가**
  ```python
  # tests/test_reducers.py
  from manufacturing_agent.contracts.reducers import (
      dict_merge_max, dict_merge_last_wins, replace_if_present,
  )

  def test_dict_merge_max_appends_disjoint_keys():
      assert dict_merge_max({"prediction": 1}, {"sql": 1}) == {"prediction": 1, "sql": 1}

  def test_dict_merge_max_takes_higher():
      assert dict_merge_max({"prediction": 2}, {"prediction": 1}) == {"prediction": 2}
      assert dict_merge_max({"prediction": 1}, {"prediction": 2}) == {"prediction": 2}

  def test_dict_merge_max_handles_none():
      assert dict_merge_max(None, {"a": 1}) == {"a": 1}
      assert dict_merge_max({"a": 1}, None) == {"a": 1}

  def test_dict_merge_last_wins():
      assert dict_merge_last_wins({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}

  def test_replace_if_present_skips_none():
      assert replace_if_present("old", None) == "old"
      assert replace_if_present("old", "new") == "new"
  ```

- [ ] **테스트 실행**
  ```bash
  uv run pytest tests/test_reducers.py -v
  ```

### 3.2 state.py 어노테이션 추가 (ADR-0004 정합)

> v0.2의 잘못된 코드(`active_task_ids: Annotated[list[str], add]` 등)는 폐기. ADR-0004에 맞춰 **2개 필드만 어노테이션**한다.

- [ ] **`contracts/state.py` 수정**
  ```python
  # manufacturing_agent/contracts/state.py — ADR-0004 기준
  from operator import add
  from typing import Annotated, Optional
  from manufacturing_agent.contracts.reducers import dict_merge_max

  class ManufacturingState(MessagesState, total=False):
      # ...기존 필드 유지...

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
  ```

- [ ] **단위 테스트 (state 합치기)**
  ```python
  # tests/test_state_reducers.py
  from langgraph.graph import StateGraph, START, END
  from langgraph.types import Send
  from manufacturing_agent.contracts.state import ManufacturingState

  def _node_a(_): return {"gate_reports": [{"gate_name": "a", "status": "PASS"}],
                          "retry_counts": {"prediction": 1}}
  def _node_b(_): return {"gate_reports": [{"gate_name": "b", "status": "PASS"}],
                          "retry_counts": {"sql": 1}}

  def test_parallel_state_merge():
      g = StateGraph(ManufacturingState)
      g.add_node("a", _node_a)
      g.add_node("b", _node_b)
      g.add_node("end", lambda s: {})
      g.add_conditional_edges(START, lambda _: [Send("a", {}), Send("b", {})], ["a", "b"])
      g.add_edge("a", "end"); g.add_edge("b", "end"); g.add_edge("end", END)
      app = g.compile()
      out = app.invoke({})
      assert len(out["gate_reports"]) == 2
      assert out["retry_counts"] == {"prediction": 1, "sql": 1}
  ```

### 3.3 `active_task_id` → `active_task_ids` 마이그레이션

- [ ] **state.py에 신규 필드 추가 (§3.2에 포함)**

- [ ] **`runtime.py:make_initial_state` 수정**
  ```python
  return {
      ...,
      "active_task_id": None,
      "active_task_ids": [],
      "consumed_replan_report_indices": [],
      "consumed_replan_report_index": None,
      ...
  }
  ```

- [ ] **`runtime.py:checkpoint_status` 반환에 신규 키 추가**
  ```python
  return {
      "next": tuple(snapshot.next or ()),
      "request_id": values.get("request_id"),
      "user_message": values.get("user_message"),
      "active_task_ids": values.get("active_task_ids") or [],
      "has_final_answer": bool(values.get("final_answer")),
      "gate_count": len(values.get("gate_reports") or []),
  }
  ```

- [ ] **`_print_turn_result` 디버그 출력 수정**
  ```python
  if debug:
      print("🧭 ACTIVE TASKS:", result.get("active_task_ids"))
  ```

- [ ] **`api/routers/chat.py:_step_detail` 영향 점검**
  ```bash
  grep -n "active_task" api/routers/chat.py
  ```

### 3.4 Checkpoint 호환성

- [ ] **`build.py:CHECKPOINT_SAFE_TYPES`는 Pydantic 모델만 등록되어 있어 추가 불필요**
  ```bash
  grep -n "CHECKPOINT_SAFE_TYPES" manufacturing_agent/graph/build.py
  ```

- [ ] **직렬 버전 checkpoint를 병렬 버전에서 resume 수동 테스트**
  ```bash
  uv run --env-file .env python -c "
  from manufacturing_agent.runtime import run_turn
  run_turn('토크 60만 있는데 위험 진단해줘', 'demo', 'tid-bridge', 'r1')
  "
  uv run --env-file .env python -c "
  from manufacturing_agent.runtime import checkpoint_status
  print(checkpoint_status('tid-bridge', 'demo'))
  "
  ```

---

## 4. Phase 3 — `PlanOps` 배치 연산 추가

### 4.1 `next_runnable_batch`

- [ ] **`graph/plan_ops.py`에 메서드 추가**
  ```python
  @staticmethod
  def next_runnable_batch(plan: ExecutionPlan, limit: Optional[int] = None) -> list[TaskSpec]:
      """deps 종결된 PENDING worker task들. final_answer는 절대 batch에 포함하지 않는다."""
      batch: list[TaskSpec] = []
      seen_types: set[str] = set()
      for task in plan.tasks:
          if task.task_type == "final_answer":
              continue
          if task.status != "PENDING":
              continue
          if not PlanOps.deps_terminal(plan, task):
              continue
          if task.task_type in seen_types:
              continue
          batch.append(task)
          seen_types.add(task.task_type)
          if limit is not None and len(batch) >= limit:
              break
      if batch:
          return batch
      final = next((t for t in plan.tasks if t.task_type == "final_answer"), None)
      if (final and final.status not in TERMINAL_TASK_STATUSES
              and PlanOps.deps_terminal(plan, final)):
          return [final]
      return []
  ```

### 4.2 `mark_running_batch`

- [ ] **PlanOps에 추가**
  ```python
  @staticmethod
  def mark_running_batch(plan: ExecutionPlan, task_ids: list[str]) -> ExecutionPlan:
      ids = set(task_ids)
      tasks = [t.model_copy(update={"status": "RUNNING"}) if t.task_id in ids else t
               for t in plan.tasks]
      return plan.model_copy(update={"tasks": tasks})
  ```

### 4.3 `unprocessed_reports`

- [ ] **plan_ops.py 헬퍼 추가**
  ```python
  def unprocessed_reports(state, plan: ExecutionPlan) -> list[tuple[int, dict]]:
      """consumed_replan_report_indices에 없는 worker-gate report 전체."""
      reports = state.get("gate_reports") or []
      consumed = set(state.get("consumed_replan_report_indices") or [])
      out: list[tuple[int, dict]] = []
      for idx, r in enumerate(reports):
          if idx in consumed:
              continue
          if r.get("gate_name") not in WORKER_GATE_TO_TASK:
              continue
          out.append((idx, r))
      return out
  ```

### 4.4 단위 테스트

- [ ] **`tests/test_plan_ops_batch.py` 신규**
  ```python
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
  ```

- [ ] **테스트 실행**
  ```bash
  uv run pytest tests/test_plan_ops_batch.py -v
  ```

---

## 5. Phase 4 — Dispatcher fan-out (`Send` API 도입)

### 5.1 `OrchestratorDecision` 확장

- [ ] **`contracts/context.py` 수정**
  ```python
  class OrchestratorDecision(BaseModel):
      action: Literal[
          "DISPATCH_TASK", "DISPATCH_BATCH", "RETRY_TASK",
          "REPLAN", "FINALIZE", "WAIT_USER_INPUT", "BLOCKED"
      ] = "DISPATCH_TASK"
      next_node: Literal[
          "prediction_agent", "evidence_agent", "sql_agent",
          "final_answer", "supervisor_replanner"
      ]
      dispatched_task_ids: list[str] = Field(default_factory=list)
      active_task_id: Optional[str] = None
      reason_summary: str = ""
  ```

### 5.2 dispatcher 본문 교체

- [ ] **`graph/dispatcher.py` 핵심 변경**
  ```python
  from manufacturing_agent.config import MAX_PARALLEL_WORKERS, PARALLEL_DISPATCH_ENABLED
  from manufacturing_agent.graph.plan_ops import PlanOps, TASK_TO_NODE, unprocessed_reports

  def orchestrator_dispatcher(state: ManufacturingState, config: RunnableConfig = None) -> dict:
      plan = state.get("execution_plan")
      if plan is None:
          raise ValueError("orchestrator_dispatcher requires execution_plan.")

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


  def _reset_orphan_running_batch(plan, active_ids: list[str], pending_reports):
      reported_task_ids = {rep.get("task_id") for _, rep in pending_reports}
      keep = set(active_ids) | reported_task_ids
      tasks = [t.model_copy(update={"status": "PENDING"})
               if (t.status == "RUNNING" and t.task_id not in keep) else t
               for t in plan.tasks]
      return plan.model_copy(update={"tasks": tasks})
  ```

### 5.3 `route_after_orchestrator`가 `list[Send]` 반환

- [ ] **`graph/dispatcher.py` 라우터 교체**
  ```python
  from langgraph.types import Send
  from manufacturing_agent.graph.plan_ops import PlanOps, TASK_TO_NODE

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
  ```

### 5.4 `_wrap_retry`가 병렬에서도 안전한지 확인

- [ ] **검증 단위 테스트**
  ```python
  # tests/test_wrap_retry_parallel.py
  from manufacturing_agent.graph.build import _wrap_retry
  from manufacturing_agent.contracts.reducers import dict_merge_max

  def test_wrap_retry_two_calls_merge_max():
      def fake_agent(_state): return {}
      f = _wrap_retry(fake_agent, "prediction")
      r1 = f({"retry_counts": {}})
      r2 = f({"retry_counts": {}})
      merged = dict_merge_max(r1["retry_counts"], r2["retry_counts"])
      assert merged == {"prediction": 1}
  ```

---

## 6. Phase 5 — Gate fan-in (barrier 보장)

### 6.1 LangGraph barrier 동작 검증

- [ ] **토이 그래프로 사전 확인**
  ```python
  # docs/_scratch/verify_fanin.py (commit 안 함)
  from langgraph.graph import StateGraph, START, END
  from langgraph.types import Send
  from typing import TypedDict, Annotated
  from operator import add

  class S(TypedDict, total=False):
      reports: Annotated[list, add]

  def a(_): return {"reports": ["a"]}
  def b(_): return {"reports": ["b"]}
  def c(_): return {"reports": ["c"]}
  def sink(s):
      print("sink got reports:", s.get("reports"))
      return {}

  g = StateGraph(S)
  for n, fn in [("a", a), ("b", b), ("c", c), ("sink", sink)]:
      g.add_node(n, fn)
  g.add_conditional_edges(START, lambda _: [Send("a", {}), Send("b", {}), Send("c", {})],
                          ["a", "b", "c"])
  g.add_edge("a", "sink"); g.add_edge("b", "sink"); g.add_edge("c", "sink")
  g.add_edge("sink", END)
  app = g.compile()
  out = app.invoke({})
  assert sorted(out["reports"]) == ["a", "b", "c"]
  print("OK barrier holds.")
  ```
  ```bash
  mkdir -p docs/_scratch
  uv run python docs/_scratch/verify_fanin.py
  ```

### 6.2 dispatcher가 모든 미처리 보고서를 한 루프에 적용

- [ ] **`tests/test_dispatcher_fanin.py`**
  ```python
  from manufacturing_agent.graph.dispatcher import orchestrator_dispatcher
  from manufacturing_agent.contracts.context import ExecutionPlan, TaskSpec

  def test_dispatcher_applies_all_pending_reports():
      plan = ExecutionPlan(intent="combined_analysis", tasks=[
          TaskSpec(task_id="prediction_1", task_type="prediction", status="RUNNING"),
          TaskSpec(task_id="sql_1", task_type="sql", status="RUNNING"),
          TaskSpec(task_id="evidence_1", task_type="evidence", status="RUNNING"),
          TaskSpec(task_id="final_1", task_type="final_answer",
                   depends_on=["prediction_1", "sql_1", "evidence_1"], status="PENDING"),
      ])
      state = {
          "execution_plan": plan,
          "active_task_ids": ["prediction_1", "sql_1", "evidence_1"],
          "consumed_replan_report_indices": [],
          "gate_reports": [
              {"gate_name": "prediction_gate", "status": "PASS", "task_id": "prediction_1"},
              {"gate_name": "sql_gate", "status": "PASS", "task_id": "sql_1"},
              {"gate_name": "evidence_gate", "status": "PASS", "task_id": "evidence_1"},
          ],
      }
      out = orchestrator_dispatcher(state)
      new_plan = out["execution_plan"]
      worker_statuses = [t.status for t in new_plan.tasks if t.task_type != "final_answer"]
      assert all(s == "PASS" for s in worker_statuses)
      assert out["orchestrator_decision"].action == "FINALIZE"
  ```

### 6.3 replanner도 `consumed_replan_report_indices` 사용

- [ ] **replanner.py 신규 필드 사용**
  ```python
  def supervisor_replanner_node(state, config=None) -> dict:
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
  ```

---

## 7. Phase 6 — Replanner를 병렬 환경에 맞춤

### 7.1 다중 task patch 헬퍼

- [ ] **`graph/replanner.py`에 batch decision 추가**
  ```python
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
  ```

### 7.2 다중 patch 회귀 테스트

- [ ] **`tests/test_replanner_batch.py`**
  ```python
  from manufacturing_agent.contracts.context import (
      ExecutionPlan, TaskSpec, SupervisorReplannerDecision, TaskPatch,
  )
  from manufacturing_agent.graph.replanner import apply_replanner_decision

  def test_apply_replanner_with_multiple_patches():
      plan = ExecutionPlan(intent="combined_analysis", tasks=[
          TaskSpec(task_id="sql_1", task_type="sql", status="FAIL"),
          TaskSpec(task_id="evidence_1", task_type="evidence", status="FAIL"),
          TaskSpec(task_id="final_1", task_type="final_answer",
                   depends_on=["sql_1", "evidence_1"], status="PENDING"),
      ])
      decision = SupervisorReplannerDecision(
          action="PATCH_AND_RERUN",
          target_task_ids=["sql_1", "evidence_1"],
          task_patches=[
              TaskPatch(task_id="sql_1", params_update={"strict_schema_check": True}),
              TaskPatch(task_id="evidence_1", params_update={"retrieval_profile": "fallback_broad"}),
          ],
          invalidate_task_ids=["final_1"],
      )
      new_plan = apply_replanner_decision(plan, decision, report=None)
      patched = {t.task_id: t for t in new_plan.tasks}
      assert patched["sql_1"].status == "PENDING" and patched["sql_1"].rerun_count == 1
      assert patched["evidence_1"].status == "PENDING" and patched["evidence_1"].rerun_count == 1
      assert patched["final_1"].status == "PENDING"
      assert new_plan.plan_revision == 1
  ```

---

## 8. Phase 7 — chat / runtime / observability 동기화

### 8.1 SSE 라벨 보강

- [ ] **`api/routers/chat.py:_step_detail`**
  ```python
  def _step_detail(node: str, delta: dict) -> str:
      try:
          if node == "supervisor_planner":
              plan = delta.get("execution_plan")
              tasks = [t.task_type for t in getattr(plan, "tasks", []) if t.task_type != "final_answer"]
              ko = ", ".join(_TASK_KO.get(t, t) for t in tasks)
              return f"필요 작업: {ko}" if ko else ""
          if node == "orchestrator_dispatcher":
              decision = delta.get("orchestrator_decision")
              ids = getattr(decision, "dispatched_task_ids", []) if decision else []
              if len(ids) > 1:
                  return f"병렬 실행: {', '.join(ids)}"
              return ", ".join(ids)
          if node in {"prediction_gate", "evidence_gate", "sql_gate", "intake_gate", "output_safety_gate"}:
              reports = delta.get("gate_reports") or []
              if reports:
                  return str(reports[-1].get("status", ""))
      except Exception:
          pass
      return ""
  ```

### 8.2 OpenTelemetry thread-safety 검증

- [ ] **`tests/test_observability_threadsafe.py`**
  ```python
  import threading
  from manufacturing_agent.observability import record_llm_usage, usage_snapshot

  def test_concurrent_record_does_not_lose_count():
      def hit():
          for _ in range(100):
              record_llm_usage("gpt-4o", "default", 10, 5)
      ts = [threading.Thread(target=hit) for _ in range(8)]
      [t.start() for t in ts]; [t.join() for t in ts]
      snap = usage_snapshot()
      total_calls = sum(v["calls"] for v in snap.get("by_model", {}).values())
      assert total_calls >= 800
  ```

### 8.3 LangSmith trace 시각화

- [ ] **병렬 1턴 trace 캡처**
  ```bash
  LANGSMITH_TRACING=true LANGCHAIN_TRACING_V2=true PARALLEL_DISPATCH=1 \
    uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence
  # → docs/_scratch/parallel_trace_after.png
  ```

---

## 9. Phase 8 — 테스트 전략

### 9.1 단위 테스트 일괄 실행

- [ ] **신규 테스트 모두 통과**
  ```bash
  uv run pytest tests/ -v --tb=short
  ```

### 9.2 통합 회귀 (v2 기준)

- [ ] **flag OFF (직렬) 회귀**
  ```bash
  unset PARALLEL_DISPATCH
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 | tee docs/_scratch/after_serial.log
  diff <(grep -E "PASS|FAIL" docs/_scratch/baseline_regression.log) \
       <(grep -E "PASS|FAIL" docs/_scratch/after_serial.log)
  ```

- [ ] **flag ON (병렬) 회귀**
  ```bash
  PARALLEL_DISPATCH=1 MAX_PARALLEL_WORKERS=3 \
    uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 | tee docs/_scratch/after_parallel.log
  grep -E "PASS|FAIL" docs/_scratch/after_parallel.log | sort | uniq -c
  ```

### 9.3 신규 시나리오 추가 (v2 regression 트랙에 합류)

- [ ] **`scripts/run_manufacturing_scenarios_v2.py:scenarios()`의 regression 리스트에 추가**
  ```python
  Scenario(
      "R11_parallel_workers",
      "병렬: prediction+sql+evidence가 단일 dispatch 사이클에서 fan-out",
      [Turn("입력한 데이터로 위험 진단, 비슷한 과거 이력, 점검 문서 근거까지 정리해줘.",
            FEATURES_HIGH_RISK)],
      _check_parallel_dispatch,
      tags=["R", "parallel", "combined"],
  ),
  Scenario(
      "R12_parallel_partial_failure",
      "병렬: sql만 PLAN_REPAIR, 나머지는 PASS → replanner가 sql만 patch",
      [Turn("입력한 데이터로 위험 진단, 비슷한 과거 이력, 점검 문서 근거 정리.", FEATURES_HIGH_RISK)],
      _check_parallel_partial_replan,
      tags=["R", "parallel", "replan"],
  ),
  Scenario(
      "R13_parallel_replan_then_pass",
      "병렬 → 1개 replan → 재실행 → 최종 통과",
      [Turn("입력한 데이터로 위험 진단과 비슷한 과거 이력 정리.", FEATURES_HIGH_RISK)],
      _check_parallel_replan_recovery,
      tags=["R", "parallel", "replan", "recovery"],
  ),
  ```

- [ ] **체커 함수 예시 (v2 헬퍼 재사용)**
  ```python
  def _check_parallel_dispatch(results, g):
      r = results[-1]
      decisions = [d for d in r.get("orchestrator_decisions", [])
                   if getattr(d, "action", "") == "DISPATCH_BATCH"]
      failures = []
      if not decisions:
          failures.append("병렬 dispatch가 발생하지 않음")
          return failures
      ids = decisions[0].dispatched_task_ids
      if len(ids) < 2:
          failures.append(f"단일 task만 dispatch됨: {ids}")
      return failures
  ```

### 9.4 성능 측정 비교

- [ ] **wall-clock 5회 평균 비교**
  ```bash
  # 워밍업
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence > /dev/null 2>&1

  echo "=== Serial ===" > /tmp/perf.txt
  unset PARALLEL_DISPATCH
  for i in 1 2 3 4 5; do
    /usr/bin/time -p uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence 2>&1 | grep real
  done >> /tmp/perf.txt

  echo "=== Parallel ===" >> /tmp/perf.txt
  for i in 1 2 3 4 5; do
    PARALLEL_DISPATCH=1 MAX_PARALLEL_WORKERS=3 \
      /usr/bin/time -p uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence 2>&1 | grep real
  done >> /tmp/perf.txt

  python -c "
  import re; t=open('/tmp/perf.txt').read()
  for sec, label in [('Serial', 'serial'), ('Parallel', 'parallel')]:
      seg = t.split(sec)[1].split('===')[0]
      vals = [float(m) for m in re.findall(r'real\s+([\d.]+)', seg)]
      print(label, 'avg:', sum(vals)/len(vals), 'min:', min(vals), 'max:', max(vals))
  "
  ```

- [ ] **LLM 호출 횟수 동일성 확인**
  ```bash
  # 서버 가동 후
  curl -s http://localhost:8000/usage | jq '.by_model'
  ```

---

## 10. Phase 9 — 안전한 롤아웃

### 10.1 Feature flag 도입

- [ ] **`config.py`에 flag 정의**
  ```python
  PARALLEL_DISPATCH_ENABLED = os.environ.get("PARALLEL_DISPATCH", "0") == "1"
  MAX_PARALLEL_WORKERS = int(os.environ.get("MAX_PARALLEL_WORKERS", "3"))
  ```

### 10.2 PR 단위 점진 적용

- [ ] **PR 1 — baseline 측정 + ADR**
  ```bash
  git checkout -b chore/parallel-baseline
  git add docs/
  git commit -m "chore: parallelization baseline metrics + ADRs (v2 runner)"
  ```

- [ ] **PR 2 — reducers + state annotation (ADR-0004 정합)**
  ```bash
  git checkout -b feat/state-reducers
  uv run pytest tests/test_reducers.py tests/test_state_reducers.py -v
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py
  git commit -m "feat(state): add merge reducers for parallel-safe state (ADR-0004)"
  ```

- [ ] **PR 3 — PlanOps batch methods (호출 미연결)**
  ```bash
  git checkout -b feat/planops-batch
  uv run pytest tests/test_plan_ops_batch.py -v
  git commit -m "feat(plan_ops): add next_runnable_batch / mark_running_batch"
  ```

- [ ] **PR 4 — OrchestratorDecision 확장 + dispatcher 직렬 호환**
  ```bash
  git checkout -b feat/dispatcher-batch-decision
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py
  git commit -m "feat(dispatcher): batch-aware decision, serial behavior preserved"
  ```

- [ ] **PR 5 — Send fan-out (flag OFF 머지)**
  ```bash
  git checkout -b feat/dispatcher-send-fanout
  git commit -m "feat(dispatcher): introduce Send fan-out behind flag"
  ```

- [ ] **PR 6 — replanner batch decision**
  ```bash
  git checkout -b feat/replanner-batch
  git commit -m "feat(replanner): support multi-task patch decisions"
  ```

- [ ] **PR 7 — chat SSE / runtime sync**
  ```bash
  git checkout -b feat/api-parallel-sync
  git commit -m "feat(api): surface parallel task ids in SSE detail"
  ```

- [ ] **PR 8 — 신규 시나리오 + 성능 측정**
  ```bash
  git checkout -b test/parallel-scenarios
  git commit -m "test: add R11~R13 parallel scenarios + perf report"
  ```

- [ ] **PR 9 — flag ON**
  ```bash
  git checkout -b chore/enable-parallel-dispatch
  git commit -m "chore: enable PARALLEL_DISPATCH=1 in staging"
  ```

### 10.3 롤백 절차

- [ ] **즉시 롤백 — flag만 끄기**
  ```bash
  export PARALLEL_DISPATCH=0
  ```

- [ ] **롤백 후 직렬 회귀 1회**
  ```bash
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 | grep -E "PASS|FAIL" | sort | uniq -c
  ```

- [ ] **checkpoint DB 영향 확인**
  ```bash
  uv run --env-file .env python -c "
  from manufacturing_agent.runtime import checkpoint_status
  print(checkpoint_status('demo-user-001', 'tid-bridge'))
  "
  ```

---

## 11. 변경 영향 매트릭스

| 파일 | 변경 종류 | 위험도 | 코드 위치 |
|---|---|---|---|
| `contracts/reducers.py` (신규) | `dict_merge_max` 필수 / `dict_merge_last_wins` 미사용 마킹 | 🟨 낮 | §3.1 |
| `contracts/state.py` | reducer 어노테이션 2개 + 신규 필드 | 🟧 중 | §3.2 |
| `contracts/context.py` | `OrchestratorDecision.dispatched_task_ids` | 🟨 낮 | §5.1 |
| `graph/plan_ops.py` | batch 메서드 + `unprocessed_reports` | 🟧 중 | §4.1~4.3 |
| `graph/dispatcher.py` | `Send` API, batch 분기 | 🟥 높 | §5.2~5.3 |
| `graph/replanner.py` | 다중 patch decision | 🟧 중 | §7.1, §6.3 |
| `config.py` | flag + 상한 상수 | 🟨 낮 | §10.1 |
| `runtime.py` | `active_task_ids` 출력 | 🟨 낮 | §3.3 |
| `api/routers/chat.py` | SSE 라벨 | 🟨 낮 | §8.1 |
| `tests/` | 신규 6개 + 회귀 통과 | 🟧 중 | §3~7 |
| `scripts/run_manufacturing_scenarios_v2.py` | R11~R13 추가 | 🟨 낮 | §9.3 |
| `scripts/run_manufacturing_scenarios.py` | **변경 없음** (v2가 라이브러리로 import) | — | — |

---

## 12. 함정 / 조심할 것

- [ ] **`execution_plan`을 두 worker가 동시 write 금지** — worker는 자기 artifact 필드만, plan 변경은 dispatcher 1점.
- [ ] **`retry_counts`에 단순 `add` reducer 쓰지 말 것** — `add`는 list append용. 반드시 `dict_merge_max`.
- [ ] **`active_task_ids` / `consumed_replan_report_indices`에 `add` 붙이지 말 것** — 단일 writer가 전체 리스트를 반환하므로 `add` 사용 시 중복 누적 발생. (ADR-0004 §이유 참조)
- [ ] **`messages` 필드는 MessagesState 상속의 `add_messages` reducer가 이미 있음** — 손대지 말 것.
- [ ] **PydanticAI sql_agent thread-safety** — 동시 호출 가능한지 확인.
- [ ] **OpenAI RPM/TPM** — 이미 측정 완료(§2.2). 현 tier에서는 worry 없음.
- [ ] **fan-in 시점에 dispatcher가 한 번만 실행되는지** Phase 5.1 토이 그래프로 먼저 확인.
- [ ] **v1 러너(`run_manufacturing_scenarios.py`)에 직접 패치 금지** — v2가 라이브러리로 import 후 `_corrected_definition_cells()`로 보정. v1 수정 시 충돌 가능.

---

## 13. 완료 정의 (Definition of Done)

- [ ] **회귀 v2 전체 시나리오 PASS** (flag ON/OFF 양쪽)
- [ ] **3-worker 평균 wall-clock이 직렬 대비 30% 이상 단축** (`/tmp/perf.txt` 증빙)
- [ ] **LLM 호출 횟수 변동 없음** (`/usage` snapshot 비교)
- [ ] **LangSmith trace에서 worker 3개가 형제 branch로 시각화** (스크린샷)
- [ ] **flag OFF로 100% 직렬 복귀 가능** (PR 1 베이스라인과 동일 결과)
- [ ] **checkpoint DB가 두 모드 모두에서 resume 가능** (§3.4 / §10.3)
- [ ] **ADR 0001~0004 머지** + **`docs/parallelization_perf.md` 머지**

---

## 14. 참고 자료

- LangGraph `Send` API: <https://langchain-ai.github.io/langgraph/concepts/low_level/#send>
- LangGraph State Reducers: <https://langchain-ai.github.io/langgraph/concepts/low_level/#reducers>
- LLMCompiler 논문: Kim et al., *An LLM Compiler for Parallel Function Calling*, ICML 2024
- LangGraph Plan-and-Execute 튜토리얼: <https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/plan-and-execute/>
- Anthropic *Building effective agents*: <https://www.anthropic.com/research/building-effective-agents>

---

## 변경 이력

- **v0.3 (2026-06-23)**
  - 시나리오 러너 v2 전환 (`run_manufacturing_scenarios_v2.py` + 신규 시나리오 ID 체계)
  - `DEFINITION_CELLS` 수동 패치 단계 제거 (v2가 자동 보정)
  - `--env-file .env` 모든 명령에 추가, 단일 줄 페이스트 안전 형태로 통일
  - `uv sync --all-extras` Phase 0 선결 조건 명시
  - §2.5 중복 매트릭스 삭제 → ADR-0004 단일 출처화
  - §3.2 state.py 어노테이션 ADR-0004 정합화 (active_task_ids 등 `add` 제거)
  - `dict_merge_last_wins` 미사용 주석 추가
  - ADR-0004 코드 블록 닫힘 오류 수정
  - v0.2의 invalid 베이스라인 측정값(0.07s / 2.4s) 폐기 → 재측정 placeholder
  - 문서 제목 빈 `#` 수정 / `!image.png` 표준 마크다운 표기 / strikethrough 항목 정리
- **v0.2.1 (2026-06-23)**: 시나리오 runner CLI 플래그 `--only` → `--scenario` 정정
- **v0.2 (2026-06-23)**: 각 체크리스트 항목에 실행 코드 첨부
- **v0.1**: 초기 작성
