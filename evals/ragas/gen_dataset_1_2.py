"""1-2(예측 기반 근거 검색) 평가셋 생성.

TestsetGenerator는 문서에서 '변수+예측' 질의를 못 만들기 때문에, 1-2는 시나리오 질문을
고정하고 다음을 채운다:
  - input_features:        예측을 띄울 AI4I 변수 (OSF용 high-risk / HDF용)
  - expected_failure_types: 채점용 정답 failure_type (예측 정확도 ③)
  - ground_truth:          (예측 규칙 + 검색 근거)로 LLM이 작성한 모범답안 초안(사람 검수 필요)

run_prediction(feats)로 실제 규칙 예측을 돌려 predicted_failure_types도 기록한다.

실행:
    PYTHONUTF8=1 PYTHONPATH=. python evals/ragas/gen_dataset_1_2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from manufacturing_agent.services.prediction_service import run_prediction
from manufacturing_agent.services.rag_service import rag_search
from manufacturing_agent.contracts.context import PredictionResult
from manufacturing_agent.config import call_llm

HERE = Path(__file__).resolve().parent
OUT = HERE / "dataset_1_2.jsonl"

# OSF(과부하) 고위험: tool_wear×torque = 215×62 = 13,330 ≥ M타입 12,000 → OSF high (+TWF, +HDF medium)
FEATURES_OSF = {"type": "M", "air_temperature": 298.0, "process_temperature": 309.0,
                "rotational_speed": 1320.0, "torque": 62.0, "tool_wear": 215.0}
# HDF(냉각저하) 고위험: 온도차 5K(<8.6) & rpm 1300(<1380) → HDF high, OSF/TWF는 낮게
FEATURES_HDF = {"type": "M", "air_temperature": 300.0, "process_temperature": 305.0,
                "rotational_speed": 1300.0, "torque": 40.0, "tool_wear": 100.0}

SCENARIOS = [
    # A. 예측 결과 설명 (OSF)
    ("k12_01", "pred_explain", "이런 상황에서 관련 정비 기준 있어?", FEATURES_OSF, ["OSF"]),
    # B. 대응 방법 (HDF)
    ("k12_02", "pred_response", "지금 가장 먼저 해야 할 점검은?", FEATURES_HDF, ["HDF"]),
    ("k12_03", "pred_response", "작업 계속해도 돼?", FEATURES_HDF, ["HDF"]),
    ("k12_04", "pred_response", "어떤 부품부터 확인해야 해?", FEATURES_HDF, ["HDF"]),
    ("k12_05", "pred_response", "점검 순서를 알려줘.", FEATURES_HDF, ["HDF"]),
    ("k12_06", "pred_response", "작업을 중단해야 하는 상황이야?", FEATURES_HDF, ["HDF"]),
    # C. 변수 중심 (OSF)
    ("k12_07", "pred_variable", "토크가 높으면 왜 OSF가 발생하지?", FEATURES_OSF, ["OSF"]),
    ("k12_08", "pred_variable", "토크를 낮추면 위험이 줄어들까?", FEATURES_OSF, ["OSF"]),
]

GT_SYSTEM = (
    "너는 제조 설비 정비 전문가다. 아래 '규칙 기반 예측 진단'과 '문서 근거'만 사용해 "
    "사용자 질문에 대한 모범 답안을 한국어 3~5문장으로 작성한다. "
    "문서 근거에 없는 절차/수치는 지어내지 말고, 진단이 가리키는 failure_type과 원인 변수를 답에 반영한다."
)


def predict(feats: dict):
    out = run_prediction(feats)
    failure_types = [r.failure_type for r in out["risks"]]
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
    rule_lines = [f"- {r.failure_type}({r.level}): {r.detail}" for r in out["risks"]]
    return pred, failure_types, "\n".join(rule_lines)


def main() -> int:
    rows = []
    for sid, category, question, feats, expected in SCENARIOS:
        pred, predicted, rule_text = predict(feats)
        res = rag_search(question, profile="prediction_plus_rag", prediction=pred)
        docs = res.get("documents") or []
        contexts = [str(d.get("text") or "") for d in docs]
        print(f"[{sid}] predicted={predicted} expected={expected} contexts={len(contexts)}", flush=True)

        gt_user = (
            f"질문: {question}\n\n[규칙 기반 예측 진단]\n{rule_text}\n\n"
            f"[문서 근거]\n" + ("\n---\n".join(contexts[:5]) if contexts else "(검색된 문서 근거 없음)")
        )
        ground_truth = call_llm(GT_SYSTEM, gt_user).strip()

        rows.append({
            "id": sid,
            "track": "1-2",
            "category": category,
            "question": question,
            "ground_truth": ground_truth,
            "profile": "prediction_plus_rag",
            "input_features": feats,
            "expected_failure_types": expected,
            "predicted_failure_types": predicted,
            "expect_evidence": True,
        })

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"\n저장: {OUT} ({len(rows)}개)")
    for r in rows:
        ok = "✓" if set(r["expected_failure_types"]) <= set(r["predicted_failure_types"]) else "✗"
        print(f"  {ok} [{r['category']}] {r['question']}  (pred={r['predicted_failure_types']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
