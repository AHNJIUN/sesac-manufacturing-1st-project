"""SQL/evidence 결과를 붙잡고 재질문하는 멀티턴 시나리오 — 두 레이어를 함께 검증한다.

레이어1) decide_context: 이전 sql/evidence artifact 참조를 is_followup/uses_previous_sql/uses_previous_evidence로 잡나?
레이어2) supervisor_planner: 그 carryover를 받아 needs_sql/needs_evidence를 맞게 라우팅하나?

실제 파이프라인 순서대로 체이닝한다: decide_context → carryover를 ContextPacket에 담아 → planner.
탐색용 on-demand eval(실 LLM 호출). 실행: PYTHONUTF8=1 PYTHONPATH=. python evals/sql_evidence_followup.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manufacturing_agent.context.engine import decide_context
from manufacturing_agent.context.policy import extract_machine_values
from manufacturing_agent.contracts.context import ContextPacket
from manufacturing_agent.graph.planner import _llm_supervisor_planner_decision

PREV_SQL = "status=OK; query_type=detail; rows=20; sample_rows=[{event_date:2026-06-20,failure_type:HDF,downtime_min:55},{event_date:2026-06-18,failure_type:TWF,downtime_min:120}]"
PREV_EV = "status=OK; profile=troubleshooting_rag; sources=[haas_TG0101, kosha_M-114]; summary=스핀들 베어링 과열 점검 절차 근거 요약"


def _selected(msg, *, prev_sql=None, prev_ev=None):
    return {"current_values": dict(extract_machine_values(msg)),
            "active_context": None, "recent_contexts": [], "recent_turns": [{"role": "user", "content": "이전 질문"}],
            "previous_prediction_summary": None, "previous_sql_summary": prev_sql,
            "previous_evidence_summary": prev_ev, "injection_in_current": False}


def _run(msg, prev_sql, prev_ev):
    sel = _selected(msg, prev_sql=prev_sql, prev_ev=prev_ev)
    d = decide_context(msg, sel)
    packet = ContextPacket(current_question=msg, recent_turns_summary="이전 턴: 이력/근거 조회",
                           previous_sql_summary=prev_sql, previous_evidence_summary=prev_ev,
                           context_carryover=d.to_carryover())
    decision = _llm_supervisor_planner_decision({"user_message": msg, "input_features": None, "context_packet": packet})
    return d, decision


# exp_carry: 하드 검사(is_followup/uses_sql/uses_ev 중 명시한 것). exp_route: needs_*; route_soft면 참고만.
CASES = [
    dict(id="sql_filter_downtime", msg="방금 조회한 이력 중 다운타임이 가장 큰 사례만 알려줘",
         prev_sql=PREV_SQL, prev_ev=None,
         exp_carry=dict(is_followup=True, uses_previous_sql=True), exp_route=dict(needs_sql=True)),
    dict(id="sql_filter_type", msg="그 이력에서 TWF 유형만 다시 추려서 정리해줘",
         prev_sql=PREV_SQL, prev_ev=None,
         exp_carry=dict(is_followup=True, uses_previous_sql=True), exp_route=dict(needs_sql=True)),
    dict(id="sql_more_similar", msg="방금 결과랑 비슷한 과거 사례가 더 있는지 찾아줘",
         prev_sql=PREV_SQL, prev_ev=None,
         exp_carry=dict(is_followup=True, uses_previous_sql=True), exp_route=dict(needs_sql=True)),
    dict(id="sql_actions_only", msg="그 사례들에서 대응 조치만 뽑아 정리해줘",
         prev_sql=PREV_SQL, prev_ev=None,
         exp_carry=dict(is_followup=True, uses_previous_sql=True), exp_route=dict(needs_sql=True), route_soft=True),
    dict(id="sql_new_window", msg="아까 거 말고 최근 7일 기준으로 이력 다시 조회해줘",
         prev_sql=PREV_SQL, prev_ev=None,
         exp_carry=dict(is_followup=True), exp_route=dict(needs_sql=True)),
    # evidence 재질문
    dict(id="ev_detail", msg="방금 그 문서 근거를 더 자세히 설명해줘",
         prev_sql=None, prev_ev=PREV_EV,
         exp_carry=dict(is_followup=True, uses_previous_evidence=True), exp_route=dict(needs_evidence=True)),
    dict(id="ev_safety_only", msg="그 점검 절차에서 안전 주의사항만 따로 정리해줘",
         prev_sql=None, prev_ev=PREV_EV,
         exp_carry=dict(is_followup=True, uses_previous_evidence=True), exp_route=dict(needs_evidence=True)),
    dict(id="ev_source", msg="방금 근거의 출처가 어디야?",
         prev_sql=None, prev_ev=PREV_EV,
         exp_carry=dict(is_followup=True, uses_previous_evidence=True), exp_route=dict(needs_evidence=True), route_soft=True),
    # 혼합 + 트랩
    dict(id="mix_both", msg="방금 이력이랑 문서 근거를 같이 묶어서 결론만 정리해줘",
         prev_sql=PREV_SQL, prev_ev=PREV_EV,
         exp_carry=dict(is_followup=True, uses_previous_sql=True, uses_previous_evidence=True),
         exp_route=dict(needs_sql=True, needs_evidence=True), route_soft=True),
    dict(id="trap_add_new_evidence", msg="그럼 이 고장에 대한 점검 문서 근거도 새로 찾아줘",
         prev_sql=PREV_SQL, prev_ev=None,
         exp_carry=dict(is_followup=True), exp_route=dict(needs_evidence=True)),
]


def _subset_ok(exp: dict, obj) -> bool:
    return all(getattr(obj, k) == v for k, v in exp.items())


def main() -> int:
    rows, passed, scored = [], 0, 0
    for c in CASES:
        try:
            d, dec = _run(c["msg"], c["prev_sql"], c["prev_ev"])
            carry_ok = _subset_ok(c["exp_carry"], d)
            route_ok = _subset_ok(c["exp_route"], dec)
            ok = carry_ok and (route_ok or c.get("route_soft"))
        except Exception as e:
            d = dec = None; carry_ok = route_ok = ok = False; err = f"{type(e).__name__}: {e}"
        scored += 1; passed += int(ok)
        tag = "✓" if ok else "✗"
        if d is not None:
            info = (f"carry[fup={int(d.is_followup)} s={int(d.uses_previous_sql)} e={int(d.uses_previous_evidence)}] "
                    f"route[P={int(dec.needs_prediction)} S={int(dec.needs_sql)} E={int(dec.needs_evidence)} {dec.intent}]")
            why = "" if ok else f"   ← carry_ok={carry_ok} route_ok={route_ok}{' (route soft)' if c.get('route_soft') else ''}"
        else:
            info, why = f"ERROR {err}", ""
        rows.append(f"{tag} {c['id']:<22} {info}{why}")
    print("\n=== SQL/evidence 재질문 시나리오 (carryover + 라우팅) ===")
    print("\n".join(rows))
    print(f"\n점수: {passed}/{scored} = {passed/scored*100:.0f}%")
    return 0 if passed == scored else 1


if __name__ == "__main__":
    raise SystemExit(main())
