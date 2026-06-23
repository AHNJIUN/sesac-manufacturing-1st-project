"""라우팅 eval — SupervisorPlanner가 사용자 의도를 올바른 worker 조합으로 분해하는지 측정한다.

목적:
  - "쉬운 예시만 보면 잘 되는 것처럼 보인다"를 방지하는 기준선(baseline) 측정 도구.
  - 이후 (a) 라우팅 LLM 콜 합치기 리팩터링이 정확도를 깨지 않는지 회귀 검증하는 안전망.

특징:
  - 실제 LLM(call_llm, OpenAI)을 호출하므로 비용이 든다. CI test가 아니라 on-demand eval이다.
  - planner의 최종 정규화 결정(_llm_supervisor_planner_decision)을 평가한다(= 실제 라우팅에 쓰이는 값).

실행(프로젝트 루트에서):
  PYTHONUTF8=1 PYTHONPATH=. python evals/routing_eval.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manufacturing_agent.contracts.context import ContextPacket, ContextCarryoverDecision
from manufacturing_agent.graph.planner import _llm_supervisor_planner_decision


def _packet(*, prev_sql=None, prev_ev=None, prev_pred=None, recent="",
            uses_sql=False, uses_ev=False, uses_pred=False, followup=False) -> ContextPacket:
    """멀티턴 케이스용 최소 ContextPacket(이전 요약 + carryover)."""
    return ContextPacket(
        current_question="",
        recent_turns_summary=recent,
        previous_sql_summary=prev_sql,
        previous_evidence_summary=prev_ev,
        previous_prediction_summary=prev_pred,
        context_carryover=ContextCarryoverDecision(
            is_followup=followup, uses_previous_sql=uses_sql,
            uses_previous_evidence=uses_ev, uses_previous_prediction=uses_pred),
    )


# 각 케이스: id, 설명, message, (선택)structured 입력, (선택)packet, 기대 라우팅.
# 기대값은 needs_prediction/needs_sql/needs_evidence (intent는 참고용 soft 비교).
CASES = [
    # --- 단일 worker ---
    dict(id="pred_only", msg="토크 60, 공구마모 210만 보고 고장 위험 진단해줘",
         exp=dict(needs_prediction=True, needs_sql=False, needs_evidence=False)),
    dict(id="pred_structured", msg="입력한 수치로 위험 진단해줘",
         structured={"type": "M", "air_temperature": 300.0, "process_temperature": 311.0,
                     "rotational_speed": 1400.0, "torque": 55.0, "tool_wear": 180.0},
         exp=dict(needs_prediction=True, needs_sql=False, needs_evidence=False)),
    dict(id="sql_detail", msg="최근 TWF 사례에서 어떤 조치를 했는지 정리해줘",
         exp=dict(needs_prediction=False, needs_sql=True, needs_evidence=False)),
    dict(id="sql_aggregate", msg="고장 유형별 반복 패턴과 부품별 다운타임 통계를 집계해줘",
         exp=dict(needs_prediction=False, needs_sql=True, needs_evidence=False)),
    dict(id="sql_history", msg="최근 30일 고장 이력과 대응 방식을 조회해서 요약해줘",
         exp=dict(needs_prediction=False, needs_sql=True, needs_evidence=False)),
    dict(id="evidence_proc", msg="스핀들 베어링 점검 절차를 매뉴얼 근거로 알려줘",
         exp=dict(needs_prediction=False, needs_sql=False, needs_evidence=True)),
    dict(id="safety_guidance", msg="점검 없이 재가동해도 돼? 왜 위험한지 매뉴얼 근거와 안전 절차를 알려줘",
         exp=dict(needs_prediction=False, needs_sql=False, needs_evidence=True)),
    dict(id="general_qa", msg="CNC 가공에서 채터(chatter)가 뭐야?",
         exp=dict(needs_prediction=False, needs_sql=False, needs_evidence=True)),

    # --- 복합 ---
    dict(id="combined_all", msg="현재 위험 진단, 과거 유사 사례, 점검 문서 근거까지 다 알려줘",
         structured={"type": "H", "air_temperature": 301.0, "process_temperature": 312.0,
                     "rotational_speed": 1380.0, "torque": 64.0, "tool_wear": 215.0},
         exp=dict(needs_prediction=True, needs_sql=True, needs_evidence=True)),
    dict(id="hist_plus_evidence", msg="비슷한 과거 고장 사례랑 그 원인 설명 문서도 같이 줘",
         exp=dict(needs_prediction=False, needs_sql=True, needs_evidence=True)),

    # --- 애매 ---
    dict(id="ambiguous_summary", msg="요약해줘",
         exp=dict(needs_evidence=True), soft=True),  # worker 없음→evidence fallback 기대(soft)

    # --- 멀티턴 후속 ---
    dict(id="followup_sql", msg="방금 조회한 고장 유형 중 HIGH 심각도만 다시 정리해줘",
         packet=_packet(prev_sql="status=OK; detail rows=20 ...", uses_sql=True, followup=True),
         exp=dict(needs_sql=True), soft=True),
    dict(id="followup_evidence", msg="방금 근거 기준으로 점검 항목을 더 구체화해줘",
         packet=_packet(prev_ev="status=OK; sources=[...]", uses_ev=True, followup=True),
         exp=dict(needs_evidence=True), soft=True),
    dict(id="followup_pred_patch", msg="그럼 다른 조건은 그대로 두고 토크만 75로 올리면?",
         packet=_packet(prev_pred="위험 높음: OSF/TWF ...", uses_pred=True, followup=True),
         exp=dict(needs_prediction=True), soft=True),
]


def _state(case: dict) -> dict:
    return {
        "user_message": case["msg"],
        "input_features": case.get("structured"),
        "context_packet": case.get("packet"),
    }


def _check(exp: dict, dec, soft: bool) -> tuple[bool, list[str]]:
    """기대한 필드만 비교. soft면 명시된 필드만(나머지는 무시), 아니면 명시 필드 일치 요구."""
    got = {"needs_prediction": dec.needs_prediction, "needs_sql": dec.needs_sql,
           "needs_evidence": dec.needs_evidence}
    misses = [f"{k}: 기대={v} 실제={got.get(k)}" for k, v in exp.items()
              if k in got and got.get(k) != v]
    return (len(misses) == 0), misses


def main() -> int:
    rows, passed = [], 0
    for case in CASES:
        try:
            dec = _llm_supervisor_planner_decision(_state(case))
            ok, misses = _check(case["exp"], dec, case.get("soft", False))
        except Exception as e:
            ok, misses = False, [f"EXCEPTION {type(e).__name__}: {e}"]
            dec = None
        passed += int(ok)
        tag = "✓" if ok else "✗"
        route = (f"P={int(dec.needs_prediction)} S={int(dec.needs_sql)} E={int(dec.needs_evidence)} "
                 f"intent={dec.intent}") if dec else "(error)"
        soft = " (soft)" if case.get("soft") else ""
        rows.append(f"{tag} {case['id']:<22}{soft:<7} {route}" + (f"   ← {'; '.join(misses)}" if misses else ""))
    print("\n=== 라우팅 eval 결과 ===")
    print("\n".join(rows))
    print(f"\n점수: {passed}/{len(CASES)} = {passed/len(CASES)*100:.0f}%")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
