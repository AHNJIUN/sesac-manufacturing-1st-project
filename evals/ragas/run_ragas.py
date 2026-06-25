"""RAGAS 채점기.

build_samples.py 가 만든 samples.jsonl 을 읽어 RAGAS 지표를 계산한다.
지표(기본): faithfulness, answer/response relevancy, context precision, context recall.

판정 LLM/임베딩은 OpenAI(langchain_openai)를 RAGAS 래퍼로 감싸 사용한다.

사전 설치:
    uv add "ragas>=0.2,<0.3" datasets        # 또는 uv pip install
실행:
    PYTHONUTF8=1 PYTHONPATH=. python evals/ragas/run_ragas.py
    python evals/ragas/run_ragas.py --judge-model gpt-4o   # 판정 모델 변경
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "samples.jsonl"


def _shim_langchain_vertexai() -> None:
    """ragas 0.2 ↔ langchain 1.x 호환 shim.

    ragas 0.2.x의 ragas/llms/base.py 가 `langchain_community.chat_models.vertexai` 와
    `langchain_community.llms.VertexAI` 를 eager import 하는데, langchain-community 1.x(0.4+)
    에서 해당 경로가 사라져 import가 깨진다. 우리는 OpenAI judge만 쓰므로 미사용 VertexAI를
    더미로 채워 import만 통과시킨다.
    """
    import types
    import langchain_community.chat_models  # noqa: F401
    if "langchain_community.chat_models.vertexai" not in sys.modules:
        mod = types.ModuleType("langchain_community.chat_models.vertexai")
        mod.ChatVertexAI = type("ChatVertexAI", (), {})
        sys.modules["langchain_community.chat_models.vertexai"] = mod
    import langchain_community.llms as _llms
    if not hasattr(_llms, "VertexAI"):
        _llms.VertexAI = type("VertexAI", (), {})


try:
    _shim_langchain_vertexai()
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
except Exception as e:  # pragma: no cover
    print("RAGAS/langchain_openai 임포트 실패:", e)
    print('설치 필요:  uv add "ragas>=0.2,<0.3" datasets')
    raise SystemExit(1)


def _load_metrics():
    """ragas 0.2(class) 우선, 구버전(instance) 폴백."""
    try:  # ragas >= 0.2
        from ragas.metrics import (
            Faithfulness, ResponseRelevancy,
            LLMContextPrecisionWithReference, LLMContextRecall,
        )
        return [Faithfulness(), ResponseRelevancy(),
                LLMContextPrecisionWithReference(), LLMContextRecall()]
    except Exception:  # ragas 0.1.x
        from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
        return [faithfulness, answer_relevancy, context_precision, context_recall]


def _load_env() -> None:
    """프로젝트 루트 .env 를 os.environ 에 로드(OPENAI_API_KEY 등). 이미 있으면 skip."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    env = HERE.parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v[:1] not in "\"'" and " #" in v:
            v = v.split(" #", 1)[0].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


def _build_dataset(rows: list[dict]):
    """ragas 0.2 EvaluationDataset 우선, 구버전 HF Dataset 폴백."""
    try:  # ragas >= 0.2
        from ragas import EvaluationDataset
        return EvaluationDataset.from_list([
            {"user_input": r["question"], "response": r["answer"],
             "retrieved_contexts": r["contexts"], "reference": r["ground_truth"]}
            for r in rows
        ])
    except Exception:  # ragas 0.1.x
        from datasets import Dataset
        return Dataset.from_dict({
            "question": [r["question"] for r in rows],
            "answer": [r["answer"] for r in rows],
            "contexts": [r["contexts"] for r in rows],
            "ground_truth": [r["ground_truth"] for r in rows],
        })


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGAS 채점")
    ap.add_argument("--judge-model", default=os.environ.get("RAGAS_JUDGE_MODEL", "gpt-4o-mini"))
    ap.add_argument("--embed-model", default=os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"))
    args = ap.parse_args()
    _load_env()

    if not SAMPLES.exists():
        print(f"{SAMPLES} 없음 — 먼저 build_samples.py 를 실행하세요.")
        return 1
    samples = [json.loads(l) for l in open(SAMPLES, encoding="utf-8") if l.strip()]

    # context 지표는 retrieved_contexts가 필요 → 빈 것은 제외(있으면 경고)
    rows = [s for s in samples if s.get("contexts")]
    skipped = [s["id"] for s in samples if not s.get("contexts")]
    if skipped:
        print(f"⚠ contexts 0건 제외(NO_EVIDENCE 등): {skipped}")
    if not rows:
        print("채점할 샘플이 없습니다(모두 contexts 0건).")
        return 1

    judge = LangchainLLMWrapper(ChatOpenAI(model=args.judge_model, temperature=0))
    emb = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=args.embed_model))

    print(f"[judge={args.judge_model} embed={args.embed_model}] 샘플 {len(rows)}건 채점 중…")
    result = evaluate(dataset=_build_dataset(rows), metrics=_load_metrics(), llm=judge, embeddings=emb)

    print("\n=== RAGAS 점수(평균) ===")
    print(result)
    try:
        df = result.to_pandas()
        out_csv = HERE / "ragas_scores.csv"
        df.to_csv(out_csv, index=False, encoding="utf-8-sig")
        # 케이스별 점수 요약
        idcol = "id" if "id" in df.columns else None
        metric_cols = [c for c in df.columns if c not in {"user_input", "response", "retrieved_contexts",
                                                          "reference", "question", "answer", "contexts",
                                                          "ground_truth", "id"}]
        print("\n=== 케이스별 ===")
        for i, r in df.iterrows():
            label = rows[i]["id"] if i < len(rows) else i
            vals = " ".join(f"{c}={r[c]:.2f}" for c in metric_cols if isinstance(r[c], (int, float)))
            print(f"  {label:<20} {vals}")
        print(f"\nCSV 저장: {out_csv}")
    except Exception as e:
        print("상세 표 변환 생략:", e)

    # ③ 예측 정확도 (1-2: input_features 기반 케이스만) — RAGAS와 별개의 결정적 지표
    pred_rows = [s for s in samples if s.get("expected_failure_types")]
    if pred_rows:
        hit = 0
        print("\n=== 예측 정확도(1-2, high위험 기준 expected⊆predicted) ===")
        for s in pred_rows:
            exp, got = set(s.get("expected_failure_types") or []), set(s.get("predicted_failure_types") or [])
            ok = exp <= got and bool(exp)
            hit += int(ok)
            print(f"  {'✓' if ok else '✗'} {s['id']:<8} expected={sorted(exp)} predicted_high={sorted(got)}")
        print(f"  정확도: {hit}/{len(pred_rows)} = {hit/len(pred_rows):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
