# RAG 평가 (Evidence Agent)

`evidence_agent`의 RAG를 **검색(IR) + 생성품질(RAGAS) + 예측정확도** 세 축으로 평가한다.
평가셋은 **2개 트랙**으로 구성된다.

| 트랙 | 설명 | profile | 생성기 | 문항 |
|---|---|---|---|---|
| **1-1** | 단순 정비 지식 검색(예측 없음) | troubleshooting_rag | `gen_testset.py`(RAGAS TestsetGenerator) | 21 `k11_*` |
| **1-2** | 예측 기반 근거 검색(변수+예측) | prediction_plus_rag | `gen_dataset_1_2.py`(시나리오+예측+GT) | 8 `k12_*` |

```
gen_testset.py        ─┐  (1-1: TestsetGenerator → 카테고리 분류 → 번역)
gen_dataset_1_2.py    ─┘  (1-2: 시나리오 질문 + 변수 + run_prediction + GT)
        → dataset_1_1.jsonl + dataset_1_2.jsonl = dataset.jsonl
        → build_samples.py   (RAG 실행: contexts+answer 캐시, 1-2는 mode B 예측 주입)
        → samples.jsonl
        ├→ run_ragas.py      (RAGAS 4지표 + 예측 정확도)  → ragas_scores.csv
        └→ ir_eval.py        (Recall@K/Precision@K/MRR)   → ir_scores.csv
```

## 1. 사전 준비

```bash
uv add "ragas>=0.2,<0.3" datasets rapidfuzz     # RAGAS 본체 + TestsetGenerator 의존
# OPENAI_API_KEY, VECTOR_BACKEND=pinecone, 색인(haas) 은 .env 에 이미 있음
```

## 2. 실행

```bash
# (선택) 평가셋 재생성
PYTHONUTF8=1 PYTHONPATH=. uv run python evals/ragas/gen_testset.py --size 20     # 1-1
PYTHONUTF8=1 PYTHONPATH=. uv run python evals/ragas/gen_dataset_1_2.py           # 1-2
cat evals/ragas/dataset_1_1.jsonl evals/ragas/dataset_1_2.jsonl > evals/ragas/dataset.jsonl

# (1) RAG 실행 결과 캐시 — threshold 낮춰 커버리지↑
MIN_EVIDENCE_SCORE=0.3 PYTHONUTF8=1 PYTHONPATH=. uv run python evals/ragas/build_samples.py

# (2) RAGAS 채점 + 예측 정확도
PYTHONUTF8=1 PYTHONPATH=. uv run python evals/ragas/run_ragas.py

# (3) IR 지표(Recall@K/Precision@K/MRR)
PYTHONUTF8=1 PYTHONPATH=. uv run python evals/ragas/ir_eval.py --tau-ref 0.65 --tau-gt 0.42
```

> Windows에서 `python` 직접 실행 시 `No module named 'pinecone'` 가 나면 **반드시 `uv run`** 으로 실행.

## 3. 지표

### RAGAS (생성 품질, LLM judge)

| 지표 | 필요 입력 | 의미 |
|---|---|---|
| **faithfulness** | answer, contexts | 답변이 검색 문맥에 **근거**하는가(환각 여부) |
| **answer_relevancy** | question, answer | 답변이 질문에 **관련**되는가 |
| **context_precision** | question, contexts, ground_truth | 관련 문맥이 **상위에** 검색됐는가 |
| **context_recall** | contexts, ground_truth | 정답에 필요한 문맥을 **다 검색**했는가 |

### IR (검색 품질, 임베딩 매칭) — `ir_eval.py`

랭커 ranked top-10을 **gold passage**와 cosine 매칭(threshold cut 이전 raw 랭킹).

- **Gold**: 1-1 = `reference_contexts`(GOLD) / 1-2 = `ground_truth` 1개(**SILVER proxy**)
- **관련 판정**: cosine ≥ τ (`--tau-ref 0.65` 영-영 / `--tau-gt 0.42` 한-영)
- **Precision@K** = top-K 중 관련 청크/K, **Recall@K** = top-K가 커버한 gold/총 gold, **MRR** = 1/첫 관련 순위

### 예측 정확도 (1-2 전용) — `run_ragas.py` 출력

`input_features` → `run_prediction` 의 **high위험** failure_type 이 `expected_failure_types` 를 포함하는가(결정적).

## 4. 구성 파일

| 파일 | 역할 |
|---|---|
| `gen_testset.py` | 1-1 생성: PDF(Spindle/Chatter/Manual토픽) → TestsetGenerator → 카테고리 분류 → 한국어 번역 → `dataset_1_1.jsonl` |
| `gen_dataset_1_2.py` | 1-2 생성: 시나리오 질문 + AI4I 변수 + `run_prediction` + GT → `dataset_1_2.jsonl` |
| `build_samples.py` | RAG 실행 재현(1-2는 mode B 예측 주입) → `samples.jsonl` |
| `run_ragas.py` | RAGAS 채점 + 예측 정확도 → `ragas_scores.csv` |
| `ir_eval.py` | Recall@K/Precision@K/MRR → `ir_scores.csv` |
| `RESULTS.md` | **결과·해석 리포트** |
| `dataset*.jsonl`, `samples.jsonl`, `*_scores.csv` | 데이터/산출물 |
| `dataset_handwritten.jsonl`, `dataset_generated.jsonl` | 과거 평가셋 백업 |

### 데이터 스키마 (`dataset.jsonl`)

```jsonc
{
  "id": "k11_04" | "k12_02",
  "track": "1-1" | "1-2",
  "category": "cause|inspection|maintenance|concept | pred_explain|pred_response|pred_variable",
  "question": "...", "ground_truth": "...",
  "profile": "troubleshooting_rag" | "prediction_plus_rag",
  "expect_evidence": true,
  // 1-1 전용
  "reference_contexts": ["출처 청크 ..."],
  // 1-2 전용
  "input_features": {"type":"M","torque":62.0, ...},
  "expected_failure_types": ["OSF"]
}
```

## 5. 주의

- **ground_truth는 초안**(LLM 생성). Haas 매뉴얼 기준 사람 검수 시 점수 신뢰도↑.
- **answer는 evidence 요약 경로**(`EVIDENCE_SUMMARY_SYSTEM`) 재현. 전체 final_answer가 아님 → answer_relevancy가 포맷상 낮게 나옴(`RESULTS.md` §5-3).
- **IR @K는 절대값이 아니라 하한**으로 해석: gold(`reference_contexts`)와 Pinecone 색인의 **청킹 경계가 달라** 진짜 매칭 코사인도 눌린다(`RESULTS.md` §5-4). 1-2 IR은 교차언어로 **참고용(비신뢰)**.
- **MIN_EVIDENCE_SCORE**: 기본 0.45는 한국어 추상 질의에서 NO_EVIDENCE가 잦음 → 평가는 **0.3**으로 커버리지 확보(0.45→0.3에서 NO_EVIDENCE 8/12→3/29).
- **NO_EVIDENCE(contexts 0건)** 샘플은 RAGAS context/IR 지표에서 제외(스크립트가 경고).
- RAGAS는 버전 민감 — `run_ragas.py`/`gen_testset.py`에 ragas 0.2↔langchain 1.x vertexai shim 포함.
- judge 모델 기본 `gpt-4o-mini`. 엄격히 보려면 `--judge-model gpt-4o`.
