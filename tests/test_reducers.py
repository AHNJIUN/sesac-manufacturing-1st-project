from manufacturing_agent.contracts.reducers import (
    dict_merge_max, dict_merge_last_wins, replace_if_present,
)

def test_dict_merge_max_appends_disjoint_keys():
    assert dict_merge_max({"prediction": 1}, {"sql": 1}) == {"prediction": 1, "sql": 1}

def test_dict_merge_max_takes_higher():
    assert dict_merge_max({"prediction": 2}, {"prediction": 1}) == {"prediction": 2}
    assert dict_merge_max({"prediction": 1}, {"prediction": 2}) == {"prediction": 2}

def test_dict_merge_max_handles_none():
    assert dict_merge_max(None, {"a": 1}) == {"a": 1}
    assert dict_merge_max({"a": 1}, None) == {"a": 1}

def test_dict_merge_last_wins():
    assert dict_merge_last_wins({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}

def test_replace_if_present_skips_none():
    assert replace_if_present("old", None) == "old"
    assert replace_if_present("old", "new") == "new"