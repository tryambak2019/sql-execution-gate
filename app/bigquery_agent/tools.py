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

"""Tools for the ADK Sampmles BigQuery Data Science Agent."""

import asyncio
import logging

from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from google.auth import default
from google.cloud import bigquery as bq


from .sub_agents import advanced_analytics_agent, analytics_agent
from .visualization import is_allowed_executor_reason, sanitize_analytics_output

logger = logging.getLogger(__name__)

# ============================================================================
# SQL planning/execution is handled by dedicated sub-agents.
# This module only exposes analytics tool integration for the root agent.
# ============================================================================


async def call_analytics_agent(
    question: str,
    tool_context: ToolContext,
):
    """
    This tool visualizes and analyzes an already-retrieved dataset.

    It returns Vega-Lite for ordinary charts. It may use its sandboxed Python
    executor only for allowlisted advanced computation, including:
    * Statistical tests, forecasting, clustering, optimization, or simulation;
    * Processing or filtering existing datasets;
    * Combining datasets to create a joined dataset for further analysis.

    The Python modules available to it are:
    * io
    * math
    * re
    * matplotlib.pyplot (specialized visualization fallback only)
    * numpy
    * pandas

    The tool DOES NOT have the ability to retrieve additional data from
    a database. Only the data already retrieved will be analyzed.

    Args:
        question (str): Natural language question or analytics request.
        tool_context (ToolContext): The tool context to use for generating the
            SQL query.

    Returns:
        Response from the analytics agent.

    """
    logger.debug("call_analytics_agent: %s", question)

    # if question == "N/A":
    #    return tool_context.state["db_agent_output"]

    bigquery_data = ""

    if "bigquery_query_result" in tool_context.state:
        bigquery_data = tool_context.state["bigquery_query_result"]

    question_with_data = f"""
  Question to answer: {question}

  Actual data to analyze this question is available in the following data
  tables:

  <BIGQUERY>
  {bigquery_data}
  </BIGQUERY>

  """

    agent_tool = AgentTool(agent=analytics_agent)

    analytics_agent_output = await agent_tool.run_async(
        args={"request": question_with_data}, tool_context=tool_context
    )
    safe_output = sanitize_analytics_output(analytics_agent_output)
    tool_context.state["analytics_agent_output"] = safe_output
    return safe_output


async def call_advanced_analytics_agent(
    question: str,
    reason_code: str,
    tool_context: ToolContext,
):
    """Run gated Python analysis for a recognized computational requirement.

    Args:
        question: The advanced analysis requested by the user.
        reason_code: One of statistical_test, forecasting, clustering,
            optimization, simulation, unsupported_transform, or
            specialized_visualization.
        tool_context: Current session context containing approved query results.
    """
    if not is_allowed_executor_reason(reason_code):
        return (
            "Code execution denied: use call_analytics_agent for ordinary charts "
            "or provide a recognized advanced-computation reason."
        )

    bigquery_data = tool_context.state.get("bigquery_query_result", "")
    request = f"""
  Validated executor reason: {reason_code}
  Question to answer: {question}

  <BIGQUERY>
  {bigquery_data}
  </BIGQUERY>
  """
    agent_tool = AgentTool(agent=advanced_analytics_agent)
    output = await agent_tool.run_async(
        args={"request": request}, tool_context=tool_context
    )
    safe_output = sanitize_analytics_output(output)
    tool_context.state["analytics_agent_output"] = safe_output
    return safe_output
