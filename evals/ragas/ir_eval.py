"""검색(retrieval) IR 지표: Recall@K / Precision@K / MRR — 데이터(질문)마다 계산.

RAGAS(LLM-judge)와 별개로, 랭커가 내놓는 ranked 후보를 gold passage와 매칭해
전통 IR 지표를 낸다. NO_EVIDENCE 임계값(cut) 이전의 raw ranked top-K를 평가한다.

gold passage 정의(데이터마다):
  - 1-1: dataset의 reference_contexts (TestsetGenerator가 질문을 만든 실제 출처 청크 = GOLD)
  - 1-2: ground_truth 1개 (라벨된 출처 청크가 없어 GT를 단일 pseudo-passage로 사용 = SILVER proxy)

관련성 판정: cosine(emb(retrieved_chunk), emb(gold)) >= tau.
  - ref(영-영, 거의 원문) tau_ref=0.75
  - gt (한-영 교차언어)   tau_gt =0.55
지표:
  - Precision@K = (top-K 중 관련 청크 수) / K
  - Recall@K    = (top-K가 커버한 gold passage 수) / (gold passage 총수)
  - MRR         = 1 / (첫 관련 청크의 순위)

실행:
    PYTHONUTF8=1 PYTHONPATH=. uv run python evals/ragas/ir_eval.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
KS = [1, 3, 5, 10]


def _load_env() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    env = ROOT / ".env"
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


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _build_prediction(feats: dict):
    from manufacturing_agent.services.prediction_service import run_prediction
    from manufacturing_agent.contracts.context import PredictionResult
    out = run_prediction(feats)
    failure_types = [r.failure_type for r in out["risks"]]
    cause_features: list[str] = []
    for r in out["risks"]:
        for f in getattr(r, "contributing_features", []) or []:
            if f in feats and f not in cause_features:
                cause_features.append(f)
    return PredictionResult(
        status="OK" if out["full"] else "PARTIAL",
        missing_features=out["missing"], failure_types=failure_types,
        cause_features=cause_features, safety_hints=out["safety_hints"],
        confidence=out["confidence"],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="검색 IR 지표(Recall@K/Precision@K/MRR)")
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--embed-model", default=os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"))
    ap.add_argument("--tau-ref", type=float, default=0.75)
    ap.add_argument("--tau-gt", type=float, default=0.55)
    ap.add_argument("--depth", type=int, default=10, help="평가할 ranked top-N 깊이")
    ap.add_argument("--out", default=str(HERE / "ir_scores.csv"))
    args = ap.parse_args()
    _load_env()

    from manufacturing_agent.services.rag_service import build_query, retrieve_stage, rank_evidence
    from langchain_openai import OpenAIEmbeddings
    emb = OpenAIEmbeddings(model=args.embed_model)

    cases = [json.loads(l) for l in open(args.dataset, encoding="utf-8") if l.strip()]
    rows = []
    for i, c in enumerate(cases, 1):
        profile = c.get("profile") or "troubleshooting_rag"
        pred = _build_prediction(c["input_features"]) if c.get("input_features") else None
        plan = build_query(c["question"], profile, pred)
        ranked = rank_evidence(retrieve_stage(plan, k=16, top_k=args.depth), top_k=args.depth)
        chunks = [str(d.get("text") or "") for d in ranked]

        golds = c.get("reference_contexts") or [c.get("ground_truth", "")]
        golds = [g for g in golds if g and g.strip()]
        gold_type = "ref" if c.get("reference_contexts") else "gt"
        tau = args.tau_ref if gold_type == "ref" else args.tau_gt

        if not chunks or not golds:
            print(f"[{i}/{len(cases)}] {c['id']} 검색 0건/ gold 없음 → skip")
            continue

        vecs = emb.embed_documents(chunks + golds)
        cvecs, gvecs = vecs[:len(chunks)], vecs[len(chunks):]
        sim = [[_cos(cv, gv) for gv in gvecs] for cv in cvecs]   # chunk×gold
        rel = [max(row) >= tau for row in sim]                   # 청크별 관련 여부

        rec = {"id": c["id"], "track": c.get("track"), "category": c.get("category"),
               "gold_type": gold_type, "n_gold": len(golds), "n_ranked": len(chunks)}
        for K in KS:
            topk = min(K, len(chunks))
            prec = sum(rel[:topk]) / K
            covered = sum(1 for j in range(len(golds))
                          if any(sim[ci][j] >= tau for ci in range(topk)))
            recall = covered / len(golds)
            rec[f"P@{K}"] = round(prec, 3)
            rec[f"R@{K}"] = round(recall, 3)
        first = next((idx for idx, r in enumerate(rel) if r), None)
        rec["MRR"] = round(1.0 / (first + 1), 3) if first is not None else 0.0
        rows.append(rec)
        print(f"[{i}/{len(cases)}] {c['id']:<8} {gold_type} "
              f"P@5={rec['P@5']} R@5={rec['R@5']} MRR={rec['MRR']}", flush=True)

    # CSV 저장
    import csv
    cols = ["id", "track", "category", "gold_type", "n_gold", "n_ranked"] \
        + [f"P@{K}" for K in KS] + [f"R@{K}" for K in KS] + ["MRR"]
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

    def avg(sel, key):
        xs = [r[key] for r in rows if sel(r)]
        return round(sum(xs) / len(xs), 3) if xs else float("nan")

    print("\n=== IR 지표 평균 (트랙별) ===")
    for label, sel in [("1-1 (gold=ref)", lambda r: r["track"] == "1-1"),
                       ("1-2 (silver=gt)", lambda r: r["track"] == "1-2"),
                       ("전체", lambda r: True)]:
        n = len([r for r in rows if sel(r)])
        if not n:
            continue
        line = " ".join(f"{k}={avg(sel, k)}" for k in
                        [f"P@{K}" for K in KS] + [f"R@{K}" for K in KS] + ["MRR"])
        print(f"  {label:<16} (n={n})  {line}")
    print(f"\nCSV 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
