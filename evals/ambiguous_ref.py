"""모호한 참조 멀티턴 — 여러 artifact(prediction/sql/evidence)가 동시에 있을 때
'그거/방금 거/그 근거'가 무엇을 가리키는지 carryover 결정이 합리적인지 관찰/검증한다.

- 의미적 lean이 분명한 것(왜 위험→진단, 어떤 사례→이력, 근거→문서)은 채점.
- 진짜 모호한 것('그거', '이어서')은 soft로 두고 '무엇을 골랐는지'만 관찰(정답 없음).

탐색용 on-demand eval. 실행: PYTHONUTF8=1 PYTHONPATH=. python evals/ambiguous_ref.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manufacturing_agent.context.engine import decide_context
from manufacturing_agent.context.policy import extract_machine_values
from manufacturing_agent.contracts.context import DiagnosisContext

A = DiagnosisContext(id="A", turn_id="t", user_id="u", thread_id="th",
    features={"air_temperature": 301.0, "process_temperature": 312.0,
              "rotational_speed": 1380.0, "torque": 64.0, "tool_wear": 215.0},
    failure_types=["OSF", "TWF"], prediction_summary="이전 진단: 과부하·공구마모 위험 높음.",
    created_at="2026-06-23T10:00:00")
PREV_SQL = "status=OK; detail rows=9; 최근 30일 고장 9건, 대표 HDF/TWF"
PREV_EV = "status=OK; sources=[haas_TG0101, kosha_M-114]; 스핀들 점검 절차 근거"


def _sel(msg, *, active=A, prev_pred=True, prev_sql=PREV_SQL, prev_ev=PREV_EV):
    return {"current_values": dict(extract_machine_values(msg)),
            "active_context": active, "recent_contexts": [active] if active else [],
            "recent_turns": [{"role": "user", "content": "이전 질문"}],
            "previous_prediction_summary": A.prediction_summary if prev_pred else None,
            "previous_sql_summary": prev_sql, "previous_evidence_summary": prev_ev,
            "injection_in_current": False}


CASES = [
    # 진짜 모호 — soft, 관찰만
    dict(id="bare_geugeo", msg="그거 다시 보여줘", sel=lambda m: _sel(m), soft=True),
    dict(id="more_detail", msg="좀 더 자세히 설명해줘", sel=lambda m: _sel(m), soft=True),
    dict(id="continue", msg="이어서 해줘", sel=lambda m: _sel(m), soft=True),
    dict(id="it_oneline", msg="방금 거 한 줄로 정리해줘", sel=lambda m: _sel(m), soft=True),
    # 의미적 lean — 채점
    dict(id="why_dangerous", msg="왜 그렇게 위험한 거야?", sel=lambda m: _sel(m),
         exp=dict(uses_previous_prediction=True)),
    dict(id="what_cases", msg="아까 어떤 과거 사례들이었지?", sel=lambda m: _sel(m),
         exp=dict(uses_previous_sql=True)),
    dict(id="what_grounds", msg="그 근거가 어느 문서였어?", sel=lambda m: _sel(m, prev_pred=False, active=None),
         exp=dict(uses_previous_evidence=True)),
    dict(id="those_severe", msg="그것들 중 제일 심각했던 사례는 뭐야?", sel=lambda m: _sel(m, prev_pred=False, active=None),
         exp=dict(uses_previous_sql=True)),
    dict(id="explicit_not_pred", msg="그 진단 말고 과거 이력 쪽으로 다시 정리해줘", sel=lambda m: _sel(m),
         exp=dict(uses_previous_sql=True, uses_previous_prediction=False)),
    dict(id="explicit_not_sql", msg="이력 말고 문서 근거만 더 설명해줘", sel=lambda m: _sel(m),
         exp=dict(uses_previous_evidence=True, uses_previous_sql=False)),
]


def _subset_ok(exp, d):
    return all(getattr(d, k) == v for k, v in exp.items())


def main() -> int:
    rows, passed, scored = [], 0, 0
    for c in CASES:
        soft = c.get("soft", False)
        try:
            d = decide_context(c["msg"], c["sel"](c["msg"]))
            ok = True if soft else _subset_ok(c["exp"], d)
        except Exception as e:
            d, ok = None, False; err = f"{type(e).__name__}: {e}"
        if not soft:
            scored += 1; passed += int(ok)
        tag = "~" if soft else ("✓" if ok else "✗")
        if d is not None:
            refs = ",".join(d.referenced_artifacts) or "-"
            info = (f"mode={d.mode} fup={int(d.is_followup)} "
                    f"uses(p/s/e)={int(d.uses_previous_prediction)}{int(d.uses_previous_sql)}{int(d.uses_previous_evidence)} refs=[{refs}]")
            why = "" if (soft or ok) else f"   ← 기대 {c['exp']}"
        else:
            info, why = f"ERROR {err}", ""
        rows.append(f"{tag} {c['id']:<18}{' (soft)' if soft else '':<7} {info}{why}")
    print("\n=== 모호한 참조 멀티턴 (관찰=soft / lean=채점) ===")
    print("\n".join(rows))
    print(f"\n채점(soft 제외): {passed}/{scored} = {passed/scored*100:.0f}%")
    return 0 if passed == scored else 1


if __name__ == "__main__":
    raise SystemExit(main())
