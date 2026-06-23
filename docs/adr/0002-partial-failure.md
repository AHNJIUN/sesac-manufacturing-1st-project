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
