# BigQuery Agent Continuation Guide

Date: 2026-05-03

This file is the practical handoff and execution checklist for the current FSBQ architecture.

## 1) Structure Verification Snapshot

Verified against code in:

- app/bigquery_agent/agent.py
- app/bigquery_agent/prompts.py
- app/bigquery_agent/sub_agents/bigquery/agent.py
- app/bigquery_agent/sub_agents/bigquery/tools.py
- tests/test_hitl_workflow.py

### Current (implemented in code)

- bq_root_agent orchestrates SQL workflow via sub-agent transfers
- Dynamic schema injection uses get_instruction_with_schema on each turn
- sql_plan_generator and sql_executor are separate LlmAgent units
- SQL execution path stores execution rows into state for analytics use
- Tool auto-chaining is disabled for HITL agents
- Schema formatting includes strict table boundary warnings
- BQML is opt-in through `ENABLE_BQML=true`; the public Cloud Run demo keeps it disabled

### Yet to implement / stabilize

- Full HITL suite stabilization across provider/ADK version drift (TC1-TC5 consistency)
- Observability configuration hardening for local runs (reduce noisy OTEL export errors)

## 2) Implemented in this update

- Fixed Makefile playground log path and ensured log directory creation.
- Wired SQL approval UI into chat rendering.
- Fixed SQL approval detection export naming and parser usage.
- Added GitHub Actions HITL workflow with TC3 critical gate + full HITL suite.
- Added fail-closed SQL executor behavior for missing approved SQL in state.
- Added integration-style state transition assertions in tests/test_hitl_workflow.py.
- Migrated HITL tests to current ADK InMemoryRunner API (deprecated Session/run_async path removed).
- Restored `make test-tc3` to passing with fail-closed compatible assertions.

## 3) Backward Compatibility Requirements

Do not break these contracts unless you ship a migration:

- Agent names:
	- bq_root_agent
	- sql_plan_generator
	- sql_executor
	- bqml_agent
- Session keys:
	- database_settings
	- generated_sql_plan
	- execution_result
	- bigquery_query_result
	- nl2sql_prompt
- User interaction contract:
	- yes means execute approved SQL
	- no means cancel current SQL execution

## 4) Fail-Closed Requirements

The system must refuse risky behavior by default:

- Never execute SQL before explicit user approval
- Never auto-regenerate and auto-execute after a failed query
- Keep SQL executor read-only for non-BQML workflows
- Keep automatic function calling disabled on HITL agents
- If approved SQL is missing from state, stop and return a clear error

## 5) Continuation Notes

- CONTINUATION_README.md for next work and risk controls

## 6) Recommended Next Steps

1. Build a clean demo artifact set: record one 60-90s walkthrough video and capture 3 screenshots (approval prompt, rejection path, approved execution result).
2. Add one negative integration test for missing `generated_sql_plan` to verify explicit blocked response in executor.
3. Add CI status badge and explicit test policy section in README (what must pass before merge).
4. Run reproducibility check from clean machine state: `make clean-env && make install && make dev && make test-tc3`.
5. Add a short "Lessons Learned" section in README highlighting safety failures found and fixed.

## 7) Data Gaps To Close Before Portfolio Submission

1. Baseline metrics are missing: no fixed pass-rate snapshot for TC1-TC5 across at least 3 consecutive CI runs.
2. Runtime evidence is incomplete: no saved artifact showing a full fail-closed error cycle (query -> approval -> execution error -> stop).
3. Cost/perf envelope is undocumented: average latency and token usage by step are not summarized for evaluators.
4. Reliability matrix is incomplete: behavior under missing env vars, empty schema, and transient BigQuery errors is not tabulated in README.

## 8) Quick Validation Commands

```bash
uv sync
make playground
pytest tests/test_hitl_workflow.py -v
```

For critical verification, run TC3 first:

```bash
pytest tests/test_hitl_workflow.py::TestHITLWorkflow::test_tc3_error_recovery_no_bypass -v
```

Preferred local verification flow (portfolio path):

```bash
make clean-env
make install
make dev
make test-tc3
make portfolio-check
```
