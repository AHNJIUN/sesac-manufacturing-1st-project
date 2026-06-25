"""Supervisor Planner Intent 분류 정확도 평가.

데이터: evals/datasets/intent_eval.jsonl (임의 작성, 35건)
범위 : 단일 턴, 텍스트 수치 임베딩 없음, context_packet=None

최우선 지표:
  1) Intent accuracy + macro-F1 (5분류)
  2) Per-class precision / recall / F1
  3) Confusion matrix (5x5)
  4) Task 분해 EM + per-label F1 (보조)
  5) Confidence calibration (ECE)
  6) Split별 분석 (clear vs adversarial)

실행:
    uv run --env-file .env python evals/scripts/run_intent_eval.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (직접 실행 대응)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)

from manufacturing_agent.graph.planner import _llm_supervisor_planner_decision


DS_PATH = Path("evals/datasets/intent_eval.jsonl")
OUT_DIR = Path("evals/results")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def call_planner(row):
    """Planner 단독 호출 (intake/context_manager 미사용)."""
    state = {
        "user_message": row["msg"],
        "input_features": row.get("input_features"),
        "context_packet": None,
    }
    t0 = time.time()
    decision = _llm_supervisor_planner_decision(state)
    return decision, time.time() - t0


def main():
    rows = [json.loads(line) for line in open(DS_PATH)]
    print(f"평가 대상 : {len(rows)} 건")
    print(f"Intent 분포: {dict(Counter(r['intent'] for r in rows))}")
    print(f"Split 분포 : {dict(Counter(r['split'] for r in rows))}")
    print()

    # ─────────────────────────────────────────
    # Planner 호출
    # ─────────────────────────────────────────
    results = []
    for i, row in enumerate(rows):
        try:
            decision, latency = call_planner(row)
            results.append({
                "id": row["id"],
                "split": row["split"],
                "msg": row["msg"],
                "true_intent": row["intent"],
                "pred_intent": decision.intent,
                "true_pred": row["needs_prediction"],
                "true_sql": row["needs_sql"],
                "true_evi": row["needs_evidence"],
                "pred_pred": decision.needs_prediction,
                "pred_sql": decision.needs_sql,
                "pred_evi": decision.needs_evidence,
                "confidence": decision.confidence,
                "latency": latency,
                "correct_intent": decision.intent == row["intent"],
            })
            if (i + 1) % 5 == 0:
                print(f"  {i + 1}/{len(rows)} 진행...")
        except Exception as e:
            print(f"  ⚠️  {row['id']} 에러: {type(e).__name__}: {e}")
            results.append({
                "id": row["id"], "split": row["split"],
                "true_intent": row["intent"], "error": str(e),
            })

    valid = [r for r in results if "pred_intent" in r]
    n = len(valid)
    print(f"\n유효 {n}건 / 오류 {len(results) - n}건\n")

    if n == 0:
        print("❌ 유효한 결과가 없습니다. planner 호출을 확인하세요.")
        return

    # ─────────────────────────────────────────
    # 최우선 — Intent 5분류
    # ─────────────────────────────────────────
    y_true = [r["true_intent"] for r in valid]
    y_pred = [r["pred_intent"] for r in valid]

    intent_acc = accuracy_score(y_true, y_pred)
    intent_macro_f1 = f1_score(y_true, y_pred, average="macro", zero_division=0)
    intent_weighted_f1 = f1_score(y_true, y_pred, average="weighted", zero_division=0)
    intent_report = classification_report(y_true, y_pred, zero_division=0, digits=3)

    intent_labels = sorted(set(y_true) | set(y_pred))
    cm = confusion_matrix(y_true, y_pred, labels=intent_labels)

    # ─────────────────────────────────────────
    # 보조 — Task 분해 (needs 3-binary)
    # ─────────────────────────────────────────
    task_em = float(np.mean([
        (r["true_pred"], r["true_sql"], r["true_evi"])
        == (r["pred_pred"], r["pred_sql"], r["pred_evi"])
        for r in valid
    ]))
    f1_p = f1_score([r["true_pred"] for r in valid],
                    [r["pred_pred"] for r in valid], zero_division=0)
    f1_s = f1_score([r["true_sql"] for r in valid],
                    [r["pred_sql"] for r in valid], zero_division=0)
    f1_e = f1_score([r["true_evi"] for r in valid],
                    [r["pred_evi"] for r in valid], zero_division=0)

    # ─────────────────────────────────────────
    # Confidence calibration (ECE)
    # ─────────────────────────────────────────
    bins = np.linspace(0, 1, 11)
    ece = 0.0
    for i in range(10):
        in_bin = [r for r in valid if bins[i] <= r["confidence"] < bins[i + 1]]
        if not in_bin:
            continue
        avg_conf = float(np.mean([r["confidence"] for r in in_bin]))
        acc_bin = float(np.mean([r["correct_intent"] for r in in_bin]))
        ece += len(in_bin) / n * abs(avg_conf - acc_bin)

    # ─────────────────────────────────────────
    # Split별 분석
    # ─────────────────────────────────────────
    split_stats = {}
    for split in ["clear", "adversarial"]:
        srows = [r for r in valid if r["split"] == split]
        if not srows:
            continue
        s_acc = accuracy_score(
            [r["true_intent"] for r in srows],
            [r["pred_intent"] for r in srows],
        )
        s_f1 = f1_score(
            [r["true_intent"] for r in srows],
            [r["pred_intent"] for r in srows],
            average="macro", zero_division=0,
        )
        split_stats[split] = {
            "n": len(srows),
            "intent_acc": float(s_acc),
            "intent_macro_f1": float(s_f1),
        }

    # ─────────────────────────────────────────
    # Latency
    # ─────────────────────────────────────────
    lats = [r["latency"] for r in valid]
    avg_lat = float(np.mean(lats))
    p95_lat = float(np.percentile(lats, 95))

    # ─────────────────────────────────────────
    # 저장
    # ─────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {
        "timestamp": ts,
        "n_total": len(rows),
        "n_valid": n,
        "n_error": len(results) - n,
        # 최우선
        "intent_accuracy": float(intent_acc),
        "intent_macro_f1": float(intent_macro_f1),
        "intent_weighted_f1": float(intent_weighted_f1),
        "intent_classification_report": intent_report,
        "intent_confusion_labels": intent_labels,
        "intent_confusion_matrix": cm.tolist(),
        # 보조
        "task_em": task_em,
        "f1_needs_prediction": float(f1_p),
        "f1_needs_sql": float(f1_s),
        "f1_needs_evidence": float(f1_e),
        # Calibration
        "ece": ece,
        # Latency
        "avg_latency": avg_lat,
        "p95_latency": p95_lat,
        # Split
        "split_stats": split_stats,
        # Raw
        "results": results,
    }
    fp = OUT_DIR / f"intent_eval_{ts}.json"
    json.dump(out, open(fp, "w"), ensure_ascii=False, indent=2)

    # ─────────────────────────────────────────
    # 콘솔 출력
    # ─────────────────────────────────────────
    print("=" * 70)
    print("Supervisor Intent 분류 평가 결과")
    print("=" * 70)
    print()
    print("[🎯 최우선 — Intent 5분류]")
    print(f"  Accuracy         : {intent_acc:.4f}")
    print(f"  Macro-F1         : {intent_macro_f1:.4f}")
    print(f"  Weighted-F1      : {intent_weighted_f1:.4f}")
    print()
    print("[Per-class precision / recall / F1]")
    print(intent_report)
    print()
    print("[Confusion Matrix]  (rows: true, cols: pred)")
    print(f"  labels: {intent_labels}")
    for label, row_cm in zip(intent_labels, cm):
        print(f"  {label:25s} {row_cm.tolist()}")
    print()
    print("[보조 — Task 분해 (needs 3-binary)]")
    print(f"  EM               : {task_em:.4f}")
    print(f"  F1 needs_pred    : {f1_p:.4f}")
    print(f"  F1 needs_sql     : {f1_s:.4f}")
    print(f"  F1 needs_evi     : {f1_e:.4f}")
    print()
    print("[Confidence]")
    print(f"  ECE              : {ece:.4f} (낮을수록 우수)")
    print()
    print("[Latency]")
    print(f"  avg              : {avg_lat:.2f}s")
    print(f"  p95              : {p95_lat:.2f}s")
    print()
    print("[Split별 Intent 정확도]")
    for sp, s in split_stats.items():
        print(f"  {sp:12s} n={s['n']:2d}  "
              f"acc={s['intent_acc']:.3f}  macroF1={s['intent_macro_f1']:.3f}")
    print()
    print(f"저장: {fp}")


if __name__ == "__main__":
    main()
