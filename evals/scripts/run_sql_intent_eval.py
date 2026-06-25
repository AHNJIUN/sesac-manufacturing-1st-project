"""Planner SQL query intents (detail/aggregate) 평가.

데이터: evals/datasets/intent_eval.jsonl (sql=true & sql_query_intent 라벨 있음)
매칭 : 정답 set과 예측 set의 교집합 (관대한 매칭)
"""
from __future__ import annotations
import json, sys, time
from datetime import datetime
from pathlib import Path
from collections import Counter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
from sklearn.metrics import precision_recall_fscore_support
from manufacturing_agent.graph.planner import _llm_supervisor_planner_decision

DS_PATH = Path("evals/datasets/intent_eval.jsonl")
OUT_DIR = Path("evals/results")

def main():
    rows = [json.loads(l) for l in open(DS_PATH)]
    sql_rows = [r for r in rows if r.get("needs_sql") and r.get("sql_query_intent")]
    print(f"평가 대상: {len(sql_rows)}건")
    print(f"라벨 분포: {Counter(r['sql_query_intent'] for r in sql_rows)}\n")

    results = []
    for i, row in enumerate(sql_rows):
        state = {"user_message": row["msg"], "input_features": row.get("input_features"), "context_packet": None}
        t0 = time.time()
        d = _llm_supervisor_planner_decision(state)
        latency = time.time() - t0
        true_label = row["sql_query_intent"]
        pred_set = set(d.sql_query_intents)
        # 관대한 매칭: 정답 라벨이 예측 set에 포함되면 OK
        correct = true_label in pred_set
        results.append({
            "id": row["id"],
            "msg": row["msg"],
            "true": true_label,
            "pred_set": list(pred_set),
            "confidence": d.confidence,
            "latency": latency,
            "correct": correct,
        })
        print(f"  {i+1}/{len(sql_rows)} [{row['id']:12s}] true={true_label}, pred={pred_set}, ok={correct}")

    n = len(results)
    acc = sum(r["correct"] for r in results) / n
    # per-class
    perclass = {}
    for label in ["detail", "aggregate"]:
        tp = sum(1 for r in results if r["true"] == label and r["correct"])
        fn = sum(1 for r in results if r["true"] == label and not r["correct"])
        fp = sum(1 for r in results if r["true"] != label and label in r["pred_set"])
        perclass[label] = {"tp": tp, "fn": fn, "fp": fp}

    out = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "n": n,
        "accuracy": acc,
        "per_class": perclass,
        "results": results,
    }
    fp = OUT_DIR / f"sql_intent_eval_{out['timestamp']}.json"
    json.dump(out, open(fp, "w"), ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("SQL Query Intents 평가 결과")
    print("=" * 60)
    print(f"Accuracy: {acc:.4f} ({sum(r['correct'] for r in results)}/{n})")
    print()
    for label, st in perclass.items():
        print(f"  {label:10s}: TP={st['tp']} FN={st['fn']} FP={st['fp']}")
    print(f"\n저장: {fp}")

if __name__ == "__main__":
    main()