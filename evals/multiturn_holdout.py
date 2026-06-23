"""멀티턴 held-out 검증 — 프롬프트 튜닝에 사용하지 않은 '안 본' 케이스로 일반화를 확인한다.

규율: 이 파일의 케이스는 CONTEXT_DECISION_SYS 튜닝에 쓰이지 않았다.
      결과를 보고 프롬프트를 추가로 고치지 않는다(고치면 더 이상 held-out이 아님).
      multiturn_eval.py와 같은 원칙을 '다른 표현/다른 부류'로 다시 물어 taught-to-test를 가려낸다.

실행: PYTHONUTF8=1 PYTHONPATH=. python evals/multiturn_holdout.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manufacturing_agent.context.engine import decide_context
from manufacturing_agent.context.policy import extract_machine_values
from manufacturing_agent.contracts.context import DiagnosisContext

A = DiagnosisContext(id="A", turn_id="t", user_id="u", thread_id="th",
    features={"air_temperature": 300.0, "process_temperature": 311.0,
              "rotational_speed": 1400.0, "torque": 55.0, "tool_wear": 180.0},
    failure_types=["TWF"], prediction_summary="이전1: 공구마모(TWF) 주의, 위험 중간.",
    created_at="2026-06-23T10:00:00")
B = DiagnosisContext(id="B", turn_id="t2", user_id="u", thread_id="th",
    features={"air_temperature": 302.0, "process_temperature": 318.0,
              "rotational_speed": 1300.0, "torque": 82.0, "tool_wear": 120.0},
    failure_types=["OSF"], prediction_summary="이전2: 과부하(OSF) 위험 높음.",
    created_at="2026-06-23T10:05:00")


def _sel(msg, *, active=A, recents=None, prev_pred=True, prev_sql=None, prev_ev=None):
    return {"current_values": dict(extract_machine_values(msg)),
            "active_context": active, "recent_contexts": recents if recents is not None else ([active] if active else []),
            "recent_turns": [], "previous_prediction_summary": A.prediction_summary if prev_pred else None,
            "previous_sql_summary": prev_sql, "previous_evidence_summary": prev_ev, "injection_in_current": False}


CASES = [
    # 상대 변경 — 튜닝 때 안 쓴 연산(+델타/-델타/비율)
    dict(id="rel_add_speed", msg="회전속도를 200 더 올리면 위험이 어때?",
         sel=lambda m: _sel(m), exp_mode="PATCH_ACTIVE",
         check=lambda d: d.resolved_features.get("rotational_speed") == 1600.0),
    dict(id="rel_sub_temp", msg="공정 온도를 10도 낮추면 위험이 달라져?",
         sel=lambda m: _sel(m), exp_mode="PATCH_ACTIVE",
         check=lambda d: d.resolved_features.get("process_temperature") == 301.0),
    dict(id="rel_half_torque", msg="토크를 절반으로 줄이면?",
         sel=lambda m: _sel(m), exp_mode="PATCH_ACTIVE",
         check=lambda d: d.resolved_features.get("torque") == 27.5),
    # 새 대상 — 다른 표현
    dict(id="separate_machine", msg="이건 별개 설비야. 토크 35만 측정했어, 진단해줘",
         sel=lambda m: _sel(m), exp_mode="CURRENT_ONLY",
         check=lambda d: d.resolved_features.get("torque") == 35.0 and not d.reused_features),
    dict(id="remeasure_sensor", msg="센서 다시 읽었더니 토크 52, 공구마모 200 나왔어. 다시 진단해줘",
         sel=lambda m: _sel(m), exp_mode="CURRENT_ONLY",
         check=lambda d: "air_temperature" not in d.resolved_features),
    dict(id="reset_session", msg="처음부터 다시 할게. 토크 60으로 진단해줘",
         sel=lambda m: _sel(m), exp_mode="CURRENT_ONLY",
         check=lambda d: d.resolved_features.get("torque") == 60.0 and not d.reused_features),
    # 과거조건 선택 — 다른 속성(고장유형)
    dict(id="select_by_failuretype", msg="공구마모 문제였던 쪽 조건으로 다시 진단해줘",
         sel=lambda m: _sel(m, recents=[A, B]), exp_mode="SELECT_HISTORY",
         check=lambda d: d.resolved_features.get("torque") == 55.0),
    # 순서 + patch 조합
    dict(id="ordinal_patch", msg="첫 번째 조건에서 토크만 90으로 바꾸면?",
         sel=lambda m: _sel(m, recents=[A, B]), exp_mode="SELECT_HISTORY", soft=True,
         check=lambda d: d.resolved_features.get("torque") == 90.0),
    # carryover 둘 다(sql + evidence)
    dict(id="carry_both", msg="방금 이력이랑 문서 근거 같이 묶어서 정리해줘",
         sel=lambda m: _sel(m, active=None, recents=[], prev_pred=False,
                            prev_sql="status=OK; ...", prev_ev="status=OK; ..."),
         exp_mode=None, check=lambda d: d.is_followup and d.uses_previous_sql and d.uses_previous_evidence),
    # 비교 질의(재진단 아님)
    dict(id="compare_two", msg="방금 두 조건 중 어느 쪽이 더 위험했어?",
         sel=lambda m: _sel(m, recents=[A, B]), exp_mode="REFER_ACTIVE_RESULT", soft=True,
         check=lambda d: not d.resolved_features),
    # 제외 표현 — 다른 feature
    dict(id="exclude_air", msg="공기온도는 빼고 나머지 조건 그대로 다시 봐줘",
         sel=lambda m: _sel(m), exp_mode=None, soft=True,
         check=lambda d: d.resolved_features.get("air_temperature") != 0.0),
]


def main() -> int:
    rows, passed, scored = [], 0, 0
    for c in CASES:
        soft = c.get("soft", False)
        try:
            d = decide_context(c["msg"], c["sel"](c["msg"]))
            mode_ok = (c["exp_mode"] is None) or (d.mode == c["exp_mode"])
            check_ok = (c["check"] is None) or bool(c["check"](d))
            ok = mode_ok and check_ok
        except Exception as e:
            d, ok, check_ok = None, False, False
            err = f"{type(e).__name__}: {e}"
        if not soft:
            scored += 1; passed += int(ok)
        tag = "✓" if ok else ("~" if soft else "✗")
        if d is not None:
            carry = f"uses(p/s/e)={int(d.uses_previous_prediction)}{int(d.uses_previous_sql)}{int(d.uses_previous_evidence)}"
            detail = f"mode={d.mode} {carry} resolved={ {k: d.resolved_features[k] for k in sorted(d.resolved_features)} }"
            why = "" if ok else f"   ← 기대 mode={c['exp_mode']}, check_ok={check_ok}"
        else:
            detail, why = f"ERROR {err}", ""
        rows.append(f"{tag} {c['id']:<22}{' (soft)' if soft else '':<7} {detail}{why}")
    print("\n=== 멀티턴 HELD-OUT 검증 (안 본 케이스) ===")
    print("\n".join(rows))
    print(f"\n점수(soft 제외): {passed}/{scored} = {passed/scored*100:.0f}%")
    return 0 if passed == scored else 1


if __name__ == "__main__":
    raise SystemExit(main())
