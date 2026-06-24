# v0.3 baseline known failures (2026-06-23)

병렬화 작업 시작 시점에 이미 FAIL인 시나리오 — **별도 코드 작업 영역**.
병렬화 PR 회귀 시 이 5건은 비교 대상에서 제외하고, PASS 23건이 그대로 유지되는지만 확인한다.

- S4-3_diagnosis_history_evidence
- S5_multiturn_rediagnose
- R4_multiturn_sql_followup
- R5_multiturn_evidence_followup
- R9_broad_lookup_no_contamination

병렬화로 인한 회귀 식별 규칙:
- PASS 23건 → PASS 23건 (유지): OK
- PASS 23건 → PASS < 23건: 병렬화 회귀, 조사 필요
- FAIL 5건 → FAIL 6건: 새 FAIL 발생, 병렬화 회귀
- FAIL 5건 → FAIL 4건: 우연한 통과, 별도 영역 진행 결과로 추정 (병렬화 무관)
