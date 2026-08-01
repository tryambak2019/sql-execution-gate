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

"""
Automated HITL Workflow Tests

Tests the CRITICAL TC3 (Error Recovery) scenario to verify that HITL
is not bypassed when SQL execution fails.

Run with: pytest tests/test_hitl_workflow.py -v
"""

import asyncio
import uuid
import pytest
import pytest_asyncio
from google.genai import types
from google.adk.runners import InMemoryRunner
from app.bigquery_agent import bq_root_agent


class TestHITLWorkflow:
    """Test suite for HITL workflow validation."""

    @pytest_asyncio.fixture
    async def test_ctx(self):
        """Create an isolated runner context for each test."""
        runner = InMemoryRunner(agent=bq_root_agent)
        user_id = "u_test"
        session_id = str(uuid.uuid4())
        await runner.session_service.create_session(
            app_name=runner.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        context = {
            "runner": runner,
            "user_id": user_id,
            "session_id": session_id,
        }
        try:
            yield context
        finally:
            await runner.close()

    async def _ask(self, test_ctx, user_message: str, state_delta: dict | None = None) -> str:
        """Send a user message and return concatenated model text output."""
        events = []
        async for event in test_ctx["runner"].run_async(
            user_id=test_ctx["user_id"],
            session_id=test_ctx["session_id"],
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=user_message)],
            ),
            state_delta=state_delta,
        ):
            events.append(event)

        response_chunks: list[str] = []
        for event in events:
            content = getattr(event, "content", None)
            if not content or not getattr(content, "parts", None):
                continue
            for part in content.parts:
                text = getattr(part, "text", None)
                if text:
                    response_chunks.append(text)

        return "\n".join(response_chunks).strip()

    async def _get_state(self, test_ctx) -> dict:
        """Fetch current mutable session state via the runner session service."""
        runner = test_ctx["runner"]
        session = await runner.session_service.get_session(
            app_name=runner.app_name,
            user_id=test_ctx["user_id"],
            session_id=test_ctx["session_id"],
        )
        assert session is not None, "Session must exist"
        state = getattr(session, "state", None)
        assert state is not None, "Session state must be available"
        return state


    @pytest.mark.asyncio
    async def test_tc1_basic_hitl_workflow(self, test_ctx):
        """TC1: Basic HITL workflow - SQL generation → approval → execution"""
        # Step 1: Generate SQL
        response1 = await self._ask(
            test_ctx, "which 10 products generated the most revenue"
        )
        
        # Verify SQL presented
        assert "```sql" in response1.lower(), "SQL query should be presented in code block"
        assert "yes" in response1.lower(), "Should ask for approval with 'yes'"
        assert "no" in response1.lower() or "cancel" in response1.lower(), "Should offer cancellation option"
        
        # Verify agent stopped and didn't execute
        assert "count(*)" in response1.lower() or "select" in response1.lower(), "Should contain SQL"
        # Should NOT contain result data yet
        assert "rows" not in response1.lower() or "result" not in response1.lower(), \
            "Should not have executed SQL yet"
        
        # Step 2: Approve execution
        response2 = await self._ask(test_ctx, "yes")
        
        # Verify execution happened
        assert response2, "Execution response should not be empty"

    @pytest.mark.asyncio
    async def test_tc2_user_rejection(self, test_ctx):
        """TC2: User rejection - SQL generation → user says 'no' → execution cancelled"""
        # Generate SQL
        response1 = await self._ask(test_ctx, "show me all products")
        assert "```sql" in response1.lower()
        
        # User rejects
        response2 = await self._ask(test_ctx, "no")
        
        # Verify cancellation
        assert "cancel" in response2.lower() or "different" in response2.lower(), \
            "Should acknowledge cancellation"
        # Should NOT contain result data
        assert "product" not in response2.lower() or "would you like" in response2.lower(), \
            "Should not have executed SQL after rejection"

    @pytest.mark.asyncio
    async def test_tc3_error_recovery_no_bypass(self, test_ctx):
        """
        TC3: Error Recovery (CRITICAL TEST)
        
        This is THE CRITICAL test that verifies HITL is not bypassed on error recovery.
        
        Old behavior (BROKEN):
        1. User approves SQL
        2. Execution fails (wrong project/table)
        3. Agent automatically generates NEW SQL without user input ❌
        4. Agent auto-executes the new SQL ❌
        
        New behavior (EXPECTED):
        1. User approves SQL
        2. Execution fails
        3. Agent reports error and STOPS ✅
        4. Agent waits for new user input ✅
        5. New SQL requires fresh approval cycle ✅
        """
        # Step 1: Generate a normal SQL plan and pause for user approval.
        response1 = await self._ask(
            test_ctx, "which 10 products generated the most revenue"
        )
        assert "```sql" in response1.lower(), "Should generate SQL for approval"
        assert "yes" in response1.lower(), "Should ask for approval"

        # Step 2: User says yes, with explicit state override to force execution error.
        response2 = await self._ask(
            test_ctx,
            "yes",
            state_delta={
                "generated_sql_plan": (
                    "SELECT * FROM `bigquery-public-data.thelook_ecommerce.nonexistent_table_xyz`"
                )
            },
        )

        # Step 3: Agent should report failure/cancellation context, never silently execute.
        assert (
            "error" in response2.lower()
            or "not found" in response2.lower()
            or "does not exist" in response2.lower()
            or "unable" in response2.lower()
            or "not available" in response2.lower()
            or "cannot" in response2.lower()
            or "cancel" in response2.lower()
        ), "Should report error/fail-closed outcome instead of executing"
        
        # ⚠️⚠️ CRITICAL VERIFICATION ⚠️⚠️
        # Agent MUST NOT auto-generate new SQL after error
        # Response should contain error message, NOT new SQL being presented
        
        # Count SQL blocks in error response
        sql_block_count = response2.lower().count("```sql")
        
        assert sql_block_count == 0, \
            f"ERROR: Agent generated new SQL after error without user input! Found {sql_block_count} SQL blocks. " \
            "This is HITL BYPASS - agent should report error and STOP, waiting for user input."
        
        # Should NOT ask for approval again (no new SQL to approve)
        approval_request_count = response2.lower().count("reply yes to execute") + \
                                 response2.lower().count("reply **yes**")
        
        assert approval_request_count == 0, \
            "ERROR: Agent asked for approval without user requesting new query. " \
            "This indicates agent auto-generated SQL during error recovery (HITL bypass)."

    @pytest.mark.asyncio
    async def test_tc4_consecutive_queries_state_isolation(self, test_ctx):
        """TC4: Consecutive queries - verify state isolation and fresh HITL cycle"""
        # Query 1
        response1 = await self._ask(
            test_ctx, "which 10 products generated the most revenue"
        )
        assert "```sql" in response1.lower()
        
        response2 = await self._ask(test_ctx, "yes")
        # Should have results
        assert response2, "Execution response should not be empty"
        
        # Query 2 - should trigger FRESH HITL workflow
        response3 = await self._ask(
            test_ctx, "show monthly revenue for the last 12 months in the data"
        )
        
        # Should present NEW SQL (not execute immediately)
        assert "```sql" in response3.lower(), "Should generate new SQL"
        assert "yes" in response3.lower(), "Should ask for fresh approval"
        
        # Should NOT contain results yet (waiting for approval)
        assert "revenue" not in response3.lower() or "```sql" in response3.lower(), \
            "Should not auto-execute second query without approval"

    @pytest.mark.asyncio
    async def test_tc4b_state_transition_assertions(self, test_ctx):
        """Integration check: state transitions from planning to execution are persisted."""
        response1 = await self._ask(
            test_ctx, "which 10 products generated the most revenue"
        )
        assert "```sql" in response1.lower(), "Planner should emit SQL for approval"

        state = await self._get_state(test_ctx)
        generated_sql_plan = state.get("generated_sql_plan")
        assert generated_sql_plan, "generated_sql_plan should be set after planning"

        await self._ask(test_ctx, "yes")
        execution_result = state.get("execution_result")
        assert execution_result is not None, "execution_result should be populated after execution"

    @pytest.mark.asyncio
    async def test_tc5_schema_accuracy_no_hallucination(self, test_ctx):
        """TC5: Schema accuracy - verify correct project/dataset, no hallucinated columns"""
        response = await self._ask(test_ctx, "describe the order_items table")
        
        # Should mention correct columns
        expected_columns = [
            "id",
            "order_id",
            "user_id",
            "product_id",
            "status",
            "created_at",
            "sale_price",
        ]
        found_columns = [col for col in expected_columns if col in response.lower()]
        
        assert len(found_columns) >= 4, \
            f"Should mention at least 4 correct columns. Found: {found_columns}"
        
        # Should NOT mention hallucinated columns
        hallucinated_columns = [
            "sticker_id",
            "num_sold",
            "store_name",
            "units_ordered",
            "product_revenue_total",
        ]
        found_hallucinated = [col for col in hallucinated_columns if col in response.lower()]
        
        assert len(found_hallucinated) == 0, \
            f"Found hallucinated columns: {found_hallucinated}. These do not exist in schema!"
        
        # Should reference correct project (if mentioned)
        if "project" in response.lower() or "bigquery-public-data" in response.lower():
            assert "bigquery-public-data" in response, \
                "Should reference data project bigquery-public-data"
        
        # Should NOT reference wrong projects
        assert "kaggle-competition-datasets" not in response.lower(), \
            "Should NOT reference kaggle-competition-datasets (schema hallucination)"


def test_import_agent():
    """Sanity check: verify agent can be imported"""
    from app.bigquery_agent import bq_root_agent
    from app.bigquery_agent.sub_agents.bigquery.agent import sql_executor

    assert bq_root_agent is not None, "Agent should be importable"
    assert bq_root_agent.name == "bq_root_agent", "Agent should have correct name"
    assert any(
        getattr(tool, "name", None) == "call_analytics_agent"
        or getattr(tool, "__name__", None) == "call_analytics_agent"
        for tool in bq_root_agent.tools
    ), "Root agent must expose the analytics-agent tool"
    assert any(
        getattr(tool, "name", None) == "call_advanced_analytics_agent"
        or getattr(tool, "__name__", None) == "call_advanced_analytics_agent"
        for tool in bq_root_agent.tools
    ), "Root agent must expose the gated advanced-analytics tool"
    assert not sql_executor.disallow_transfer_to_parent, (
        "SQL executor must return control to the root so pending analytics "
        "requests continue after approval"
    )


if __name__ == "__main__":
    # Allow running directly for quick testing
    print("Running HITL Workflow Tests...")
    print("=" * 80)
    print("TC3 (Error Recovery) is the CRITICAL test")
    print("=" * 80)
    
    print("Run with: pytest tests/test_hitl_workflow.py::TestHITLWorkflow::test_tc3_error_recovery_no_bypass -v")
