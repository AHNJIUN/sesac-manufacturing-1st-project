"""LLM 시스템 프롬프트 모음 패키지.

프롬프트는 같은 폴더의 .txt 파일로 관리한다(코드와 분리, 비개발자도 수정 가능).
사용처는 load_prompt(name)으로 읽는다:

    from manufacturing_agent.prompts import load_prompt
    SUPERVISOR_PLANNER_SYS = load_prompt("supervisor_planner")  # prompts/supervisor_planner.txt

원칙
- .txt 에는 정적 지시문만 둔다. 동적 값은 호출부에서 user 메시지로 주입한다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """prompts/<name>.txt 를 읽어 시스템 프롬프트 문자열로 반환한다(결과 캐시)."""
    path = _PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"프롬프트 파일이 없습니다: {path}")
    return path.read_text(encoding="utf-8")
