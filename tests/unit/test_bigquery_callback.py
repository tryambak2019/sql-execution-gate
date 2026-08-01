"""Regression tests for BigQuery agent tool callbacks."""

import os
from types import SimpleNamespace

os.environ.setdefault("BQ_COMPUTE_PROJECT_ID", "test-project")
os.environ.setdefault("BQ_DATA_PROJECT_ID", "test-project")
os.environ.setdefault("BQ_DATASET_ID", "test-dataset")

from app.bigquery_agent.sub_agents.bigquery.agent import (  # noqa: E402
    enforce_compute_project,
    store_results_in_context,
)


def test_transfer_to_agent_none_response_is_ignored() -> None:
    """ADK control-tool transfers must not be parsed as SQL responses."""
    context = SimpleNamespace(state={})
    transfer_tool = SimpleNamespace(name="transfer_to_agent")

    result = store_results_in_context(transfer_tool, {}, context, None)

    assert result is None
    assert context.state == {}


def test_execute_sql_success_preserves_rows_and_columns() -> None:
    """Successful SQL results must remain available to the analytics handoff."""
    context = SimpleNamespace(state={})
    execute_tool = SimpleNamespace(name="execute_sql")
    response = {
        "status": "SUCCESS",
        "columns": ["country", "total_sales"],
        "rows": [
            {"country": "Canada", "total_sales": 42},
            {"country": "United States", "total_sales": 37},
        ],
    }

    result = store_results_in_context(execute_tool, {}, context, response)

    assert result is None
    assert context.state["execution_result"] == response
    assert context.state["execution_result"]["columns"] == response["columns"]
    assert context.state["bigquery_query_result"] == response["rows"]


def test_execute_sql_always_uses_compute_project(monkeypatch) -> None:
    """A public table project must never become the query billing project."""
    monkeypatch.setenv("BQ_COMPUTE_PROJECT_ID", "test-project")
    context = SimpleNamespace(state={})
    execute_tool = SimpleNamespace(name="execute_sql")
    args = {
        "project_id": "bigquery-public-data",
        "query": "SELECT * FROM `bigquery-public-data.thelook_ecommerce.order_items`",
    }

    result = enforce_compute_project(execute_tool, args, context)

    assert result is None
    assert args["project_id"] == "test-project"
