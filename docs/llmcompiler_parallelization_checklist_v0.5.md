# LLMCompiler 스타일 DAG 병렬화 적용 체크리스트 (v0.5)

> 대상: 현재 `manufacturing_agent/graph/` 구조 (Plan-and-Execute, 직렬 실행)
> 목표: planner가 만든 worker task 중 **의존성 없는 task를 동시에 실행**
> 핵심 도구: LangGraph **`Send` API** + **State reducer**
> 작성일: 2026-06-23
>
> **v0.5 변경 요약** (v0.4 → v0.5)
> - **팀 작업 pull 반영**: `f918734` 멀티턴 검증셋 확장, `e299d43` recent_turns 정리, `37bf39c` 최근 대화 윈도우 턴수 기준 전환, `182a87d` 멀티턴 결정 정확도 개선이 main에 들어옴.
> - **베이스라인 재측정 강제**: v0.4 시점의 PASS 23 / FAIL 5 정책은 pull 이전 상태. 위 변경이 멀티턴/context를 손댔으므로 다음 5개 시나리오 중 일부 또는 전부가 회복됐을 가능성이 큼:
>   - S5_multiturn_rediagnose
>   - R4_multiturn_sql_followup
>   - R5_multiturn_evidence_followup
>   - R9_broad_lookup_no_contamination
>   - (S4-3는 3-worker 자체 영역이라 회복 가능성 낮음)
> - **§1.1.0 신규: pull 후 재측정 절차 도입**. 그 결과에 따라 측정 시나리오 (S4-3 vs S5-3) 자동 결정.
> - **측정 시나리오 정책 조건부 변경**:
>   - 재측정 후 **S4-3 PASS** → S4-3로 측정 복귀 (3-worker, 단일 턴, 변동 작음)
>   - 재측정 후 **S4-3 여전히 FAIL** → S5-3 유지 (v0.4와 동일)
> - **PR 1 산출물에 `baseline_regression_after_pull.log` 포함**.
> - v0.4와의 backward-compat 유지: 모든 v0.4 산출물(adr/0001~0004, openai_rate_limits.log 등)은 그대로 사용.

---

## 0. 사전 이해

LLMCompiler(Kim et al., 2023)의 핵심 두 가지:

1. **Planner가 task DAG를 먼저 만든다** — 이 프로젝트는 이미 `TaskSpec.depends_on`으로 표현 중. 100% 보유.
2. **Executor가 의존성 만족된 task를 병렬로 dispatch** — 이 프로젝트는 dispatcher가 **단수**로 픽업 중. 이 부분만 바꾸면 된다.

즉 **planner 쪽은 거의 손대지 않고, dispatcher + state + LangGraph wiring만 수정**하는 작업이다.

---

## 1. Phase 0 — 사전 조사 & 기준점 확보

### 1.0 의존성 동기화 (선결 조건)

- [x] **`.venv` 의존성 전체 설치**
  ```bash
  uv sync --all-extras
  ```

- [x] **핵심 모듈 import 가능성 확인**
  ```bash
  uv run --env-file .env python -c "
  import langchain_openai, langgraph
  from importlib.metadata import version
  from langgraph.types import Send
  print('OK langgraph=', version('langgraph'))
  print('OK langchain_openai=', version('langchain_openai'))
  "
  ```
  **현재 확인됨**: langgraph `1.2.5` / langchain_openai `1.3.2` / Send import OK.

### 1.1 베이스라인 측정 및 정책

#### 1.1.0 (v0.5 신규) 팀 작업 pull 후 베이스라인 재측정

> v0.4 정책(PASS 23 / FAIL 5)은 pull 이전 시점이다.
> 팀이 멀티턴/context/SQL 영역을 손댔으므로 v0.5 작업 시작 전 반드시 다시 측정한다.

- [ ] **pull 후 회귀 재측정**
  ```bash
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 \
    | tee docs/_scratch/baseline_regression_after_pull.log
  grep -E "PASS|FAIL" docs/_scratch/baseline_regression_after_pull.log | sort | uniq -c
  echo "---FAIL 시나리오---"
  grep "FAIL" docs/_scratch/baseline_regression_after_pull.log
  ```

- [ ] **결과 판정 + 정책 분기 결정**

  | 시나리오 | 새 PASS 카운트 | 다음 조치 |
  |---|---|---|
  | (A) 28/28 PASS | 모두 통과 | `baseline_known_failures.md` **폐기**, S4-3 측정 시나리오로 복귀, §1.1.2/§9.4의 시나리오 수정 |
  | (B) 24~27 PASS | 부분 회복 | `baseline_known_failures.md` 갱신 (회복된 시나리오 제거), S4-3가 PASS면 S4-3 측정, 아니면 S5-3 유지 |
  | (C) 23/28 변동 없음 | 회복 없음 | v0.4 정책 그대로 (S5-3 측정, FAIL 5건 그대로) |

- [ ] **`baseline_known_failures.md` 갱신 (B/C 결과 시)**
  ```bash
  # 예시: B 결과 — S5/R4/R5/R9는 회복, S4-3만 남은 경우
  cat > docs/_scratch/baseline_known_failures.md <<'EOF'
  # v0.5 baseline known failures (pull 후 2026-06-23 재측정)

  pull(#14 merge)으로 멀티턴/context/SQL 영역 회복 후,
  여전히 FAIL인 시나리오 — 별도 코드 작업 영역.
  병렬화 PR 회귀 시 이 시나리오는 비교 대상에서 제외하고,
  PASS N건이 그대로 유지되는지만 확인한다.

  - S4-3_diagnosis_history_evidence   # 또는 실제 잔여 FAIL 목록

  병렬화로 인한 회귀 식별 규칙:
  - PASS N건 → PASS N건 (유지): OK
  - PASS N건 → PASS < N건: 병렬화 회귀, 조사 필요
  EOF
  ```

#### 1.1.1 측정 시나리오 결정 (1.1.0 결과 의존)

> v0.4는 S4-3 FAIL 정책으로 S5-3 사용. v0.5는 pull 재측정 결과로 동적 결정.

- [ ] **S4-3가 새 PASS면 S4-3 사용 (권장, 측정 변동 작고 단일 턴)**
- [ ] **S4-3가 여전히 FAIL이면 S5-3 사용 (v0.4 정책 유지)**

#### 1.1.2 wall-clock 5회 측정

**시나리오 A: S4-3 PASS 복귀 시**
- [ ] **S4-3 측정**
  ```bash
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence > /dev/null 2>&1

  for i in 1 2 3 4 5; do
    /usr/bin/time -p uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence 2>&1 | grep real
  done | tee docs/_scratch/baseline_serial_S4-3.log

  awk '/real/{s+=$2; n++} END{print "avg:", s/n, "sec"}' docs/_scratch/baseline_serial_S4-3.log
  ```
  기대 avg: **15~25초** (참고: pull 전 측정 20.53 sec).

**시나리오 B: S4-3 FAIL 유지 시**
- [ ] **S5-3 측정 (v0.4와 동일)**
  ```bash
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S5-3_multiturn_history_evidence > /dev/null 2>&1

  for i in 1 2 3 4 5; do
    /usr/bin/time -p uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S5-3_multiturn_history_evidence 2>&1 | grep real
  done | tee docs/_scratch/baseline_serial_S5-3.log

  awk '/real/{s+=$2; n++} END{print "avg:", s/n, "sec"}' docs/_scratch/baseline_serial_S5-3.log
  ```
  기대 avg: **30~40초**.

#### 1.1.3 참고: 측정 이력

| 측정 시점 | 시나리오 | avg | 비고 |
|---|---|---|---|
| 2026-06-23 (pull 전) | S4-3 | 20.53 sec | runtime 정상 종료, 체커는 FAIL 분류 |
| (pull 후) | S4-3 또는 S5-3 | ? | §1.1.0/1.1.2 재측정 필요 |

#### 1.1.4 시나리오 worker 조합 표

- [x] **시나리오 조합 출력 (v0.4 동일, 28건)**
  ```bash
  uv run python -c "
  import sys; sys.path.insert(0, 'scripts')
  from run_manufacturing_scenarios_v2 import scenarios
  for s in scenarios():
      print(f'{s.sid:40s} mode={s.mode:8s} tags={s.tags}')
      print(f'    └ {s.description}')
  " | tee docs/_scratch/scenario_workers_v2.log
  ```

  **결과 요약**:
  - 3-worker (prediction + sql + rag): S4-3, S5-3
  - 2-worker 조합: S4-1, S4-2, S5-1, S5-2, S8
  - 1-worker / 0-worker: 21건

#### 1.1.5 회귀 PASS/FAIL — pull 후 갱신

- [ ] **회귀 결과 (pull 후 측정값 기록 placeholder)**
  ```text
  # 실행 결과 (2026-06-23 pull 후 재측정 후 채울 것)
  PASS: ?
  FAIL: ?  (시나리오: ?)
  ```

#### 1.1.6 (선택) 그룹별 빠른 회귀

- [ ] **B / R 트랙 분리 회귀**
  ```bash
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --group B 2>&1 | tee docs/_scratch/baseline_groupB.log
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --group R 2>&1 | tee docs/_scratch/baseline_groupR.log
  ```

#### 1.1.7 LangSmith trace 캡처

- [ ] **직렬 trace 1건 (S4-3 또는 S5-3 기준 시나리오)**
  ```bash
  LANGSMITH_TRACING=true LANGCHAIN_TRACING_V2=true \
    uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py --scenario S4-3_diagnosis_history_evidence > /dev/null 2>&1
  # 또는 시나리오 B인 경우 S5-3_multiturn_history_evidence
  # LangSmith UI → trace → docs/_scratch/serial_trace_baseline.png
  ```

### 1.2 LangGraph 버전 확인

- [x] **langgraph 1.2.5 / langgraph-checkpoint-sqlite 3.1.0 확인 완료**
- [x] **~~0.2.39 미만 업그레이드~~ — 해당 없음**
- [x] **`Send` import OK**

### 1.3 코드 그래프 정독

- [x] **dispatcher 단수 라우팅 baseline (`docs/_scratch/dispatcher_single_route_baseline.log`)**
  9건 매치. 핵심 변경 대상은 **라인 53**(`next_runnable`), **라인 78**(`route.next_node`).

- [ ] **(v0.5 신규) pull 후 dispatcher 변경 여부 확인**
  ```bash
  grep -n "next_runnable\|RouteDecision\|next_node" manufacturing_agent/graph/dispatcher.py \
    | tee docs/_scratch/dispatcher_after_pull.log

  diff docs/_scratch/dispatcher_single_route_baseline.log docs/_scratch/dispatcher_after_pull.log
  ```
  → diff가 없으면 dispatcher는 그대로. diff가 있으면 baseline 갱신 필요.

- [x] **state reducer 어노테이션 현황 (`docs/_scratch/state_reducer_annotations.log`)**
  PR 2에서 손댈 라인:
  - 라인 **41** `gate_reports` → `Annotated[list, add]`
  - 라인 **42** `retry_counts` → `Annotated[dict, dict_merge_max]`
  - 신규 `active_task_ids: list[str]`, `consumed_replan_report_indices: list[int]`

- [ ] **(v0.5 신규) pull 후 state.py 변경 여부 확인**
  ```bash
  uv run python -c "
  from typing import get_type_hints
  from manufacturing_agent.contracts.state import ManufacturingState
  for name, t in get_type_hints(ManufacturingState, include_extras=True).items():
      print(f'{name:35s} {t}')
  " | tee docs/_scratch/state_reducer_after_pull.log

  diff docs/_scratch/state_reducer_annotations.log docs/_scratch/state_reducer_after_pull.log
  ```
  → diff가 없으면 state는 그대로. diff가 있으면 ADR-0004 정합성 재검토.

---

## 2. Phase 1 — 설계 결정 (코드 변경 전 확정)

> v0.4의 ADR 4종(0001~0004)은 그대로 유지. pull로 인한 영향 없음.
> 단, pull 후 회귀가 회복됐다면 §2.5 ADR-0004의 reducer 정책에는 변동이 없으나, ADR-0001의 depends_on 영향 시나리오 예시는 갱신 가능.

### 2.1 병렬 정책 (ADR-0001) — v0.4 그대로

- [ ] **ADR-0001 작성** (v0.4 §2.1과 동일)
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

### 2.2 동시 실행 상한 — v0.4 그대로

- [ ] **config.py에 상수 추가**
  ```python
  MAX_PARALLEL_WORKERS = int(os.environ.get("MAX_PARALLEL_WORKERS", "3"))
  ```

- [x] **OpenAI tier 측정 완료 (v0.4)**

  | 모델 | RPM | TPM | tier |
  |---|---|---|---|
  | gpt-4o | 10,000 | 2,000,000 | Tier 4 |
  | gpt-4o-mini | 10,000 | 10,000,000 | Tier 4~5 |
  | gpt-4.1-mini | 10,000 | 10,000,000 | Tier 4~5 |

  **결론**: rate limit는 병렬화 제약 아님.

### 2.3 부분 실패 정책 (ADR-0002) — v0.4 그대로

- [ ] **ADR 작성** (v0.4 §2.3과 동일)

### 2.4 Replanner 트리거 시점 (ADR-0003) — v0.4 그대로

- [ ] **ADR 작성** (v0.4 §2.4와 동일)

### 2.5 State 일관성 정책 (ADR-0004) — v0.4 그대로

- [ ] **ADR-0004 작성** (v0.4 §2.5와 동일)

  > 핵심 정책 (변동 없음):
  > - reducer 필요 필드: `gate_reports` (add), `retry_counts` (dict_merge_max)
  > - 그 외 모든 필드: reducer 없음 (last-write-wins)
  > - `active_task_ids`, `consumed_replan_report_indices`: 단일 writer라 `add` 절대 금지

---

## 3~10. Phase 2~9 — v0.4 그대로

> Phase 2(state 스키마) ~ Phase 9(롤아웃)는 v0.4와 동일.
> 변경된 것은 베이스라인 회귀 비교 시 사용하는 **PASS 카운트만** (`baseline_known_failures.md`의 N값).

### v0.4 → v0.5 변경 없는 섹션 요약

- §3 Phase 2 — State 스키마 (reducers.py + state.py 어노테이션)
- §4 Phase 3 — PlanOps batch 메서드
- §5 Phase 4 — Dispatcher Send fan-out
- §6 Phase 5 — Gate fan-in
- §7 Phase 6 — Replanner batch
- §8 Phase 7 — chat/runtime/observability
- §9 Phase 8 — 테스트 (단위 + 통합)
- §10 Phase 9 — 롤아웃

**참조**: v0.4 문서의 §3~§10 그대로 적용. 다음 두 가지만 v0.5에서 변동:
1. §9.2 회귀 PASS 카운트 비교 기준 (23 → 새 N값으로 치환)
2. §9.4 성능 측정 시나리오 (S5-3 → S4-3, 시나리오 A 결정 시)

### §9.2 변경분 (회귀 비교)

- [ ] **flag OFF (직렬) 회귀: PASS N건 유지 확인**
  ```bash
  unset PARALLEL_DISPATCH
  uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 | tee docs/_scratch/after_serial.log
  echo "after pull baseline:"; grep -c "PASS" docs/_scratch/baseline_regression_after_pull.log
  echo "after_serial:";        grep -c "PASS" docs/_scratch/after_serial.log
  ```

- [ ] **flag ON (병렬) 회귀: PASS N건 유지 확인**
  ```bash
  PARALLEL_DISPATCH=1 MAX_PARALLEL_WORKERS=3 \
    uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 | tee docs/_scratch/after_parallel.log
  echo "after_parallel:";      grep -c "PASS" docs/_scratch/after_parallel.log
  ```

  > **회귀 판정 규칙**:
  > - pull-after PASS N → PASS N: **OK 머지**
  > - pull-after PASS N → PASS < N: **병렬화 회귀, 조사 필요**
  > - 신규 FAIL 발생: **병렬화 회귀**

### §9.4 변경분 (성능 측정)

- [ ] **시나리오 A (S4-3 PASS 복귀) — S4-3로 측정**
  ```bash
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

- [ ] **시나리오 B (S4-3 FAIL 유지) — S5-3로 측정 (v0.4와 동일)**

---

## 11. 변경 영향 매트릭스 — v0.4 그대로

| 파일 | 변경 종류 | 위험도 | 코드 위치 |
|---|---|---|---|
| `contracts/reducers.py` (신규) | `dict_merge_max` 필수 / `dict_merge_last_wins` 미사용 마킹 | 🟨 낮 | §3.1 |
| `contracts/state.py` | reducer 어노테이션 2개 + 신규 필드 | 🟧 중 | §3.2 |
| `contracts/context.py` | `OrchestratorDecision.dispatched_task_ids` | 🟨 낮 | §5.1 |
| `graph/plan_ops.py` | batch 메서드 + `unprocessed_reports` | 🟧 중 | §4 |
| `graph/dispatcher.py` | `Send` API, batch 분기 | 🟥 높 | §5.2~5.3 |
| `graph/replanner.py` | 다중 patch decision | 🟧 중 | §6.3, §7.1 |
| `config.py` | flag + 상한 상수 | 🟨 낮 | §10.1 |
| `runtime.py` | `active_task_ids` 출력 | 🟨 낮 | §3.3 |
| `api/routers/chat.py` | SSE 라벨 | 🟨 낮 | §8.1 |
| `tests/` | 신규 6개 + 회귀 통과 | 🟧 중 | §3~7 |
| `scripts/run_manufacturing_scenarios_v2.py` | R11~R13 추가 | 🟨 낮 | §9.3 |
| `scripts/run_manufacturing_scenarios.py` | **변경 없음** | — | — |
| `docs/_scratch/baseline_known_failures.md` (신규/갱신) | FAIL N건 정책 | 🟨 낮 | §1.1.0 |
| `docs/_scratch/baseline_regression_after_pull.log` (신규) | pull 후 회귀 결과 | 🟨 낮 | §1.1.0 |

---

## 12. 함정 / 조심할 것 — v0.4 + v0.5 신규

- [ ] **`execution_plan`을 두 worker가 동시 write 금지**
- [ ] **`retry_counts`에 단순 `add` reducer 금지** — 반드시 `dict_merge_max`
- [ ] **`active_task_ids` / `consumed_replan_report_indices`에 `add` 금지**
- [ ] **`messages`는 `add_messages` 이미 있음**
- [ ] **PydanticAI sql_agent thread-safety 확인**
- [ ] **fan-in 시점 dispatcher 1회만 실행되는지** — §6.1 토이 그래프로 사전 확인
- [ ] **v1 러너 직접 패치 금지** — v2 import 시 충돌 가능
- [ ] **(v0.5) pull 후 baseline_known_failures 정책 사용 — `_after_pull.log` 기준**
- [ ] **(v0.5) S4-3 측정 사용은 pull 후 재측정 통과 후에만**
- [ ] **(v0.5) pull로 dispatcher/state가 변경됐는지 §1.3 diff 명령으로 확인**

---

## 13. 완료 정의 (Definition of Done)

- [ ] **회귀 PASS N건 유지** (pull 후 새 PASS 카운트 기준)
- [ ] **측정 시나리오(S4-3 또는 S5-3) 평균 wall-clock이 직렬 대비 30% 이상 단축**
- [ ] **LLM 호출 횟수 변동 없음**
- [ ] **LangSmith trace에서 worker 3개 형제 branch 시각화**
- [ ] **flag OFF로 100% 직렬 복귀 가능**
- [ ] **checkpoint DB 두 모드 모두 resume 가능**
- [ ] **ADR 0001~0004 + `baseline_known_failures.md`(갱신본) + `docs/parallelization_perf.md` 머지**

---

## 14. 참고 자료

- LangGraph `Send` API: <https://langchain-ai.github.io/langgraph/concepts/low_level/#send>
- LangGraph State Reducers: <https://langchain-ai.github.io/langgraph/concepts/low_level/#reducers>
- LLMCompiler 논문: Kim et al., *An LLM Compiler for Parallel Function Calling*, ICML 2024
- LangGraph Plan-and-Execute 튜토리얼: <https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/plan-and-execute/>
- Anthropic *Building effective agents*: <https://www.anthropic.com/research/building-effective-agents>

---

## 15. v0.4 → v0.5 마이그레이션 요약 (5분 가이드)

새로 들어온 사람이 v0.5를 빠르게 시작하기 위한 최소 절차:

1. **pull 후 재측정**
   ```bash
   uv run --env-file .env python scripts/run_manufacturing_scenarios_v2.py 2>&1 | tee docs/_scratch/baseline_regression_after_pull.log
   grep -c "PASS" docs/_scratch/baseline_regression_after_pull.log   # = 새 N
   grep "FAIL" docs/_scratch/baseline_regression_after_pull.log
   ```

2. **시나리오 분기 결정**
   - 새 PASS == 28 → 시나리오 A (S4-3 복귀)
   - 새 PASS < 28 → S4-3가 잔여 FAIL 목록에 있는가?
     - YES → 시나리오 B (S5-3 유지)
     - NO → 시나리오 A (S4-3 복귀)

3. **`baseline_known_failures.md` 갱신** (§1.1.0 템플릿 사용)

4. **선택한 시나리오로 wall-clock 5회 측정** (§1.1.2)

5. **dispatcher / state.py diff 확인** (§1.3 v0.5 신규 항목)

6. **v0.4 결과들이 그대로 유효**: ADR 4종, openai_rate_limits.log, scenario_workers_v2.log, dispatcher_single_route_baseline.log, state_reducer_annotations.log → 재생성 불필요.

→ Phase 1 ADR 작성으로 진입.

---

## 변경 이력

- **v0.5 (2026-06-23)**
  - 팀 작업 pull(#14 merge) 반영
  - 베이스라인 재측정 단계 §1.1.0 신규 도입
  - 측정 시나리오 조건부 결정 (S4-3 vs S5-3)
  - dispatcher / state.py pull 후 변경 여부 diff 확인 단계 §1.3 추가
  - `baseline_known_failures.md` 갱신 정책 명시
  - PR 1 산출물에 `baseline_regression_after_pull.log` 포함
- **v0.4 (2026-06-23)**: PASS 23 / FAIL 5 known failures 정책, S4-3 → S5-3 측정 시나리오 교체, `langgraph.__version__` → `importlib.metadata.version` 등
- **v0.3 (2026-06-23)**: v2 러너 전환, DEFINITION_CELLS 수동 패치 제거, ADR-0004 정합화
- **v0.2.1 (2026-06-23)**: `--only` → `--scenario` 정정
- **v0.2 (2026-06-23)**: 각 항목에 실행 코드 첨부
- **v0.1**: 초기 작성
