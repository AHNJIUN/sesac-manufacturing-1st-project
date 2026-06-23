# ADR-0001 병렬 정책

## 결정
prediction → (sql, evidence) 2단계.
planner가 sql/evidence task의 depends_on=["prediction_1"]을 명시한다.
prediction이 없으면 sql/evidence가 즉시 병렬.

## 이유
prediction.failure_type을 sql/evidence가 활용하는 케이스(S5-3 등)가 존재.
병렬화 이득보다 cross-worker 정보 사용이 정확도에 더 중요.

## 영향
planner.py의 _sql_task / _evidence_task 빌더 수정.
