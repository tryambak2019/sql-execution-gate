"""Executor-free analytics agent used by the deterministic chart endpoint."""

import os

from google.adk.agents import Agent

from .prompts import return_instructions_analytics


visualization_agent = Agent(
    model=os.getenv("ANALYTICS_AGENT_MODEL", "gemini-2.5-flash"),
    name="visualization_agent",
    instruction=return_instructions_analytics(),
)
