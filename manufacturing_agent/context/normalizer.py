from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403
from manufacturing_agent.contracts.context import ContextResolution, MachineValue

# ---------- context/context_normalizer.py ----------
def _machine_value_from_context(name: str, val: Any, *, is_current: bool, source: str) -> MachineValue:
    return MachineValue(name=name, value=val, source=source, is_current=is_current, is_stale=False)


def normalize_context(selected: dict) -> tuple[dict[str, MachineValue], list[str]]:
    """ContextResolution 결과를 PredictionAgent 입력용 MachineValue로 변환한다.

    이전 feature를 자동 보완하지 않는다. resolved_features는 CURRENT_ONLY, USE_ACTIVE,
    PATCH_ACTIVE, SELECT_HISTORY 중 하나의 mode에서 만들어진 단일 context 결과다.
    """
    resolution = selected.get("context_resolution") or ContextResolution(
        mode="CURRENT_ONLY",
        current_values=selected.get("current_values") or {},
        resolved_features=selected.get("current_values") or {},
        changed_features=list((selected.get("current_values") or {}).keys()),
        reason="context_resolution missing; current values only",
    )
    warnings: list[str] = list(resolution.warnings or [])
    merged: dict[str, MachineValue] = {}
    current_keys = set((resolution.current_values or {}).keys())
    changed = set(resolution.changed_features or [])
    reused = set(resolution.reused_features or [])

    for name, val in (resolution.resolved_features or {}).items():
        is_current = name in current_keys and (resolution.mode == "CURRENT_ONLY" or name in changed)
        if is_current:
            source = "current"
        elif name in reused:
            source = "active_context" if resolution.mode in {"USE_ACTIVE", "PATCH_ACTIVE"} else "history_context"
        else:
            source = "context"
        merged[name] = _machine_value_from_context(name, val, is_current=is_current, source=source)

    if selected.get("injection_in_current"):
        warnings.append("현재 입력에서 prompt injection 의심 패턴 감지 → 무력화")
    return merged, warnings
