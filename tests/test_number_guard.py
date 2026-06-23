"""Tests for the number hallucination guard in final_answer_node."""
from manufacturing_agent.nodes.final_answer_node import _allowed_numbers, _number_guard


def test_allowed_numbers_excludes_citation_only_numbers():
    # 99 appears only under the citations key; it must NOT be whitelisted.
    ctx = {
        "history_summary": "조회된 고장 사례: 5건, 다운타임 420분",
        "citations": "[C99] 문서 제목 1234",
    }
    allowed = _allowed_numbers(ctx)
    assert "5" in allowed
    assert "420" in allowed
    assert "99" not in allowed
    assert "1234" not in allowed
    assert "C99" not in allowed


def test_number_guard_flags_unitted_number_not_allowed():
    issues = _number_guard("토크 99 N·m 로 위험", allowed={"62"})
    assert len(issues) == 1
    assert issues[0].startswith("number_hallucination")
    assert "99" in issues[0]


def test_number_guard_does_not_flag_present_number():
    issues = _number_guard("토크 62 N·m 로 위험", allowed={"62"})
    assert issues == []


def test_number_guard_flags_fabricated_multiplier():
    issues = _number_guard("위험이 3배 증가했다", allowed={"62"})
    assert len(issues) == 1
    assert issues[0].startswith("number_hallucination")
    assert "3배" in issues[0]


def test_number_guard_allows_multiplier_when_in_allowed():
    issues = _number_guard("위험이 3배 증가했다", allowed={"3"})
    assert issues == []


def test_number_guard_skips_single_digit_unit_list_numbers():
    # single-digit non-multiplier unit numbers are treated as list markers, not flagged.
    issues = _number_guard("1 건 확인", allowed=set())
    assert issues == []
