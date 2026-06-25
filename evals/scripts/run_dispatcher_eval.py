"""Dispatcher 결정 정확도 + idempotency 평가.

데이터: evals/datasets/dispatcher_eval.jsonl (12 케이스)
검증 :
  1) action / next_node가 expect와 일치
  2) dispatched_task_ids가 expect와 일치
  3) Idempotency: 같은 입력 5회 → 같은 출력
  4) Plan 없음 → ValueError

실행:
    uv run --env-file .env python evals/scripts/run_dispatcher_eval.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from manufacturing_agent.contracts.context import ExecutionPlan, TaskSpec
from manufacturing_agent.graph import dispatcher as dispatcher_mod
from manufacturing_agent.graph.dispatcher import orchestrator_dispatcher


DS_PATH = Path("evals/datasets/dispatcher_eval.jsonl")
OUT_DIR = Path("evals/results")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _build_plan(plan_dict):
    """JSON dict → ExecutionPlan. None이면 None 반환."""
    if plan_dict is None:
        return None
    tasks = [TaskSpec(**t) for t in plan_dict["tasks"]]
    return ExecutionPlan(intent=plan_dict["intent"], tasks=tasks)


def _build_state(case):
    """JSON case → state dict (dispatcher 입력)."""
    return {
        "execution_plan": _build_plan(case.get("plan")),
        "gate_reports": case.get("gate_reports", []),
        "active_task_ids": case.get("active_task_ids", []),
        "consumed_replan_report_indices": case.get("consumed_replan_report_indices", []),
    }


def _set_parallel(parallel: bool):
    """dispatcher 모듈 안의 PARALLEL_DISPATCH_ENABLED를 직접 패치.
    (config.py의 값은 import 시점에 캡처되므로 env var만 바꾸면 효과 없음)
    """
    dispatcher_mod.PARALLEL_DISPATCH_ENABLED = parallel


def main():
    cases = [json.loads(l) for l in open(DS_PATH)]
    print(f"케이스: {len(cases)}건\n")

    # ─────────────────────────────────────────
    # 1. Action / routing 정확도 (12 케이스)
    # ─────────────────────────────────────────
    results = []
    for case in cases:
        cid = case["id"]
        expect = case["expect"]
        _set_parallel(case.get("parallel", False))

        # 에러 케이스 (disp_012)
        if expect.get("error"):
            expected_type = expect.get("error_type", "Exception")
            try:
                state = _build_state(case)
                orchestrator_dispatcher(state)
                results.append({"id": cid, "ok": False, "msg": "에러 미발생"})
                print(f"  [{cid:10s}] ❌ 에러 미발생")
            except Exception as e:
                if type(e).__name__ == expected_type:
                    results.append({"id": cid, "ok": True, "msg": f"예상 에러: {expected_type}"})
                    print(f"  [{cid:10s}] ✅ {expected_type} 발생")
                else:
                    results.append({
                        "id": cid, "ok": False,
                        "msg": f"다른 에러: {type(e).__name__}(예상: {expected_type})",
                    })
                    print(f"  [{cid:10s}] ❌ {type(e).__name__} (예상: {expected_type})")
            continue

        # 정상 케이스
        try:
            state = _build_state(case)
            out = orchestrator_dispatcher(state)
            d = out["orchestrator_decision"]

            expected_action = expect["action"]
            expected_next = expect["next_node"]
            expected_ids = sorted(expect.get("dispatched_task_ids", []))
            actual_ids = sorted(d.dispatched_task_ids or [])

            ok = (
                d.action == expected_action
                and d.next_node == expected_next
                and actual_ids == expected_ids
            )
            results.append({
                "id": cid,
                "scenario": case.get("scenario", ""),
                "ok": ok,
                "expected": {
                    "action": expected_action, "next_node": expected_next,
                    "dispatched_task_ids": expected_ids,
                },
                "got": {
                    "action": d.action, "next_node": d.next_node,
                    "dispatched_task_ids": actual_ids,
                },
            })
            mark = "✅" if ok else "❌"
            print(f"  [{cid:10s}] {mark} action={d.action}, next={d.next_node}, ids={actual_ids}")
            if not ok:
                print(f"               expected: action={expected_action}, "
                      f"next={expected_next}, ids={expected_ids}")
        except Exception as e:
            results.append({
                "id": cid, "ok": False,
                "error": f"{type(e).__name__}: {e}",
            })
            print(f"  [{cid:10s}] ❌ 예외 {type(e).__name__}: {e}")

    # ─────────────────────────────────────────
    # 2. Idempotency (disp_001 5회 반복)
    # ─────────────────────────────────────────
    print("\n[Idempotency 테스트] 같은 입력 5회 반복 (disp_001)")
    normal_case = next(c for c in cases if c["id"] == "disp_001")
    _set_parallel(normal_case.get("parallel", False))

    first = None
    idempotent = True
    differences = []
    for i in range(5):
        # 매번 fresh state로 호출 (이전 호출이 plan을 변형하지 않도록)
        state = _build_state(normal_case)
        out = orchestrator_dispatcher(state)
        d = out["orchestrator_decision"]
        curr = (d.action, d.next_node, tuple(sorted(d.dispatched_task_ids or [])))
        if first is None:
            first = curr
            print(f"  반복 1: {curr}")
        elif curr != first:
            idempotent = False
            differences.append({"iter": i + 1, "result": list(curr)})
            print(f"  반복 {i+1}: ❌ 불일치 {curr}")
        else:
            print(f"  반복 {i+1}: ✅ 일치")

    # ─────────────────────────────────────────
    # 저장
    # ─────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    passed = sum(r["ok"] for r in results)
    n = len(results)
    out_data = {
        "timestamp": ts,
        "n": n,
        "passed": passed,
        "failed": n - passed,
        "accuracy": passed / n if n else 0.0,
        "idempotent": idempotent,
        "idempotency_differences": differences,
        "results": results,
    }
    fp = OUT_DIR / f"dispatcher_eval_{ts}.json"
    json.dump(out_data, open(fp, "w"), ensure_ascii=False, indent=2)

    # ─────────────────────────────────────────
    # 요약
    # ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Dispatcher 평가 결과")
    print(f"{'='*60}")
    print(f"Action / routing 정확도 : {passed}/{n} = {passed/n:.4f}")
    print(f"Idempotency             : {'✅ 통과' if idempotent else '❌ 실패'}")
    print(f"저장                    : {fp}")

    if n - passed > 0:
        print(f"\n실패 케이스:")
        for r in results:
            if not r["ok"]:
                msg = r.get("msg") or r.get("error", "")
                print(f"  [{r['id']}] {msg}")
                if "expected" in r:
                    print(f"    expected: {r['expected']}")
                    print(f"    got     : {r['got']}")


if __name__ == "__main__":
    main()
