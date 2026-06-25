import json, glob
from collections import defaultdict

latest = sorted(glob.glob("evals/results/intent_eval_*.json"))[-1]
data = json.load(open(latest))
results = [r for r in data["results"] if "pred_intent" in r]

errors = [r for r in results if not r["correct_intent"]]
print(f"=== Intent 오분류 ({len(errors)}/{len(results)} = {len(errors)/len(results):.1%}) ===\n")

# 1) 오분류 pair 빈도
pairs = defaultdict(list)
for e in errors:
    pairs[(e["true_intent"], e["pred_intent"])].append(e)
print("[오분류 패턴 빈도]")
for pair, items in sorted(pairs.items(), key=lambda x: -len(x[1])):
    print(f"  {pair[0]:25s} → {pair[1]:25s}  ({len(items)} 건)")
    for r in items:
        print(f"    [{r['id']:12s}] conf={r['confidence']:.2f}: {r['msg']}")
print()

# 2) Confidence 분포
import numpy as np
errs_conf = [e["confidence"] for e in errors]
if errs_conf:
    print("[오분류 confidence 분포]")
    print(f"  평균 conf : {np.mean(errs_conf):.3f}")
    print(f"  > 0.8 (자신 있게 틀림): {sum(1 for c in errs_conf if c > 0.8)} 건")
    print(f"  < 0.5 (모르겠다 표시): {sum(1 for c in errs_conf if c < 0.5)} 건")
print()

# 3) Split별
print("[Split별 정확도]")
for split in ["clear", "adversarial"]:
    srows = [r for r in results if r["split"] == split]
    if not srows: continue
    correct = sum(r["correct_intent"] for r in srows)
    print(f"  {split:12s} n={len(srows):2d}  acc={correct/len(srows):.3f}  ({correct}/{len(srows)})")
print()

# 4) Per-intent precision/recall
print("[Per-intent 정확도]")
for intent in ["prediction_diagnosis", "document_qa", "history_lookup", "combined_analysis", "general_manufacturing"]:
    true_rows = [r for r in results if r["true_intent"] == intent]
    pred_rows = [r for r in results if r["pred_intent"] == intent]
    tp = sum(1 for r in true_rows if r["pred_intent"] == intent)
    fn = len(true_rows) - tp
    fp = len(pred_rows) - tp
    print(f"  {intent:25s}: TP={tp} FN={fn} FP={fp}")