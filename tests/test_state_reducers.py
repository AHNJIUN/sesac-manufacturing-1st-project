from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from manufacturing_agent.contracts.state import ManufacturingState

def _node_a(_): return {"gate_reports": [{"gate_name": "a", "status": "PASS"}],
                        "retry_counts": {"prediction": 1}}
def _node_b(_): return {"gate_reports": [{"gate_name": "b", "status": "PASS"}],
                        "retry_counts": {"sql": 1}}

def test_parallel_state_merge():
    g = StateGraph(ManufacturingState)
    g.add_node("a", _node_a)
    g.add_node("b", _node_b)
    g.add_node("end", lambda s: {})
    g.add_conditional_edges(START, lambda _: [Send("a", {}), Send("b", {})], ["a", "b"])
    g.add_edge("a", "end"); g.add_edge("b", "end"); g.add_edge("end", END)
    app = g.compile()
    out = app.invoke({})
    assert len(out["gate_reports"]) == 2
    assert out["retry_counts"] == {"prediction": 1, "sql": 1}