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

"""Database Agent: get data from database (BigQuery) using NL2SQL."""

import logging
import os
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import BaseTool, ToolContext
from google.adk.tools.bigquery import BigQueryToolset
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from google.genai import types

from ....utils.utils import USER_AGENT, get_env_var
from ...sql_gate import SqlGateBlocked, dry_run_sql, get_maximum_bytes_billed
from . import tools
from .chase_sql import chase_db_tools
from .prompts import return_instructions_bigquery

logger = logging.getLogger(__name__)

NL2SQL_METHOD = os.getenv("NL2SQL_METHOD", "BASELINE")

# BigQuery built-in tools in ADK
# https://google.github.io/adk-docs/tools/built-in-tools/#bigquery
ADK_BUILTIN_BQ_EXECUTE_SQL_TOOL = "execute_sql"


def setup_before_agent_call(callback_context: CallbackContext) -> None:
    """Setup the agent — idempotent, loads database settings and injects schema once."""
    if "database_settings" not in callback_context.state:
        # Keep the canonical nested state shape expected across BQ tools.
        callback_context.state["database_settings"] = {
            "bigquery": tools.get_database_settings()
        }

    db_settings = callback_context.state.get("database_settings", {})

    # Backward compatibility: support both legacy flat shape
    # {"schema": ...} and canonical nested shape {"bigquery": {"schema": ...}}.
    if isinstance(db_settings, dict) and "bigquery" in db_settings:
        bq_settings = db_settings.get("bigquery", {})
    else:
        bq_settings = db_settings
        if isinstance(bq_settings, dict):
            callback_context.state["database_settings"] = {"bigquery": bq_settings}

    if not isinstance(bq_settings, dict) or "schema" not in bq_settings:
        logger.error("database_settings missing schema; SQL generation will be blocked")
        schema = {}
    else:
        schema = bq_settings.get("schema", {})

    # Inject schema into the agent instruction so it can generate SQL without
    # making a redundant extra LLM call inside a tool.
    agent = callback_context._invocation_context.agent
    if "{schema}" in agent.instruction:
        # Format schema in human-readable form instead of raw Python dict
        formatted_schema = tools.format_schema_for_llm(schema)
        agent.instruction = agent.instruction.replace("{schema}", formatted_schema)


def inject_sql_into_executor(callback_context: CallbackContext) -> None:
    """Inject the approved SQL from state into sql_executor's instruction."""
    logger.info("🔧 inject_sql_into_executor callback STARTED")
    agent = callback_context._invocation_context.agent
    
    # Debug: log all state keys
    state_keys = [k for k in dir(callback_context.state) if not k.startswith('_')]
    logger.info("🔧 State attributes available: %s", state_keys)
    
    # Get the SQL from state (stored by sql_plan_generator)
    sql_in_state = callback_context.state.get("generated_sql_plan", "")

    # Reset instruction from template every run to avoid instruction accumulation.
    agent.instruction = SQL_EXECUTOR_INSTRUCTION_TEMPLATE.replace(
        "{compute_project_id}", get_env_var("BQ_COMPUTE_PROJECT_ID")
    )

    if sql_in_state:
        logger.info("🚨 SQL FOUND in state! Length: %d chars", len(sql_in_state))
        logger.info("🚨 Full SQL content from state: %s", sql_in_state)
        
        # Extract just the SQL from ```sql``` blocks if present
        import re
        sql_match = re.search(r'```sql\s*\n(.*?)\n```', sql_in_state, re.DOTALL)
        if sql_match:
            clean_sql = sql_match.group(1).strip()
            logger.info("🚨 Extracted clean SQL: %s", clean_sql)
        else:
            clean_sql = sql_in_state
            logger.warning("🚨 No SQL code block found, using full state content")
        
        # Inject approved SQL into instruction template.
        agent.instruction = agent.instruction.replace("{approved_sql}", clean_sql)
        logger.info("🚨 SQL successfully injected into executor instruction")
    else:
        logger.error("🚨 ERROR: No SQL found in state['generated_sql_plan']!")
        logger.error("🚨 This means sql_plan_generator didn't store the SQL properly")
        # Fail closed: explicitly force cancellation behavior when no approved SQL exists.
        callback_context.state["sql_execution_blocked"] = True
        callback_context.state["sql_execution_blocked_reason"] = (
            "No approved SQL found in session state."
        )
        agent.instruction = agent.instruction.replace("{approved_sql}", "")


def store_results_in_context(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> dict | None:
    """Persist SQL query results into state for downstream agents."""
    logger.info("🔧 store_results_in_context callback triggered for tool: %s", tool.name)
    logger.info("🔧 Tool args: %s", args)

    # ADK invokes an agent's after-tool callback for built-in control tools too.
    # transfer_to_agent legitimately returns None, so only inspect the response
    # contract for the BigQuery tool this callback is responsible for.
    if tool.name != ADK_BUILTIN_BQ_EXECUTE_SQL_TOOL:
        return None

    if not isinstance(tool_response, dict):
        logger.error("❌ execute_sql returned an unexpected response: %r", tool_response)
        return None

    logger.info("🔧 Tool response status: %s", tool_response.get("status"))
    logger.info("🔧 execute_sql tool was called!")
    tool_context.state["execution_result"] = tool_response
    if tool_response.get("status") == "SUCCESS":
        rows = tool_response.get("rows", [])
        tool_context.state["bigquery_query_result"] = rows
        logger.info("✅ SUCCESS: Stored %d rows in bigquery_query_result", len(rows))
        logger.info("✅ Sample rows: %s", rows[:3] if rows else 'None')
    else:
        logger.error("❌ execute_sql FAILED with status: %s", tool_response.get("status"))
        logger.error("❌ Error details: %s", tool_response.get("error"))
        logger.error("❌ Full response: %s", tool_response)
    return None


def enforce_compute_project(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict | None:
    """Revalidate approved SQL and force the configured billing project."""
    if tool.name == ADK_BUILTIN_BQ_EXECUTE_SQL_TOOL:
        try:
            review = dry_run_sql(args.get("query", ""))
        except Exception as exc:
            reason = str(exc) or "Query preflight failed."
            tool_context.state["sql_execution_blocked"] = True
            tool_context.state["sql_execution_blocked_reason"] = reason
            return {
                "status": "ERROR",
                "error": f"Blocked: {reason} No BigQuery query job was submitted.",
            }
        tool_context.state["sql_review"] = {
            "sql_fingerprint": review.sql_fingerprint,
            "referenced_tables": list(review.referenced_tables),
            "estimated_bytes": review.estimated_bytes,
            "maximum_bytes_billed": review.maximum_bytes_billed,
        }
        args["project_id"] = get_env_var("BQ_COMPUTE_PROJECT_ID")
    return None


# Configure BigQueryToolset with WriteMode.BLOCKED (read-only SQL execution)
# NOTE: This is used ONLY for sql_executor - sql_plan_generator doesn't need any BQ tools
bigquery_tool_filter = [ADK_BUILTIN_BQ_EXECUTE_SQL_TOOL]
bigquery_tool_config = BigQueryToolConfig(
    write_mode=WriteMode.BLOCKED,  # No CREATE/INSERT/UPDATE/DELETE allowed
    application_name=USER_AGENT,
    compute_project_id=get_env_var("BQ_COMPUTE_PROJECT_ID"),  # Billing project
    location=os.getenv("BQ_LOCATION") or None,
    maximum_bytes_billed=get_maximum_bytes_billed(),
)

# Create BigQueryToolset - only accepts tool_filter and bigquery_tool_config
bigquery_toolset = BigQueryToolset(
    tool_filter=bigquery_tool_filter, 
    bigquery_tool_config=bigquery_tool_config
)

# ============================================================================
# HYBRID HITL APPROACH: Pattern #1 + Native ToolConfirmation
# ============================================================================
# Layer 1: Architectural enforcement (Pattern #1)
#   - SQL generation and execution in separate agents (spatial tool separation)
#   - LLM must explicitly transfer between agents
#   - Cannot auto-chain across conversation turns
#
# Layer 2: Framework-native safety net (ADK ToolConfirmation)
#   - Even if LLM somehow bypasses transfer logic, tool-level confirmation blocks execution
#   - Native ADK feature, more reliable than instruction-based HITL
#   - User gets explicit confirmation dialog before SQL runs
# ============================================================================

# BASELINE: no tool needed — agent generates SQL directly from its instruction.
# CHASE: still uses initial_bq_nl2sql tool for multi-step candidate generation.
_generate_sql_tools = (
    [chase_db_tools.initial_bq_nl2sql] if NL2SQL_METHOD == "CHASE" else []
)

# ═══════════════════════════════════════════════════════════════════════════
# HITL ARCHITECTURE
# Pattern: transfer to sql_plan_generator -> user approves -> transfer to sql_executor
# SQL generation and SQL execution are isolated into separate sub-agents.
# ═══════════════════════════════════════════════════════════════════════════

# AGENT 1: Generate SQL plan and present for approval
sql_plan_generator = LlmAgent(
    # SQL planning is the highest-value reasoning step. Keep its model
    # independently configurable so execution and presentation can remain on
    # a faster, stable model.
    model=os.getenv(
        "BIGQUERY_PLANNER_MODEL", os.getenv("BIGQUERY_AGENT_MODEL", "")
    ),
    name="sql_plan_generator",
    description="Generates SQL query from natural language and presents it for user approval. Does NOT execute.",
    instruction=return_instructions_bigquery() + """
    
    ════════════════════════════════════════════════════════════════════════════
    CRITICAL: YOU ARE THE PLANNING STAGE — YOU NEVER EXECUTE SQL
    ════════════════════════════════════════════════════════════════════════════
    
    Your ONLY job is to:
    1. Generate the SQL query that answers the user's question USING ONLY THE TABLES IN THE SCHEMA ABOVE
    2. Present it clearly with explanation
    3. STOP and wait for user approval
    
    ⚠️ CRITICAL TABLE NAMING RULES ⚠️
    - You MUST use ONLY the exact fully-qualified table names from the schema above
    - Table names are in the format: `project_id.dataset_id.table_name`
    - DO NOT invent, guess, or hallucinate any table names
    - DO NOT use tables from other projects like cortex-data-foundation or any other project
    - If you cannot find the right table in the schema, say so explicitly
    
    DO NOT call execute_sql. DO NOT run the query. DO NOT say "would you like me to execute".
    
    OUTPUT FORMAT:
    I've generated this SQL query to answer your question:
    
    ```sql
    <your SQL here>
    ```
    
    **Explanation:** <brief explanation of what this query does>
    
    Reply **yes** to execute this query or **no** to cancel.
    
    ════════════════════════════════════════════════════════════════════════════
    """,
    tools=_generate_sql_tools,
    before_agent_callback=setup_before_agent_call,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.01,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    ),
    output_key="generated_sql_plan",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


SQL_EXECUTOR_INSTRUCTION_TEMPLATE = """
     You are the SQL execution agent. You run AFTER the user has approved the SQL.

     ════════════════════════════════════════════════════════════════════════════
     YOUR WORKFLOW
     ════════════════════════════════════════════════════════════════════════════

     1. Check the user's last message:
         - If it contains "yes" (case-insensitive): proceed to step 2
         - If it contains "no": output "SQL execution cancelled by user." and STOP

     2. Validate approved SQL state:
         - If approved SQL below is empty, output:
            "Execution blocked: no approved SQL found. Please request a new query plan."
            and STOP.

      3. Call execute_sql with:
          - project_id: {compute_project_id}
          - query: the approved SQL EXACTLY as-is (do not modify it)

          The fully-qualified tables may belong to a different data project.
          Never use a table's project as execute_sql.project_id; that argument is
          the compute/billing project shown above.

     4. After execute_sql succeeds, transfer control back to `bq_root_agent`.
        The root agent owns the user's remaining goal, including any requested
        visualization or Python analysis. Do not treat the SQL table as the
        final answer and do not attempt the analysis yourself.

     ════════════════════════════════════════════════════════════════════════════
     APPROVED SQL TO EXECUTE
     ════════════════════════════════════════════════════════════════════════════
     {approved_sql}
     ════════════════════════════════════════════════════════════════════════════

     ABSOLUTE RULES
     ════════════════════════════════════════════════════════════════════════════
     • Only execute if user explicitly said "yes"
     • Execute EXACTLY the approved SQL shown above
     • If approved SQL is missing/empty, block execution and ask for replanning
     • After successful execution, transfer back to `bq_root_agent`
     • On execution failure, report the error and STOP; never create new SQL
     ════════════════════════════════════════════════════════════════════════════
"""

# AGENT 2: Execute approved SQL
sql_executor = LlmAgent(
    name="sql_executor",
    model=os.getenv("BIGQUERY_AGENT_MODEL", ""),
    description="Executes pre-approved SQL from session state. Only invoked after explicit user confirmation.",
     instruction=SQL_EXECUTOR_INSTRUCTION_TEMPLATE,
    tools=[bigquery_toolset],  # Native ADK toolset with execute_sql
    before_agent_callback=inject_sql_into_executor,
    before_tool_callback=enforce_compute_project,
    after_tool_callback=store_results_in_context,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.01,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    ),
    output_key="execution_result",
    # The root must regain control to invoke call_analytics_agent when the
    # original user request included visualization or Python analysis.
    disallow_transfer_to_parent=False,
    disallow_transfer_to_peers=True,
)
