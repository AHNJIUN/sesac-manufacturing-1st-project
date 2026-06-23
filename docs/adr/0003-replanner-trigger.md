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
