"""Replanner deterministic decision 평가.

데이터: evals/datasets/replanner_eval.jsonl (12 케이스)
검증 :
  1) action이 expect와 일치 (PATCH_AND_RERUN / ASK_USER / FINALIZE_WITH_WARNINGS / BLOCK)
  2) target_task_ids가 expect와 일치
  3) invalidate_task_ids에 'final_1' 포함 (PATCH_AND_RERUN인 경우)
  4) task_patches의 params_update가 expected key 포함 (PATCH_AND_RERUN인 경우)
  5) Hybrid LLM fallback: deterministic이 FINALIZE_WITH_WARNINGS일 때 LLM 경로 시도

실행:
    uv run --env-file .env python evals/scripts/run_replanner_eval.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from manufacturing_agent.contracts.context import ExecutionPlan, TaskSpec
from manufacturing_agent.graph.replanner import (
    deterministic_replanner_decision,
    hybrid_replanner_decision,
)


DS_PATH = Path("evals/datasets/replanner_eval.jsonl")
OUT_DIR = Path("evals/results")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _build_plan(plan_dict):
    if plan_dict is None:
        return None
    tasks = [TaskSpec(**t) for t in plan_dict["tasks"]]
    return ExecutionPlan(intent=plan_dict["intent"], tasks=tasks)


def _build_state(case):
    """평가용 state — replanner는 evidence_bundle.status와 sql_result.status를 본다."""
    state = {}
    if case.get("evidence_bundle_status"):
        state["evidence_bundle"] = SimpleNamespace(status=case["evidence_bundle_status"])
    if case.get("sql_result_status"):
        state["sql_result"] = SimpleNamespace(status=case["sql_result_status"])
    return state


def _patch_keys(decision):
    """decision.task_patches[*].params_update의 모든 키 집합."""
    keys = set()
    for patch in decision.task_patches:
        keys.update(patch.params_update.keys())
    return keys


def main():
    cases = [json.loads(l) for l in open(DS_PATH)]
    print(f"케이스: {len(cases)}건\n")

    results = []
    for case in cases:
        cid = case["id"]
        expect = case["expect"]
        scenario = case.get("scenario", "")

        plan = _build_plan(case["plan"])
        state = _build_state(case)
        report = case["gate_report"]

        # deterministic vs hybrid 선택
        if case.get("use_hybrid"):
            try:
                decision = hybrid_replanner_decision(state, plan, report)
            except Exception as e:
                results.append({
                    "id": cid, "ok": False, "scenario": scenario,
                    "error": f"{type(e).__name__}: {e}",
                })
                print(f"  [{cid:12s}] ❌ hybrid 예외: {type(e).__name__}: {e}")
                continue
        else:
            decision = deterministic_replanner_decision(state, plan, report)

        # 검증 항목
        actual_action = decision.action
        actual_targets = sorted(decision.target_task_ids)
        actual_invalidate = sorted(decision.invalidate_task_ids)
        actual_patch_keys = _patch_keys(decision)

        # hybrid 케이스: action_in으로 허용 집합 검사
        if "action_in" in expect:
            allowed = expect["action_in"]
            action_ok = actual_action in allowed
        else:
            action_ok = actual_action == expect["action"]

        target_ok = actual_targets == sorted(expect.get("target_task_ids", []))

        if expect.get("action") == "PATCH_AND_RERUN":
            invalidate_ok = "final_1" in actual_invalidate
            patch_ok = all(k in actual_patch_keys for k in expect.get("patch_keys", []))
        else:
            invalidate_ok = True
            patch_ok = True

        ok = action_ok and target_ok and invalidate_ok and patch_ok

        results.append({
            "id": cid,
            "scenario": scenario,
            "ok": ok,
            "checks": {
                "action": action_ok,
                "target_task_ids": target_ok,
                "invalidate_final_1": invalidate_ok,
                "patch_keys": patch_ok,
            },
            "expected": expect,
            "got": {
                "action": actual_action,
                "target_task_ids": actual_targets,
                "invalidate_task_ids": actual_invalidate,
                "patch_keys": sorted(actual_patch_keys),
                "reason_summary": decision.reason_summary,
            },
        })

        mark = "✅" if ok else "❌"
        print(f"  [{cid:12s}] {mark} action={actual_action}, targets={actual_targets}")
        if not ok:
            print(f"    checks: {results[-1]['checks']}")
            print(f"    got patch_keys: {sorted(actual_patch_keys)}")
            if "action" in expect:
                print(f"    expected action: {expect['action']}, "
                      f"expected patch_keys: {expect.get('patch_keys', [])}")

    # ─────────────────────────────────────────
    # 저장
    # ─────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    passed = sum(r["ok"] for r in results)
    n = len(results)
    out = {
        "timestamp": ts,
        "n": n,
        "passed": passed,
        "failed": n - passed,
        "accuracy": passed / n if n else 0.0,
        "results": results,
    }
    fp = OUT_DIR / f"replanner_eval_{ts}.json"
    json.dump(out, open(fp, "w"), ensure_ascii=False, indent=2)

    # ─────────────────────────────────────────
    # 요약
    # ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Replanner 평가 결과")
    print(f"{'='*60}")
    print(f"정확도 : {passed}/{n} = {passed/n:.4f}")
    print(f"저장   : {fp}")

    if n - passed > 0:
        print(f"\n실패 케이스:")
        for r in results:
            if not r["ok"]:
                msg = r.get("error") or f"checks={r['checks']}"
                print(f"  [{r['id']}] {msg}")


if __name__ == "__main__":
    main()
