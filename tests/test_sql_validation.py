"""Tests for manufacturing_agent.agents.sql_agent pure validation/aggregation helpers."""
import datetime as dt
import pytest

from manufacturing_agent.agents.sql_agent import (
    DEFAULT_SQL_DEPS,
    validate_sql_query,
    _sanitize_time_range,
    build_sql_history_artifact_from_results,
)
from manufacturing_agent.contracts.context import SQLQueryResult

REF_DATE = "2026-06-21"
DEFAULT_DAYS = 30


# ---------- validate_sql_query ----------

def test_validate_accepts_well_formed_select():
    sql = "SELECT id, failure_type FROM failure_history WHERE failure_type = 'TWF' LIMIT 10"
    # Should not raise.
    assert validate_sql_query(sql, DEFAULT_SQL_DEPS) is None


def test_validate_rejects_non_select():
    with pytest.raises(ValueError):
        validate_sql_query("DELETE FROM failure_history WHERE id = 1 LIMIT 1", DEFAULT_SQL_DEPS)


def test_validate_rejects_ddl_keyword():
    with pytest.raises(ValueError):
        validate_sql_query("DROP TABLE failure_history", DEFAULT_SQL_DEPS)


def test_validate_rejects_missing_limit():
    with pytest.raises(ValueError):
        validate_sql_query("SELECT * FROM failure_history", DEFAULT_SQL_DEPS)


def test_validate_rejects_multiple_statements():
    with pytest.raises(ValueError):
        validate_sql_query(
            "SELECT id FROM failure_history LIMIT 5; SELECT 1 LIMIT 1", DEFAULT_SQL_DEPS
        )


def test_validate_rejects_disallowed_table():
    with pytest.raises(ValueError):
        validate_sql_query("SELECT * FROM other_table LIMIT 5", DEFAULT_SQL_DEPS)


def test_validate_rejects_limit_exceeding_max_rows():
    over = DEFAULT_SQL_DEPS.max_rows + 1
    with pytest.raises(ValueError):
        validate_sql_query(f"SELECT id FROM failure_history LIMIT {over}", DEFAULT_SQL_DEPS)


# ---------- _sanitize_time_range ----------

def test_sanitize_corrects_wrong_year_preserving_window():
    # 2023 window (30 days) should be re-anchored to the 2026 reference date,
    # preserving the 30-day span.
    tr = {"start_date": "2023-05-01", "end_date": "2023-05-31"}
    out = _sanitize_time_range(tr, REF_DATE, DEFAULT_DAYS)
    assert out["end_date"] == REF_DATE
    span = (dt.date.fromisoformat(out["end_date"]) - dt.date.fromisoformat(out["start_date"])).days
    original_span = (dt.date.fromisoformat("2023-05-31") - dt.date.fromisoformat("2023-05-01")).days
    assert span == original_span == 30
    assert "note" in out  # correction is annotated


def test_sanitize_keeps_valid_same_year_range_unchanged():
    tr = {"start_date": "2026-05-22", "end_date": "2026-06-21"}
    out = _sanitize_time_range(tr, REF_DATE, DEFAULT_DAYS)
    assert out is tr  # trusted, returned unchanged


def test_sanitize_non_dict_returned_as_is():
    assert _sanitize_time_range("not-a-dict", REF_DATE, DEFAULT_DAYS) == "not-a-dict"


# ---------- build_sql_history_artifact_from_results ----------

def _r(query_type, status, **kw):
    return SQLQueryResult(query_type=query_type, status=status, **kw)


def test_artifact_status_ok_when_any_ok():
    results = [
        _r("detail", "OK", rows=[{"id": 1}], sql="SELECT 1 LIMIT 1"),
        _r("aggregate", "EMPTY"),
    ]
    art = build_sql_history_artifact_from_results(results)
    assert art.status == "OK"
    # primary is the OK result; its rows propagate to artifact-level rows.
    assert art.rows == [{"id": 1}]


def test_artifact_status_empty_when_all_empty():
    art = build_sql_history_artifact_from_results([_r("detail", "EMPTY"), _r("aggregate", "EMPTY")])
    assert art.status == "EMPTY"


def test_artifact_status_fail_when_no_results():
    art = build_sql_history_artifact_from_results([])
    assert art.status == "FAIL"


def test_artifact_limitations_capture_non_ok_parts():
    results = [
        _r("detail", "OK", rows=[{"id": 1}]),
        _r("aggregate", "EMPTY"),
        _r("detail", "BLOCKED", error_message="policy"),
    ]
    art = build_sql_history_artifact_from_results(results)
    assert art.status == "OK"
    joined = " ".join(art.limitations)
    # non-OK parts are summarized into limitations.
    assert "aggregate:EMPTY" in joined
    assert "detail:BLOCKED" in joined
    # error_message aggregates per-result errors.
    assert art.error_message and "policy" in art.error_message


def test_artifact_status_blocked_when_blocked_and_no_ok():
    art = build_sql_history_artifact_from_results([_r("detail", "BLOCKED"), _r("aggregate", "EMPTY")])
    assert art.status == "BLOCKED"
