import threading
from manufacturing_agent.observability import record_llm_usage, usage_snapshot

def test_concurrent_record_does_not_lose_count():
    def hit():
        for _ in range(100):
            record_llm_usage("gpt-4o", "default", 10, 5)
    before = usage_snapshot()
    before_calls = sum(v["calls"] for v in before.get("by_model", {}).values())
    ts = [threading.Thread(target=hit) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    after = usage_snapshot()
    after_calls = sum(v["calls"] for v in after.get("by_model", {}).values())
    assert after_calls - before_calls >= 800