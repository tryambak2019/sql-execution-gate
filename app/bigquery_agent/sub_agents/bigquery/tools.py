# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This file contains the tools used by the database agent."""

import asyncio
import datetime
import logging
import os

import numpy as np
import pandas as pd
from google.adk.tools import ToolContext
from google.auth import default
from google.adk.tools.bigquery.client import get_bigquery_client
from google.cloud import bigquery
from google.genai import Client
from google.genai.types import HttpOptions

from ....utils.utils import USER_AGENT, get_env_var

from .chase_sql import chase_constants

logger = logging.getLogger(__name__)

# Assume that `BQ_COMPUTE_PROJECT_ID` and `BQ_DATA_PROJECT_ID` are set in the
# environment. See the `data_agent` README for more details.
def _safe_get_env(name: str):
    try:
        return get_env_var(name)
    except ValueError:
        return None


dataset_id = _safe_get_env("BQ_DATASET_ID")
data_project = _safe_get_env("BQ_DATA_PROJECT_ID")
compute_project = _safe_get_env("BQ_COMPUTE_PROJECT_ID")
vertex_project = _safe_get_env("GOOGLE_CLOUD_PROJECT")
location = _safe_get_env("GOOGLE_CLOUD_LOCATION")
http_options = HttpOptions(headers={"user-agent": USER_AGENT})
if vertex_project and location:
    try:
        llm_client = Client(
            vertexai=True,
            project=vertex_project,
            location=location,
            http_options=http_options,
        )
    except Exception:
        llm_client = None
else:
    llm_client = None

MAX_NUM_ROWS = 10000

    
def _serialize_value_for_sql(value):
    """Serializes a Python value from a pandas DataFrame into a BigQuery SQL literal."""
    if isinstance(value, (list, np.ndarray)):
        # Format arrays.
        return f"[{', '.join(_serialize_value_for_sql(v) for v in value)}]"
    if pd.isna(value):
        return "NULL"
    if isinstance(value, str):
        # Escape single quotes and backslashes for SQL strings.
        # NOTE: This will throw an exception in Python <= 3.11 because
        # Python 3.12 introduces better f-string handling.
        new_value = value.replace("\\", "\\\\").replace("'", "''")
        return f"'{new_value}'"
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", "replace")
        new_value = decoded.replace("\\", "\\\\").replace("'", "''")
        return f"b'{new_value}'"
    if isinstance(value, (datetime.datetime, datetime.date, pd.Timestamp)):
        # Timestamps and datetimes need to be quoted.
        return f"'{value}'"
    if isinstance(value, dict):
        # For STRUCT, BQ expects ('val1', 'val2', ...).
        # The values() order from the dataframe should match the column order.
        string_values = [_serialize_value_for_sql(v) for v in value.values()]
        return f"({', '.join(string_values)})"
    return str(value)


database_settings = None


def get_database_settings():
    """Get database settings."""
    global database_settings
    if database_settings is None:
        database_settings = update_database_settings()
    return database_settings


def update_database_settings():
    """Update database settings."""
    global database_settings
    schema = get_bigquery_schema_and_samples()
    database_settings = {
        "project_id": get_env_var("BQ_COMPUTE_PROJECT_ID"),
        "data_project_id": get_env_var("BQ_DATA_PROJECT_ID"),
        "dataset_id": get_env_var("BQ_DATASET_ID"),
        "schema": schema,
        # Include ChaseSQL-specific constants.
        **chase_constants.chase_sql_constants_dict,
    }
    return database_settings


def get_bigquery_schema_and_samples():
    """Retrieves schema and sample values for the BigQuery dataset tables."""
    client = get_bigquery_client(
        project=compute_project,
        credentials=None,
        user_agent=USER_AGENT,
    )
    dataset_ref = bigquery.DatasetReference(data_project, dataset_id)
    tables_context = {}
    logger.info(f"Loading schema from project: {data_project}, dataset: {dataset_id}")
    
    for table in client.list_tables(dataset_ref):
        table_info = client.get_table(
            bigquery.TableReference(dataset_ref, table.table_id)
        )
        table_schema = [
            (schema_field.name, schema_field.field_type)
            for schema_field in table_info.schema
        ]
        table_ref = dataset_ref.table(table.table_id)
        fully_qualified_name = f"`{data_project}.{dataset_id}.{table.table_id}`"
        
        sample_values = []
        if False:
            sample_query = f"SELECT * FROM {fully_qualified_name} LIMIT 5"
            sample_values = (
                client.query(sample_query).to_dataframe().to_dict(orient="list")
            )
            for key in sample_values:
                sample_values[key] = [
                    _serialize_value_for_sql(v) for v in sample_values[key]
                ]
        
        # Use fully qualified name with backticks as the key
        tables_context[fully_qualified_name] = {
            "table_schema": table_schema,
            "example_values": sample_values,
        }
        logger.info(f"Loaded table: {fully_qualified_name}")

    return tables_context


def format_schema_for_llm(schema: dict) -> str:
    """Format schema dict into human-readable text for LLM consumption.
    
    Args:
        schema: Dict mapping fully-qualified table names to schema info
        
    Returns:
        Formatted multi-line string with clear table definitions
    """
    if not schema:
        return "No tables available."
    
    formatted = []
    formatted.append("=" * 80)
    formatted.append("AVAILABLE TABLES (COMPLETE LIST)")
    formatted.append("=" * 80)
    formatted.append("")
    formatted.append("⚠️  YOU MUST USE ONLY THE TABLES LISTED BELOW")
    formatted.append("⚠️  DO NOT USE ANY OTHER TABLE NAMES")
    formatted.append("⚠️  COPY THE EXACT TABLE NAMES (INCLUDING BACKTICKS)")
    formatted.append("")
    
    for table_name, table_info in schema.items():
        formatted.append(f"TABLE: {table_name}")
        formatted.append("COLUMNS:")
        for col_name, col_type in table_info.get("table_schema", []):
            formatted.append(f"  - {col_name} ({col_type})")
        formatted.append("")
    
    formatted.append("=" * 80)
    formatted.append(f"TOTAL TABLES AVAILABLE: {len(schema)}")
    formatted.append("=" * 80)
    
    return "\n".join(formatted)


def bigquery_nl2sql(
    question: str,
    tool_context: ToolContext,
) -> str:
    """Generates a SQL query from a natural language question.

    Args:
        question (str): Natural language question.
        tool_context (ToolContext): The tool context to use for generating the
            SQL query.

    Returns:
        str: An SQL statement to answer this question.
    """
    logger.debug("bigquery_nl2sql - question: %s", question)

    prompt_template = """
You are a BigQuery SQL expert tasked with generating SQL in the Google SQL
dialect based on the user's natural language question.
Your task is to write a Bigquery SQL query that answers the following question
while using the provided context.

**Guidelines:**

- **Table Referencing:** Always use the full table name with the database prefix
  in the SQL statement.  Tables should be referred to using a fully qualified
  name with enclosed in backticks (`) e.g.
  `project_name.dataset_name.table_name`.  Table names are case sensitive.
- **Joins:** Join as few tables as possible. When joining tables, ensure all
  join columns are the same data type. Analyze the database and the table schema
  provided to understand the relationships between columns and tables.
- **Aggregations:**  Use all non-aggregated columns from the `SELECT` statement
  in the `GROUP BY` clause.
- **SQL Syntax:** Return syntactically and semantically correct SQL for BigQuery
  with proper relation mapping (i.e., project_id, owner, table, and column
  relation). Use SQL `AS` statement to assign a new name temporarily to a table
  column or even a table wherever needed. Always enclose subqueries and union
  queries in parentheses.
- **Column Usage:** Use *ONLY* the column names (column_name) mentioned in the
  Table Schema. Do *NOT* use any other column names. Associate `column_name`
  mentioned in the Table Schema only to the `table_name` specified under Table
  Schema.
- **FILTERS:** You should write query effectively  to reduce and minimize the
  total rows to be returned. For example, you can use filters (like `WHERE`,
  `HAVING`, etc. (like 'COUNT', 'SUM', etc.) in the SQL query.
- **LIMIT ROWS:**  The maximum number of rows returned should be less than
  {MAX_NUM_ROWS}.

**Schema:**

The database structure is defined by the following table schemas (possibly with
sample rows):

```
{SCHEMA}
```

**Natural language question:**

```
{QUESTION}
```

**Think Step-by-Step:** Carefully consider the schema, question, guidelines, and
best practices outlined above to generate the correct BigQuery SQL.

   """

    schema = tool_context.state["database_settings"]["bigquery"]["schema"]

    prompt = prompt_template.format(
        MAX_NUM_ROWS=MAX_NUM_ROWS, SCHEMA=schema, QUESTION=question
    )

    # Return the formatted prompt as context — the calling LlmAgent generates
    # the SQL directly, eliminating the redundant extra LLM call.
    logger.debug("bigquery_nl2sql - returning schema context for agent to generate SQL")
    tool_context.state["nl2sql_prompt"] = prompt
    return prompt
