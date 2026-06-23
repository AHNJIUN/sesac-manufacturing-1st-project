from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403

def _json_object(raw: str) -> dict:
    """LLM 응답에서 첫 번째 JSON object를 견고하게 추출한다.
    코드펜스(```json)를 제거하고, 첫 '{'부터 balanced하게 닫히는 지점까지만 파싱한다.
    (기존 greedy 패턴은 산문/후행 텍스트의 닫는 괄호 때문에 정상 요청도 parse 실패시킬 수 있었다.)"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    if start == -1:
        return json.loads(text)
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        end = text.rfind("}")
        return json.loads(text[start:end + 1])

def _coerce_bool(value, default: bool = False) -> bool:
    """LLM/JSON에서 온 bool 후보(실제 bool, "true"/"1"/"yes" 등 문자열)를 bool로 정규화한다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "y"}:
            return True
        if low in {"false", "0", "no", "n"}:
            return False
    return default

GATE_REPORTS_MAX = int(os.environ.get("GATE_REPORTS_MAX", "60"))

def append_gate_report(state, report) -> list:
    """현재 gate_reports에 report를 append하되 한 턴 내 상한(GATE_REPORTS_MAX)을 둔다.
    상한 초과 시 가장 오래된 항목부터 버려 최근 report를 유지한다(라우팅·디버깅은 최근 report에 의존)."""
    reports = list(state.get("gate_reports", []) or [])
    reports.append(report.model_dump() if hasattr(report, "model_dump") else report)
    if len(reports) > GATE_REPORTS_MAX:
        reports = reports[-GATE_REPORTS_MAX:]
    return reports

# import * 가 밑줄(_x) 이름까지 가져오도록 명시 export
__all__ = [n for n in dir() if not n.startswith("__")]
