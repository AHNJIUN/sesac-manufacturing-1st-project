from manufacturing_agent.graph.build import _wrap_retry
from manufacturing_agent.contracts.reducers import dict_merge_max

def test_wrap_retry_two_calls_merge_max():
    def fake_agent(_state): return {}
    f = _wrap_retry(fake_agent, "prediction")
    r1 = f({"retry_counts": {}})
    r2 = f({"retry_counts": {}})
    merged = dict_merge_max(r1["retry_counts"], r2["retry_counts"])
    assert merged == {"prediction": 1}