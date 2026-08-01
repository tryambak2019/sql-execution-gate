"""Tests for deterministic SQL Execution Gate policy checks."""

from types import SimpleNamespace

import pytest

from app.bigquery_agent.sql_gate import (
    SqlGateBlocked,
    dry_run_sql,
    validate_read_only_sql,
    write_intent_block_reason,
)


def test_select_is_accepted_and_tables_are_extracted(monkeypatch) -> None:
    monkeypatch.setenv("BQ_MAXIMUM_BYTES_BILLED", "1000")
    sql = "SELECT * FROM `bigquery-public-data.thelook_ecommerce.orders`"

    review = validate_read_only_sql(sql)

    assert review.referenced_tables == (
        "`bigquery-public-data.thelook_ecommerce.orders`",
    )
    assert len(review.sql_fingerprint) == 64
    assert review.maximum_bytes_billed == 1000


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM `project.dataset.orders` WHERE TRUE",
        "UPDATE `project.dataset.orders` SET status = 'done' WHERE TRUE",
        "INSERT INTO `project.dataset.orders` (id) VALUES (1)",
        (
            "MERGE `project.dataset.orders` target "
            "USING `project.dataset.source` source ON target.id = source.id "
            "WHEN MATCHED THEN DELETE"
        ),
        "CREATE TABLE `project.dataset.copy` AS SELECT 1 AS id",
    ],
)
def test_write_statement_is_blocked(sql: str) -> None:
    with pytest.raises(SqlGateBlocked, match="only read-only queries"):
        validate_read_only_sql(sql)


def test_multiple_statements_are_blocked() -> None:
    with pytest.raises(SqlGateBlocked, match="Exactly one SQL statement"):
        validate_read_only_sql(
            "SELECT 1; DELETE FROM `bigquery-public-data.thelook_ecommerce.orders`"
        )


def test_unqualified_physical_table_is_blocked() -> None:
    with pytest.raises(SqlGateBlocked, match="not fully qualified"):
        validate_read_only_sql("SELECT * FROM orders")


def test_cte_alias_is_not_treated_as_a_physical_table() -> None:
    review = validate_read_only_sql(
        "WITH recent AS ("
        "SELECT * FROM `bigquery-public-data.thelook_ecommerce.orders`"
        ") SELECT * FROM recent"
    )

    assert review.referenced_tables == (
        "`bigquery-public-data.thelook_ecommerce.orders`",
    )


def test_dry_run_reports_bytes_without_executing(monkeypatch) -> None:
    monkeypatch.setenv("BQ_MAXIMUM_BYTES_BILLED", "1000")
    client = SimpleNamespace(
        query=lambda *_args, **_kwargs: SimpleNamespace(total_bytes_processed=640)
    )

    review = dry_run_sql("SELECT 1", client=client)

    assert review.estimated_bytes == 640


def test_dry_run_rejects_query_above_limit(monkeypatch) -> None:
    monkeypatch.setenv("BQ_MAXIMUM_BYTES_BILLED", "1000")
    client = SimpleNamespace(
        query=lambda *_args, **_kwargs: SimpleNamespace(total_bytes_processed=1001)
    )

    with pytest.raises(SqlGateBlocked, match="exceed the configured limit"):
        dry_run_sql("SELECT 1", client=client)


def test_explicit_destructive_request_is_blocked_before_planning() -> None:
    reason = write_intent_block_reason(
        "Delete all cancelled orders and return the remaining revenue."
    )

    assert reason == "SQL Execution Gate permits read-only queries only."
    assert write_intent_block_reason("Show deleted orders by month") is None