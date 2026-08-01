"""Deterministic policy checks for generated BigQuery SQL."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from google.cloud import bigquery
from sqlglot import exp, parse
from sqlglot.errors import ParseError

DEFAULT_MAXIMUM_BYTES_BILLED = 1_000_000_000
_WRITE_INTENT = re.compile(
    r"^\s*(?:please\s+)?(?:delete|drop|truncate|update|insert|merge|create|alter)\b",
    re.IGNORECASE,
)


class SqlGateBlocked(ValueError):
    """Raised when SQL cannot pass the read-only execution policy."""


@dataclass(frozen=True)
class SqlReview:
    """Inspectable evidence produced before a query can be approved."""

    sql_fingerprint: str
    referenced_tables: tuple[str, ...]
    estimated_bytes: int | None = None
    maximum_bytes_billed: int = DEFAULT_MAXIMUM_BYTES_BILLED


def get_maximum_bytes_billed() -> int:
    """Return the configured per-query byte ceiling."""
    raw_value = os.getenv(
        "BQ_MAXIMUM_BYTES_BILLED", str(DEFAULT_MAXIMUM_BYTES_BILLED)
    )
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("BQ_MAXIMUM_BYTES_BILLED must be an integer") from exc
    if value <= 0:
        raise RuntimeError("BQ_MAXIMUM_BYTES_BILLED must be greater than zero")
    return value


def write_intent_block_reason(question: str) -> str | None:
    """Block explicit imperative requests to modify data before invoking an agent."""
    if _WRITE_INTENT.search(question):
        return "SQL Execution Gate permits read-only queries only."
    return None


def validate_read_only_sql(sql: str) -> SqlReview:
    """Parse generated SQL and accept exactly one read-only BigQuery query."""
    if not sql.strip():
        raise SqlGateBlocked("No SQL was provided for review.")

    try:
        statements = [
            statement for statement in parse(sql, read="bigquery") if statement
        ]
    except ParseError as exc:
        raise SqlGateBlocked(f"BigQuery SQL could not be parsed: {exc}.") from exc

    if len(statements) != 1:
        raise SqlGateBlocked("Exactly one SQL statement is required.")

    statement = statements[0]
    if not isinstance(statement, exp.Query):
        statement_type = type(statement).__name__.upper()
        raise SqlGateBlocked(
            f"{statement_type} statements are blocked; only read-only queries are allowed."
        )

    cte_names = {
        cte.alias_or_name.casefold()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    tables: set[str] = set()
    for table in statement.find_all(exp.Table):
        if not table.catalog and not table.db and table.name.casefold() in cte_names:
            continue
        if not table.catalog or not table.db:
            raise SqlGateBlocked(
                f"Table {table.sql(dialect='bigquery')} is not fully qualified."
            )
        tables.add(table.sql(dialect="bigquery"))

    return SqlReview(
        sql_fingerprint=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        referenced_tables=tuple(sorted(tables)),
        maximum_bytes_billed=get_maximum_bytes_billed(),
    )


def dry_run_sql(sql: str, client: Any | None = None) -> SqlReview:
    """Validate SQL and ask BigQuery for a non-executing byte estimate."""
    review = validate_read_only_sql(sql)
    query_client = client or bigquery.Client(
        project=os.environ["BQ_COMPUTE_PROJECT_ID"]
    )
    job = query_client.query(
        sql,
        job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False),
        location=os.getenv("BQ_LOCATION") or None,
    )
    estimated_bytes = int(job.total_bytes_processed or 0)
    if estimated_bytes > review.maximum_bytes_billed:
        raise SqlGateBlocked(
            "Estimated bytes scanned "
            f"({estimated_bytes:,}) exceed the configured limit "
            f"({review.maximum_bytes_billed:,})."
        )
    return SqlReview(
        sql_fingerprint=review.sql_fingerprint,
        referenced_tables=review.referenced_tables,
        estimated_bytes=estimated_bytes,
        maximum_bytes_billed=review.maximum_bytes_billed,
    )