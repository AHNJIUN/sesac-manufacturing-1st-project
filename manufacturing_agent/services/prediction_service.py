from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403
from manufacturing_agent.context.policy import STANDARD_FEATURES
from manufacturing_agent.contracts.context import EvidenceHint, FailureRisk, SafetyHint

# ---------- services/prediction_service.py ----------
# 고장 유형별 필요 feature (README 9.1)
FAILURE_FEATURES = {
    "HDF": ["air_temperature", "process_temperature", "rotational_speed"],
    "PWF": ["rotational_speed", "torque"],
    "OSF": ["tool_wear", "torque", "type"],
    "TWF": ["tool_wear"],
}
PREDICTION_FEATURES = STANDARD_FEATURES
OSF_THRESHOLD = {"L": 11000, "M": 12000, "H": 13000}
RISK_QUERY_TERMS = {
    "HDF": ["heat dissipation failure", "온도차", "저속 회전", "냉각 점검"],
    "PWF": ["power failure", "출력 이상", "토크", "회전속도"],
    "OSF": ["overstrain failure", "공구마모", "토크", "부하 한계"],
    "TWF": ["tool wear failure", "공구마모", "공구 수명", "교체 기준"],
}
RISK_CHECKS = {
    "HDF": ["공정온도와 공기온도 센서 확인", "냉각/환기 상태 점검", "저속 운전 조건 확인"],
    "PWF": ["토크와 rpm 계측값 재확인", "스핀들 부하와 전원부 상태 점검"],
    "OSF": ["공구마모와 토크 조합 확인", "공구/소재별 허용 부하 확인"],
    "TWF": ["공구마모 시간 확인", "공구 상태와 교체 주기 점검"],
}


def _level(score: float) -> str:
    return "high" if score >= 0.66 else "medium" if score >= 0.33 else "low"

_LEVEL_KO = {"high": "높음", "medium": "중간", "low": "낮음"}


def _risk(failure_type: str, score: float, formula: str, rule: str) -> FailureRisk:
    return FailureRisk(
        failure_type=failure_type, level=_level(score), score=round(score, 2),
        detail=formula, rule=rule, formula=formula,
        contributing_features=FAILURE_FEATURES[failure_type],
        evidence_query_terms=RISK_QUERY_TERMS.get(failure_type, []),
        recommended_checks=RISK_CHECKS.get(failure_type, []),
    )


def _to_float(value) -> Optional[float]:
    """feature 값을 float로 해석한다. 숫자가 아니면 None(해당 위험 계산에서 제외)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_partial_risks(feats: dict) -> list[FailureRisk]:
    """규칙 기반 부분 위험. 각 위험에 사람이 읽는 규칙(rule)과 실제 수치가 들어간 계산식(formula)을 함께 만든다.
    숫자로 해석되지 않는 값(빈 문자열, 'unknown' 등)은 해당 위험에서 안전하게 건너뛴다."""
    risks = []
    nums = {k: _to_float(v) for k, v in feats.items()}

    def have(keys) -> bool:  # 모든 키가 존재하고 숫자로 해석될 때만 True
        return all(nums.get(k) is not None for k in keys)

    # HDF: 온도차 < 8.6K & rpm < 1380
    if have(FAILURE_FEATURES["HDF"]):
        dt = abs(nums["process_temperature"] - nums["air_temperature"])
        rpm = nums["rotational_speed"]
        score = (0.5 if dt < 8.6 else 0.0) + (0.5 if rpm < 1380 else 0.0)
        formula = (
            f"온도차 {dt:.1f}K {'(위험: 8.6K 미만)' if dt < 8.6 else '(정상: 8.6K 이상)'} · "
            f"회전속도 {rpm:.0f}rpm {'(위험: 1380 미만)' if rpm < 1380 else '(정상: 1380 이상)'} → {_LEVEL_KO[_level(score)]}"
        )
        risks.append(_risk("HDF", score, formula, "온도차 8.6K 미만 또는 회전속도 1380rpm 미만이면 냉각 저하"))
    # PWF: power = torque * rpm * 2pi/60, 정상 범위 3,500~9,000W
    if have(FAILURE_FEATURES["PWF"]):
        power = nums["torque"] * nums["rotational_speed"] * 2 * 3.14159 / 60
        out_of_range = power < 3500 or power > 9000
        score = 0.7 if out_of_range else 0.1
        formula = f"전력 {power:,.0f}W {'(정상범위 3,500~9,000W 밖)' if out_of_range else '(정상범위 내)'} → {_LEVEL_KO[_level(score)]}"
        risks.append(_risk("PWF", score, formula, "전력(토크×회전속도)이 정상 범위(3,500~9,000W)를 벗어나면 전원/구동 이상"))
    # OSF: tool_wear * torque vs 설비타입 임계값
    if have(["tool_wear", "torque"]) and "type" in feats:
        t = str(feats["type"]).upper()
        if t in OSF_THRESHOLD:
            tw, tq = nums["tool_wear"], nums["torque"]
            strain = tw * tq
            thr = OSF_THRESHOLD[t]
            ratio = strain / thr
            verdict = "초과" if ratio >= 1.0 else ("한계 근접" if ratio >= 0.9 else "이내")
            osf_score = 0.8 if ratio >= 1.0 else (0.5 if ratio >= 0.9 else min(0.3, ratio * 0.3))
            formula = (
                f"{tw:.0f} × {tq:.0f} = {strain:,.0f} {'≥' if strain >= thr else '<'} {t}타입 한계 {thr:,} → "
                f"{verdict}(비율 {ratio:.2f})"
            )
            risks.append(_risk("OSF", osf_score, formula, "공구마모 × 토크가 설비타입 임계값을 넘으면 과부하"))
    # TWF: tool_wear 200min 이상이면 마모 한계
    if nums.get("tool_wear") is not None:
        tw = nums["tool_wear"]
        score = 0.8 if tw >= 200 else (0.4 if tw >= 180 else 0.1)
        band = "200min 이상(높음)" if tw >= 200 else ("180~199min(중간)" if tw >= 180 else "180min 미만(낮음)")
        formula = f"공구마모 {tw:.0f}min → {band}"
        risks.append(_risk("TWF", score, formula, "공구마모 200min 이상이면 마모 한계"))
    return sorted(risks, key=lambda r: r.score, reverse=True)

def build_evidence_hints(risks: list[FailureRisk], missing: list[str]) -> list[EvidenceHint]:
    hints = []
    for idx, risk in enumerate(risks, start=1):
        queries = [
            f"{risk.failure_type} 원인과 점검 방법",
            " ".join([risk.failure_type] + risk.evidence_query_terms[:3]),
        ]
        if missing:
            queries.append(f"{risk.failure_type} 진단에 필요한 누락 입력 {', '.join(missing)}")
        hints.append(EvidenceHint(failure_type=risk.failure_type, priority=idx,
                                  queries=queries, features=risk.contributing_features))
    return hints


def build_safety_hints(risks: list[FailureRisk]) -> list[SafetyHint]:
    hints = []
    for risk in risks:
        if risk.level != "high":
            continue
        hints.append(SafetyHint(
            risk_level="high",
            reason=f"{risk.failure_type} high: {risk.detail}",
            avoid_actions=["안전조치 없는 운전 지속", "안전장치 우회", "점검 전 재가동"],
            required_checks=risk.recommended_checks,
        ))
    return hints


def run_prediction(feats: dict) -> dict:
    present = [f for f in PREDICTION_FEATURES if f in feats]
    missing = [f for f in PREDICTION_FEATURES if f not in feats]
    risks = compute_partial_risks(feats)
    full = len(missing) == 0
    confidence = "high" if full else ("medium" if risks else "low")
    limitations = [] if full else [f"전체 예측에 필요한 입력 누락: {missing}"]
    return {"present": present, "missing": missing, "full": full,
            "risks": risks, "evidence_hints": build_evidence_hints(risks, missing),
            "safety_hints": build_safety_hints(risks), "confidence": confidence,
            "limitations": limitations}
