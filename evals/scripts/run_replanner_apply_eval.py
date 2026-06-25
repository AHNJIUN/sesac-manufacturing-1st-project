"""apply_replanner_decision 무결성 회귀 테스트.

deterministic_replanner_decision으로 만든 결정을 plan에 적용한 뒤,
plan 상태가 의도한 대로 변했는지 검증.

검증 항목:
  1) PATCH_AND_RERUN:
     - target task의 status가 PENDING
     - target task의 rerun_count 증가 (+1)
     - target task의 retry_count 리셋 (0)
     - target task의 params_update 적용됨
     - 'final_1' task가 PENDING으로 invalidate
     - plan.plan_revision 증가
     - feedback_history 누적
  2) ASK_USER:
     - target task의 status가 NEEDS_USER_INPUT
  3) FINALIZE_WITH_WARNINGS:
     - target task의 status가 PASS_WITH_WARNINGS
  4) plan.replan_count 증가
  5) plan.replan_history에 한 줄 추가

실행:
    uv run --env-file .env python evals/scripts/run_replanner_apply_eval.py
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
from manufacturing_agent.graph.plan_ops import PlanOps
from manufacturing_agent.graph.replanner import (
    apply_replanner_decision,
    deterministic_replanner_decision,
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
    state = {}
    if case.get("evidence_bundle_status"):
        state["evidence_bundle"] = SimpleNamespace(status=case["evidence_bundle_status"])
    if case.get("sql_result_status"):
        state["sql_result"] = SimpleNamespace(status=case["sql_result_status"])
    return state


def _verify_patch_and_rerun(plan_before, plan_after, decision, expect_patch_keys):
    """PATCH_AND_RERUN 후 plan 상태 검증."""
    checks = {}
    for target_id in decision.target_task_ids:
        target_before = PlanOps.task_by_id(plan_before, target_id)
        target_after = PlanOps.task_by_id(plan_after, target_id)

        # 1) status == PENDING
        checks[f"{target_id}_status_PENDING"] = target_after.status == "PENDING"
        # 2) rerun_count 증가
        checks[f"{target_id}_rerun_count_inc"] = (
            target_after.rerun_count == target_before.rerun_count + 1
        )
        # 3) retry_count 리셋
        checks[f"{target_id}_retry_count_reset"] = target_after.retry_count == 0
        # 4) params_update 적용 (expected key가 params에 모두 포함)
        params_after = target_after.params or {}
        checks[f"{target_id}_params_keys"] = all(k in params_after for k in expect_patch_keys)
        # 5) feedback_history 길어짐 (또는 동일 — patch.reason이 None이면 안 늘 수도)
        checks[f"{target_id}_feedback_history_grew"] = (
            len(target_after.feedback_history) >= len(target_before.feedback_history)
        )

    # 6) final_1 invalidate (PENDING으로)
    final_after = PlanOps.task_by_id(plan_after, "final_1")
    if final_after:
        checks["final_1_PENDING"] = final_after.status == "PENDING"

    # 7) plan_revision 증가
    checks["plan_revision_inc"] = plan_after.plan_revision == plan_before.plan_revision + 1

    # 8) replan_count 증가
    checks["replan_count_inc"] = plan_after.replan_count == plan_before.replan_count + 1

    # 9) replan_history에 항목 추가
    checks["replan_history_appended"] = (
        len(plan_after.replan_history) == len(plan_before.replan_history) + 1
    )

    return checks


def _verify_ask_user(plan_before, plan_after, decision):
    """ASK_USER 후 plan 상태 검증."""
    checks = {}
    for target_id in decision.target_task_ids:
        target_after = PlanOps.task_by_id(plan_after, target_id)
        checks[f"{target_id}_status_NEEDS_USER_INPUT"] = (
            target_after.status == "NEEDS_USER_INPUT"
        )
    checks["plan_revision_inc"] = plan_after.plan_revision == plan_before.plan_revision + 1
    return checks


def _verify_finalize_warnings(plan_before, plan_after, decision):
    """FINALIZE_WITH_WARNINGS 후 plan 상태 검증."""
    checks = {}
    for target_id in decision.target_task_ids:
        target_after = PlanOps.task_by_id(plan_after, target_id)
        checks[f"{target_id}_status_PASS_WITH_WARNINGS"] = (
            target_after.status == "PASS_WITH_WARNINGS"
        )
    checks["plan_revision_inc"] = plan_after.plan_revision == plan_before.plan_revision + 1
    return checks


def main():
    cases = [json.loads(l) for l in open(DS_PATH)]
    # hybrid 케이스는 LLM 호출 비결정적이므로 apply 검증에서 제외
    cases = [c for c in cases if not c.get("use_hybrid")]
    print(f"케이스: {len(cases)}건 (hybrid 제외)\n")

    results = []
    for case in cases:
        cid = case["id"]
        expect = case["expect"]
        scenario = case.get("scenario", "")

        plan = _build_plan(case["plan"])
        state = _build_state(case)
        report = case["gate_report"]

        # 결정 도출 (deterministic)
        decision = deterministic_replanner_decision(state, plan, report)

        # 결정 action이 기대와 다르면 apply 검증 의미 없음 — decision 정확도는 별도 스크립트
        if "action" in expect and decision.action != expect["action"]:
            results.append({
                "id": cid, "scenario": scenario, "ok": False,
                "msg": f"decision action 불일치 ({decision.action} vs {expect['action']})",
            })
            print(f"  [{cid:12s}] ⚠️  decision 불일치 — apply 검증 스킵 ({decision.action})")
            continue

        # plan 적용
        try:
            plan_after = apply_replanner_decision(plan, decision, report)
        except Exception as e:
            results.append({
                "id": cid, "scenario": scenario, "ok": False,
                "error": f"{type(e).__name__}: {e}",
            })
            print(f"  [{cid:12s}] ❌ apply 예외: {type(e).__name__}: {e}")
            continue

        # action별 검증
        if decision.action == "PATCH_AND_RERUN":
            checks = _verify_patch_and_rerun(
                plan, plan_after, decision, expect.get("patch_keys", []),
            )
        elif decision.action == "ASK_USER":
            checks = _verify_ask_user(plan, plan_after, decision)
        elif decision.action == "FINALIZE_WITH_WARNINGS":
            checks = _verify_finalize_warnings(plan, plan_after, decision)
        else:
            checks = {"unknown_action": False}

        ok = all(checks.values())
        results.append({
            "id": cid,
            "scenario": scenario,
            "ok": ok,
            "decision_action": decision.action,
            "checks": checks,
        })

        mark = "✅" if ok else "❌"
        print(f"  [{cid:12s}] {mark} action={decision.action}")
        if not ok:
            failed_checks = [k for k, v in checks.items() if not v]
            print(f"    실패: {failed_checks}")

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
    fp = OUT_DIR / f"replanner_apply_eval_{ts}.json"
    json.dump(out, open(fp, "w"), ensure_ascii=False, indent=2)

    # ─────────────────────────────────────────
    # 요약
    # ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Replanner Apply 무결성 평가 결과")
    print(f"{'='*60}")
    print(f"정확도 : {passed}/{n} = {passed/n:.4f}")
    print(f"저장   : {fp}")

    if n - passed > 0:
        print(f"\n실패 케이스:")
        for r in results:
            if not r["ok"]:
                msg = r.get("error") or r.get("msg") or "checks 실패"
                print(f"  [{r['id']}] {msg}")
                if "checks" in r:
                    failed = [k for k, v in r["checks"].items() if not v]
                    print(f"    실패 체크: {failed}")


if __name__ == "__main__":
    main()
