"""Tests for manufacturing_agent.util.append_gate_report / GATE_REPORTS_MAX."""
from manufacturing_agent.util import append_gate_report, GATE_REPORTS_MAX


class _FakeReport:
    def __init__(self, idx):
        self.idx = idx

    def model_dump(self):
        return {"idx": self.idx}


def test_append_preserves_order_for_objects_and_dicts():
    state = {"gate_reports": [{"idx": 0}]}
    # object with model_dump
    out1 = append_gate_report(state, _FakeReport(1))
    assert out1 == [{"idx": 0}, {"idx": 1}]
    # raw dict appended verbatim
    out2 = append_gate_report({"gate_reports": out1}, {"idx": 2})
    assert out2 == [{"idx": 0}, {"idx": 1}, {"idx": 2}]


def test_append_handles_missing_gate_reports_key():
    out = append_gate_report({}, {"idx": 0})
    assert out == [{"idx": 0}]


def test_append_does_not_mutate_input_list():
    original = [{"idx": 0}]
    state = {"gate_reports": original}
    append_gate_report(state, {"idx": 1})
    assert original == [{"idx": 0}]  # caller's list untouched


def test_append_caps_at_max_keeping_most_recent():
    # Pre-fill with exactly GATE_REPORTS_MAX reports, then append one more.
    state = {"gate_reports": [{"idx": i} for i in range(GATE_REPORTS_MAX)]}
    out = append_gate_report(state, {"idx": GATE_REPORTS_MAX})
    assert len(out) == GATE_REPORTS_MAX
    # oldest (idx 0) dropped, newest kept.
    assert out[0] == {"idx": 1}
    assert out[-1] == {"idx": GATE_REPORTS_MAX}
