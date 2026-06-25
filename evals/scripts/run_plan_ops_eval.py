"""PlanOps 상태 전이 매트릭스 회귀 테스트.

검증:
  1) apply_gate_report — gate status × task status 조합 6 케이스
  2) next_runnable_batch — deps 종결, limit 적용, final_answer 제외, 같은 type 중복 방지 4 케이스
  3) mark_running_batch — 정상 + idempotency 2 케이스
  4) reset_orphan_running — orphan 회수 vs 보존 2 케이스

PlanOps는 pure 함수이므로 결정적. LLM 호출 없음.

실행:
    uv run --env-file .env python evals/scripts/run_plan_ops_eval.py
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
from manufacturing_agent.graph.plan_ops import PlanOps


OUT_DIR = Path("evals/results")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _plan(*tasks: TaskSpec) -> ExecutionPlan:
    return ExecutionPlan(intent="combined_analysis", tasks=list(tasks))


def _t(tid, ttype, status="PENDING", deps=None, retry=0, max_retry=2, rerun=0):
    return TaskSpec(
        task_id=tid, task_type=ttype, status=status,
        depends_on=deps or [],
        retry_count=retry, max_retries=max_retry, rerun_count=rerun,
    )


# ─────────────────────────────────────────
# 1. apply_gate_report (6 케이스)
# ─────────────────────────────────────────
def test_apply_gate_report():
    """gate report status → task status 매트릭스 검증."""
    cases = []

    # 1-1. PASS → PASS
    p = _plan(_t("sql_1", "sql", status="RUNNING"))
    r = {"gate_name": "sql_gate", "status": "PASS", "task_id": "sql_1"}
    out = PlanOps.apply_gate_report(p, r)
    cases.append({
        "id": "apply_001_PASS",
        "expected": "PASS",
        "got": out.tasks[0].status,
        "ok": out.tasks[0].status == "PASS",
    })

    # 1-2. PASS_WITH_WARNINGS → PASS_WITH_WARNINGS
    p = _plan(_t("sql_1", "sql", status="RUNNING"))
    r = {"gate_name": "sql_gate", "status": "PASS_WITH_WARNINGS", "task_id": "sql_1"}
    out = PlanOps.apply_gate_report(p, r)
    cases.append({
        "id": "apply_002_PASS_WITH_WARNINGS",
        "expected": "PASS_WITH_WARNINGS",
        "got": out.tasks[0].status,
        "ok": out.tasks[0].status == "PASS_WITH_WARNINGS",
    })

    # 1-3. NEEDS_USER_INPUT → NEEDS_USER_INPUT
    p = _plan(_t("prediction_1", "prediction", status="RUNNING"))
    r = {"gate_name": "prediction_gate", "status": "NEEDS_USER_INPUT", "task_id": "prediction_1"}
    out = PlanOps.apply_gate_report(p, r)
    cases.append({
        "id": "apply_003_NEEDS_USER_INPUT",
        "expected": "NEEDS_USER_INPUT",
        "got": out.tasks[0].status,
        "ok": out.tasks[0].status == "NEEDS_USER_INPUT",
    })

    # 1-4. NON_RETRYABLE_FAIL → FAIL
    p = _plan(_t("evidence_1", "evidence", status="RUNNING"))
    r = {"gate_name": "evidence_gate", "status": "NON_RETRYABLE_FAIL", "task_id": "evidence_1"}
    out = PlanOps.apply_gate_report(p, r)
    cases.append({
        "id": "apply_004_NON_RETRYABLE_FAIL",
        "expected": "FAIL",
        "got": out.tasks[0].status,
        "ok": out.tasks[0].status == "FAIL",
    })

    # 1-5. RETRYABLE_FAIL with budget → PENDING + retry_count+1
    p = _plan(_t("sql_1", "sql", status="RUNNING", retry=0, max_retry=2))
    r = {"gate_name": "sql_gate", "status": "RETRYABLE_FAIL", "task_id": "sql_1"}
    out = PlanOps.apply_gate_report(p, r)
    cases.append({
        "id": "apply_005_RETRYABLE_FAIL_budget",
        "expected": "PENDING + retry_count=1",
        "got": f"{out.tasks[0].status} + retry_count={out.tasks[0].retry_count}",
        "ok": out.tasks[0].status == "PENDING" and out.tasks[0].retry_count == 1,
    })

    # 1-6. RETRYABLE_FAIL exhausted → FAIL
    p = _plan(_t("sql_1", "sql", status="RUNNING", retry=2, max_retry=2))
    r = {"gate_name": "sql_gate", "status": "RETRYABLE_FAIL", "task_id": "sql_1"}
    out = PlanOps.apply_gate_report(p, r)
    cases.append({
        "id": "apply_006_RETRYABLE_FAIL_exhausted",
        "expected": "FAIL (예산 소진)",
        "got": out.tasks[0].status,
        "ok": out.tasks[0].status == "FAIL",
    })

    return cases


# ─────────────────────────────────────────
# 2. next_runnable_batch (4 케이스)
# ─────────────────────────────────────────
def test_next_runnable_batch():
    cases = []

    # 2-1. 3-worker 전체 (deps 종결, 모두 PENDING)
    p = _plan(
        _t("prediction_1", "prediction"),
        _t("sql_1", "sql"),
        _t("evidence_1", "evidence"),
        _t("final_1", "final_answer", deps=["prediction_1", "sql_1", "evidence_1"]),
    )
    batch = PlanOps.next_runnable_batch(p)
    ids = [t.task_id for t in batch]
    cases.append({
        "id": "batch_001_3worker_all",
        "expected": ["prediction_1", "sql_1", "evidence_1"],
        "got": ids,
        "ok": ids == ["prediction_1", "sql_1", "evidence_1"],
    })

    # 2-2. final_answer 제외 (deps 미충족이라도 worker만 픽업)
    p = _plan(
        _t("prediction_1", "prediction"),
        _t("final_1", "final_answer", deps=["prediction_1"]),
    )
    batch = PlanOps.next_runnable_batch(p)
    ids = [t.task_id for t in batch]
    cases.append({
        "id": "batch_002_exclude_final",
        "expected": ["prediction_1"],
        "got": ids,
        "ok": ids == ["prediction_1"],
    })

    # 2-3. limit 적용
    p = _plan(
        _t("prediction_1", "prediction"),
        _t("sql_1", "sql"),
        _t("evidence_1", "evidence"),
    )
    batch = PlanOps.next_runnable_batch(p, limit=2)
    cases.append({
        "id": "batch_003_limit_2",
        "expected": "len=2",
        "got": f"len={len(batch)}",
        "ok": len(batch) == 2,
    })

    # 2-4. 모든 worker 완료 → final_1 단독 후보
    p = _plan(
        _t("prediction_1", "prediction", status="PASS"),
        _t("final_1", "final_answer", deps=["prediction_1"]),
    )
    batch = PlanOps.next_runnable_batch(p)
    ids = [t.task_id for t in batch]
    cases.append({
        "id": "batch_004_final_only",
        "expected": ["final_1"],
        "got": ids,
        "ok": ids == ["final_1"],
    })

    return cases


# ─────────────────────────────────────────
# 3. mark_running_batch (2 케이스)
# ─────────────────────────────────────────
def test_mark_running_batch():
    cases = []

    # 3-1. 정상 — 다중 task RUNNING으로 전이
    p = _plan(
        _t("prediction_1", "prediction"),
        _t("sql_1", "sql"),
    )
    out = PlanOps.mark_running_batch(p, ["prediction_1", "sql_1"])
    statuses = [t.status for t in out.tasks]
    cases.append({
        "id": "mark_001_multi",
        "expected": ["RUNNING", "RUNNING"],
        "got": statuses,
        "ok": statuses == ["RUNNING", "RUNNING"],
    })

    # 3-2. Idempotency — 같은 호출 두 번
    out1 = PlanOps.mark_running_batch(p, ["prediction_1", "sql_1"])
    out2 = PlanOps.mark_running_batch(out1, ["prediction_1", "sql_1"])
    s1 = [t.status for t in out1.tasks]
    s2 = [t.status for t in out2.tasks]
    cases.append({
        "id": "mark_002_idempotent",
        "expected": "두 번 호출 동일",
        "got": f"out1={s1}, out2={s2}",
        "ok": s1 == s2 == ["RUNNING", "RUNNING"],
    })

    return cases


# ─────────────────────────────────────────
# 4. reset_orphan_running (2 케이스)
# ─────────────────────────────────────────
def test_reset_orphan_running():
    cases = []

    # 4-1. Orphan 있음 — active_task_id=None일 때 RUNNING task가 PENDING으로 복구
    p = _plan(
        _t("prediction_1", "prediction", status="RUNNING"),
        _t("sql_1", "sql", status="RUNNING"),
    )
    out = PlanOps.reset_orphan_running(p, None, None)
    statuses = [t.status for t in out.tasks]
    cases.append({
        "id": "orphan_001_reset",
        "expected": ["PENDING", "PENDING"],
        "got": statuses,
        "ok": statuses == ["PENDING", "PENDING"],
    })

    # 4-2. Orphan 없음 — 직전 gate가 active task를 종결한 경우 RUNNING 유지
    p = _plan(_t("sql_1", "sql", status="RUNNING"))
    last_report = {
        "gate_name": "sql_gate",
        "status": "PASS",
        "task_id": "sql_1",
    }
    out = PlanOps.reset_orphan_running(p, "sql_1", last_report)
    cases.append({
        "id": "orphan_002_keep",
        "expected": "RUNNING (보존)",
        "got": out.tasks[0].status,
        "ok": out.tasks[0].status == "RUNNING",
    })

    return cases


# ─────────────────────────────────────────
# Main
# ─────────────────────────────────────────
def main():
    print("PlanOps 상태 전이 회귀 테스트\n")

    all_results = []
    sections = [
        ("apply_gate_report", test_apply_gate_report()),
        ("next_runnable_batch", test_next_runnable_batch()),
        ("mark_running_batch", test_mark_running_batch()),
        ("reset_orphan_running", test_reset_orphan_running()),
    ]

    for section, cases in sections:
        print(f"━━━ {section} ({len(cases)} 케이스) ━━━")
        for c in cases:
            mark = "✅" if c["ok"] else "❌"
            print(f"  [{c['id']:38s}] {mark}")
            if not c["ok"]:
                print(f"    expected: {c['expected']}")
                print(f"    got     : {c['got']}")
            all_results.append({"section": section, **c})
        print()

    # ─────────────────────────────────────────
    # 저장
    # ─────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    passed = sum(r["ok"] for r in all_results)
    n = len(all_results)
    out = {
        "timestamp": ts,
        "n": n,
        "passed": passed,
        "failed": n - passed,
        "accuracy": passed / n if n else 0.0,
        "results": all_results,
        "by_section": {},
    }
    for section, cases in sections:
        sec_passed = sum(c["ok"] for c in cases)
        out["by_section"][section] = {
            "n": len(cases),
            "passed": sec_passed,
            "accuracy": sec_passed / len(cases) if cases else 0.0,
        }

    fp = OUT_DIR / f"plan_ops_eval_{ts}.json"
    json.dump(out, open(fp, "w"), ensure_ascii=False, indent=2)

    # ─────────────────────────────────────────
    # 요약
    # ─────────────────────────────────────────
    print("=" * 60)
    print("PlanOps 회귀 평가 결과")
    print("=" * 60)
    for section, stats in out["by_section"].items():
        print(f"  {section:22s} {stats['passed']}/{stats['n']} = {stats['accuracy']:.4f}")
    print(f"  {'─' * 50}")
    print(f"  {'전체':22s} {passed}/{n} = {passed/n:.4f}")
    print(f"\n저장: {fp}")


if __name__ == "__main__":
    main()
