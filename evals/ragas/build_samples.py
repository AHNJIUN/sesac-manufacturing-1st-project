"""RAGAS 입력 샘플 생성기.

dataset.jsonl 의 각 질문에 대해 **evidence_agent의 RAG 경로를 재현**해
(question, answer, contexts, ground_truth)를 만들어 samples.jsonl 로 저장한다.

- contexts: rag_search가 검색한 문서 chunk 본문(리트리버 결과)
- answer:   검색 결과로 evidence_agent와 동일하게 EVIDENCE_SUMMARY_SYSTEM 요약(LLM)
- ground_truth: dataset.jsonl의 참조 답변(사람 검수 필요)

RAG 생성(LLM·임베딩 비용)을 한 번만 하고 캐시해, RAGAS 채점은 따로 싸게 반복한다.

실행:
    PYTHONUTF8=1 PYTHONPATH=. python evals/ragas/build_samples.py
    python evals/ragas/build_samples.py --limit 2     # 일부만(테스트)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from manufacturing_agent.services.rag_service import rag_search, build_citation_aware_docs
from manufacturing_agent.agents.evidence_agent import EVIDENCE_SUMMARY_SYSTEM
from manufacturing_agent.config import call_llm
from manufacturing_agent.services.prediction_service import run_prediction
from manufacturing_agent.contracts.context import PredictionResult

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
SAMPLES = HERE / "samples.jsonl"


def _build_prediction(feats: dict):
    """1-2: AI4I 변수 → 규칙 예측 → mode B용 PredictionResult.

    반환 predicted_high = level=='high' 위험만(정확도 ③ 채점용). run_prediction은
    낮은 위험(PWF 등)도 항상 포함하므로, '전부'로 비교하면 정확도가 무의미하게 1이 된다.
    """
    out = run_prediction(feats)
    failure_types = [r.failure_type for r in out["risks"]]            # 검색용(전체)
    predicted_high = [r.failure_type for r in out["risks"] if r.level == "high"]
    cause_features: list[str] = []
    for r in out["risks"]:
        for f in getattr(r, "contributing_features", []) or []:
            if f in feats and f not in cause_features:
                cause_features.append(f)
    pred = PredictionResult(
        status="OK" if out["full"] else "PARTIAL",
        missing_features=out["missing"],
        failure_types=failure_types,
        cause_features=cause_features,
        safety_hints=out["safety_hints"],
        confidence=out["confidence"],
    )
    return pred, predicted_high


def rag_answer(question: str, profile: str = "troubleshooting_rag",
               prediction: PredictionResult | None = None) -> tuple[str, list[str], str]:
    """evidence_agent와 동일한 방식으로 (answer, contexts, status) 생성."""
    res = rag_search(question, profile=profile, prediction=prediction)
    docs = res.get("documents") or []
    contexts = [str(d.get("text") or "") for d in docs]
    status = res.get("status")
    if not docs:
        # NO_EVIDENCE: 추측 금지 안내가 곧 답변(contexts 비어 있음)
        return (res.get("guidance") or "관련 문서 근거를 찾지 못했습니다."), contexts, status
    citation_docs = build_citation_aware_docs(docs, res.get("citations") or [])
    answer = call_llm(
        EVIDENCE_SUMMARY_SYSTEM,
        "질문:" + question + "\n사용 가능한 citation 문서:" + json.dumps(citation_docs, ensure_ascii=False),
    )
    return answer, contexts, status


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGAS 샘플 생성(RAG 실행 결과 캐시)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만 처리(0=전체)")
    ap.add_argument("--profile", default="troubleshooting_rag")
    ap.add_argument("--dataset", default=str(DATASET),
                    help="입력 데이터셋 jsonl (예: evals/ragas/dataset_generated.jsonl)")
    ap.add_argument("--out", default=str(SAMPLES), help="출력 samples jsonl")
    args = ap.parse_args()

    cases = [json.loads(l) for l in open(args.dataset, encoding="utf-8") if l.strip()]
    if args.limit:
        cases = cases[: args.limit]

    from manufacturing_agent.services.rag_service import MIN_EVIDENCE_SCORE
    print(f"MIN_EVIDENCE_SCORE={MIN_EVIDENCE_SCORE} (낮출수록 NO_EVIDENCE↓ / 환경변수로 조정)")

    out = []
    for i, c in enumerate(cases, 1):
        # 행별 profile: dataset가 명시하면 우선, 없으면 CLI 기본값
        profile = c.get("profile") or args.profile
        feats = c.get("input_features")
        prediction, predicted = (None, [])
        if feats:  # 1-2: 예측 먼저 실행 → mode B
            prediction, predicted = _build_prediction(feats)
        print(f"[{i}/{len(cases)}] {c['id']} ({profile}) … 검색+요약", flush=True)
        answer, contexts, status = rag_answer(c["question"], profile=profile, prediction=prediction)
        rec = {
            "id": c["id"],
            "track": c.get("track"),
            "category": c.get("category"),
            "question": c["question"],
            "answer": answer,
            "contexts": contexts,
            "ground_truth": c.get("ground_truth", ""),
            "rag_status": status,
            "n_contexts": len(contexts),
        }
        if feats:  # 예측 정확도(③) 채점용 메타
            rec["expected_failure_types"] = c.get("expected_failure_types", [])
            rec["predicted_failure_types"] = predicted
        out.append(rec)
        print(f"    status={status} contexts={len(contexts)} answer_len={len(answer)}"
              + (f" predicted={predicted}" if feats else ""))

    Path(args.out).write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in out) + "\n", encoding="utf-8"
    )
    print(f"\n저장: {args.out}  ({len(out)}건)")
    empties = [s["id"] for s in out if s["n_contexts"] == 0]
    if empties:
        print(f"⚠ contexts 0건(검색 실패/NO_EVIDENCE): {empties} — RAGAS context 지표에서 제외/NaN될 수 있음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
