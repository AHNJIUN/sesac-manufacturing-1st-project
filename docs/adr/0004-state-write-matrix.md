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
