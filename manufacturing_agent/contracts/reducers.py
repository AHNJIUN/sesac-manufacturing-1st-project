"""병렬 worker 동시 쓰기를 안전하게 합치는 reducer 모음."""
from __future__ import annotations
from typing import Any


def dict_merge_max(left: dict[str, int] | None, right: dict[str, int] | None) -> dict[str, int]:
    """retry_counts용. 같은 키는 max로. 병렬 worker가 각자 +1 한 결과를 손실 없이 합친다."""
    if not left:
        return dict(right or {})
    if not right:
        return dict(left)
    out = dict(left)
    for k, v in right.items():
        out[k] = max(out.get(k, 0), v)
    return out


def dict_merge_last_wins(left: dict | None, right: dict | None) -> dict:
    """[현재 미사용 — ADR-0004 기준] 향후 다중 writer dict 필드 등장 시 사용 예약.
    agent_feedback은 dispatcher 단일 writer라 본 reducer 불필요."""
    out = dict(left or {})
    out.update(right or {})
    return out


def replace_if_present(left: Any, right: Any) -> Any:
    """단일 객체용 명시 reducer. right가 None이면 left를 유지(부분 업데이트)."""
    return right if right is not None else left