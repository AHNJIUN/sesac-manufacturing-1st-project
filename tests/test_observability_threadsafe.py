import threading
from manufacturing_agent.observability import record_llm_usage, usage_snapshot

def test_concurrent_record_does_not_lose_count():
    def hit():
        for _ in range(100):
            record_llm_usage("gpt-4o", "default", 10, 5)
    ts = [threading.Thread(target=hit) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    snap = usage_snapshot()
    total_calls = sum(v["calls"] for v in snap.get("by_model", {}).values())
    assert total_calls >= 800