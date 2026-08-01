be sparring partner
identify data gaps

# GitHub Copilot Instructions — Agentic HITL Workflows

## Project Context

This repo contains a fullstack ADK agent system with:
- **Search/Research Agent** with plan-approve-execute HITL workflow (tool-based gates)
- **BigQuery Agent** with NL2SQL confirm-before-execute HITL workflow (two-tool pattern)
- **BQML Sub-Agent** with mandatory user approval before model training (instruction-based)

**HITL Architecture Philosophy:**
Both Search and BigQuery agents use **tool-based approval gates** where the root agent explicitly calls separate tools for planning and execution, with user approval required in between. This is more robust than SequentialAgent wrappers which execute all sub-agents automatically.

Core orchestration: `app/agent.py`
BigQuery orchestration: `app/bigquery_agent/agent.py`
Sub-agents: `app/bigquery_agent/sub_agents/`

---

## Folder Structure

```
app/
  agent.py                    # Root orchestration: search + BQ routing
  config.py                   # Model config (worker_model, critic_model, etc.)
  bigquery_agent/
    agent.py                  # bq_root_agent orchestration
    prompts.py                # return_instructions_root() with HITL workflow
    tools.py                  # call_analytics_agent, call_sql_plan_generator, call_sql_executor
    sub_agents/
      analytics/
        agent.py              # analytics_agent (VertexAiCodeExecutor)
        prompts.py            # return_instructions_analytics()
      bigquery/
        agent.py              # sql_plan_generator + sql_executor (two separate LlmAgents)
        prompts.py            # return_instructions_bigquery()
        tools.py              # bigquery_nl2sql, get_bigquery_schema_and_samples, get_database_settings
        chase_sql/            # Chase NL2SQL: dc_prompt_template, qp_prompt_template
      bqml/
        agent.py              # bqml_agent
        prompts.py            # return_instructions_bqml()
        tools.py              # check_bq_models, rag_response
  app_utils/                  # telemetry.py, typing.py, deployment.py
  utils/                      # get_env_var, USER_AGENT constant
```

---

## Agent Definitions — Coding Rules

### General ADK Agent Rules

1. Always use `LlmAgent` for LLM-driven agents; use `Agent` only for code-executor agents.
2. Set `generate_content_config=types.GenerateContentConfig(temperature=0.01)` for deterministic SQL/analytics agents.
3. Use `output_key="<key>"` to pipe agent output into `session.state`.
4. Use `before_agent_callback` to load schema/settings into `callback_context.state` on first run — guard with `if "key" in callback_context.state: return`.
5. Use `after_tool_callback` to persist tool results into state for downstream agents.
6. Always set `disallow_transfer_to_parent=True` and `disallow_transfer_to_peers=True` on leaf agents that must not escalate.

### Tool Registration Rules

- Register each tool **exactly once** per agent.
- Do NOT add the same tool to both `tools=[]` and `sub_agents=[]`.
- `BigQueryToolset` must be added to `tools=[]`, not `sub_agents=[]`.
- `LoopAgent` and `SequentialAgent` go in `sub_agents=[]`.

### Anti-Hallucination Pattern — Schema Formatting

**Problem:** LLMs may hallucinate table names from training data when schema is injected as raw Python dict (via `str(schema)`). This causes queries to reference non-existent tables from other projects.

**Solution:** Always format schema into human-readable text with clear boundaries before injecting into agent instructions.

#### Required Pattern:

```python
# ✅ CORRECT — Human-readable schema formatting
def format_schema_for_llm(schema: dict) -> str:
    """Format schema dict into clear text for LLM."""
    formatted = []
    formatted.append("=" * 80)
    formatted.append("AVAILABLE TABLES (COMPLETE LIST)")
    formatted.append("=" * 80)
    formatted.append("")
    formatted.append("⚠️  YOU MUST USE ONLY THE TABLES LISTED BELOW")
    formatted.append("⚠️  DO NOT USE ANY OTHER TABLE NAMES")
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

# In before_agent_callback:
def setup_before_agent_call(callback_context: CallbackContext):
    schema = callback_context.state["database_settings"]["schema"]
    formatted_schema = format_schema_for_llm(schema)  # ✅ Clear formatting
    agent.instruction = agent.instruction.replace("{schema}", formatted_schema)

# ❌ WRONG — Raw Python dict confuses LLM
def setup_before_agent_call_BAD(callback_context: CallbackContext):
    schema = callback_context.state["database_settings"]["schema"]
    agent.instruction = agent.instruction.replace("{schema}", str(schema))
    # Result: {'`project.dataset.table`': {'table_schema': [...], ...}}
    # LLM interprets this poorly and falls back to training data
```

**Why This Matters:**
- Raw `str(schema)` creates Python dict syntax with quotes, braces, nested structures
- LLMs trained on SQL documentation don't parse Python dicts well in SQL context
- Clear text formatting with visual boundaries (===) and explicit warnings prevents hallucination
- Listing exact table names with column types makes it unambiguous what's available

**Implementation Files:**
- `app/bigquery_agent/sub_agents/bigquery/tools.py` → `format_schema_for_llm()`
- `app/bigquery_agent/sub_agents/bigquery/agent.py` → `setup_before_agent_call()` uses formatted schema
- `app/bigquery_agent/agent.py` → `get_dataset_definitions_for_instructions()` uses formatted schema

### BigQueryToolConfig Valid Parameters

`BigQueryToolConfig` uses Pydantic with `extra='forbid'` — only these fields are allowed:

```python
from google.adk.tools.bigquery import BigQueryToolConfig, WriteMode

# ✅ CORRECT Configuration
bigquery_tool_config = BigQueryToolConfig(
    write_mode=WriteMode.BLOCKED,           # BLOCKED (read-only), PROTECTED (temp tables), ALLOWED (all writes)
    application_name="my-agent",             # Optional: app name for tracking/monitoring
    compute_project_id="my-project",         # Optional: billing project for queries
    location="US",                           # Optional: BigQuery location (e.g., "US", "us-central1")
    maximum_bytes_billed=10485760,          # Optional: max bytes to bill (≥10MB)
    max_query_result_rows=50,               # Default: 50 rows max in query results
    job_labels={"env": "prod"},             # Optional: labels for BigQuery jobs
)

# ❌ INVALID — dataset_id does not exist
bigquery_tool_config = BigQueryToolConfig(
    dataset_id="my_dataset"  # ❌ Pydantic ValidationError: Extra inputs are not permitted
)
```

**Dataset Context:** Specify dataset via **fully-qualified table names** in SQL queries:
```sql
SELECT * FROM `project-id.dataset_id.table_name`
```

The schema loading callback already injects fully-qualified names into agent instructions.

### Agent Registration Pattern for bq_root_agent

```python
# CORRECT — two-tool HITL pattern with BQML sub-agent
tools = [
    call_analytics_agent, 
    call_sql_plan_generator,  # ✅ Step 1: Generate SQL, present to user
    call_sql_executor,        # ✅ Step 2: Execute after user says "yes"
]
sub_agents = [bqml_agent]  # ✅ BQML registered as sub-agent for LLM to transfer to

bq_root_agent = LlmAgent(
    model=os.getenv("ROOT_AGENT_MODEL", "gemini-2.5-flash"),
    name="bq_root_agent",
    instruction=return_instructions_root() + get_dataset_definitions_for_instructions(),
    sub_agents=sub_agents,
    tools=tools,
    before_agent_callback=load_database_settings_in_context,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.01,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    ),
)

# WRONG — wrapping both tools in SequentialAgent (bypasses user approval)
sql_workflow = SequentialAgent(sub_agents=[sql_plan_generator, sql_executor])  # ❌
# This executes both automatically without waiting for user input!

# WRONG — missing sql_executor tool
tools = [call_sql_plan_generator]  # ❌ No way to execute after approval!
```

**Routing Rules:**
- **Tools** (`call_sql_plan_generator`, `call_sql_executor`) → wrap standalone agents, called explicitly by bq_root_agent in sequence with user approval between
- **Sub-agents** (`bqml_agent`) → LLM transfers to them based on intent (BQML keywords)

---

## HITL Patterns — BigQuery Agent

### Pattern: Two-Tool Approval Architecture

The BigQuery agent uses a **tool-based HITL pattern** matching the search agent's plan-approve-execute workflow:

**SQL HITL Workflow:**
1. `call_sql_plan_generator` tool → generates SQL, stores in state, returns to bq_root_agent
2. bq_root_agent presents SQL to user → **STOPS and waits**
3. User replies "yes" → bq_root_agent calls `call_sql_executor` tool
4. `call_sql_executor` reads approved SQL from state → executes → returns results

**BQML HITL Workflow:**
- bqml_agent (sub-agent) uses **instruction-based approval** for CREATE MODEL statements
- Less robust than tool-based, but acceptable for specialized BQML tasks

**Why This Pattern Works:**
- bq_root_agent controls the workflow explicitly via tool calls
- No automatic chaining (SequentialAgent would execute both steps without waiting)
- Each tool call requires explicit LLM decision, creating natural approval gate

```
bq_root_agent  (LlmAgent)
│   model: ROOT_AGENT_MODEL
│   tools: [call_sql_plan_generator, call_sql_executor, call_analytics_agent]
│   sub_agents: [bqml_agent]
│   before_agent_callback: load_database_settings_in_context
│   AFC: DISABLED (prevents automatic tool chaining)
│
│   ROUTING LOGIC:
│   ├─ User asks SQL question → call_sql_plan_generator → [WAIT for "yes"] → call_sql_executor
│   └─ User asks BQML question → transfer to bqml_agent sub-agent
│
├── sql_plan_generator  (LlmAgent — wrapped by call_sql_plan_generator tool)
│       HITL ROLE: Generate SQL and present for approval (DOES NOT EXECUTE)
│       model: BIGQUERY_AGENT_MODEL
│       tools: [bigquery_nl2sql]  # SQL generation only, NO execute_sql
│       output_key: "generated_sql_plan"
│       temperature: 0.01, AFC: DISABLED
│       │
│       └── Instruction: Generate SQL in ```sql``` block
│                       Reply **yes** to execute or **no** to cancel
│                       STOP. Do not execute.
│
├── sql_executor  (LlmAgent — wrapped by call_sql_executor tool)
│       HITL ROLE: Execute pre-approved SQL from state (ONLY AFTER USER APPROVAL)
│       model: BIGQUERY_AGENT_MODEL
│       tools: [bigquery_toolset → execute_sql]
│       reads: session.state["generated_sql_plan"]
│       output_key: "execution_result"
│       temperature: 0.01, AFC: DISABLED
│       │
│       └── Instruction: If user says 'yes': execute state['generated_sql_plan']
│                       If user says 'no': output cancellation message
│
├── bqml_agent  (LlmAgent)
│       HITL ROLE: BQML model training with instruction-based approval
│       model: BQML_AGENT_MODEL
│       tools: [bq_execute_sql (BigQueryToolset), check_bq_models, rag_response]
│       WriteMode: ALLOWED  ← only agent permitted CREATE MODEL
│       temperature: 0.01, AFC: DISABLED
│       │
│       Workflow:
│       ├── rag_response → retrieve BQML syntax
│       ├── check_bq_models → list existing models
│       ├── Generate BQML → present to user
│       │   [USER MUST REPLY "yes" — instruction-based gate]
│       └── If approved: execute_sql(CREATE MODEL ...) → run BQML
│
└── analytics_agent  (Agent — VertexAiCodeExecutor)
        called via: call_analytics_agent tool
        reads: session.state["bigquery_query_result"]
        executes: Python code for data analysis, visualization
```

---

## HITL Patterns — Research/Search Agent

### Pattern: Plan-Approve-Execute (Sequential + Loop)

```
interactive_planner_agent
  └─ plan_generator (LlmAgent, output_key="research_plan")
        [USER REVIEWS AND APPROVES PLAN]
  └─ research_pipeline (SequentialAgent)
       ├─ section_planner
       ├─ section_researcher
       └─ iterative_refinement_loop (LoopAgent, max_iterations=N)
            ├─ research_evaluator (output_schema=Feedback, output_key="research_evaluation")
            ├─ EscalationChecker (custom BaseAgent — breaks loop on grade="pass")
            └─ enhanced_search_executor
```

- The `plan_generator` MUST output the plan and STOP. No research runs until the user replies with approval.
- After approval, `research_pipeline` is triggered.
- `EscalationChecker` reads `research_evaluation.grade` and calls `EventActions(escalate=True)` when `"pass"`.

---

## Session State Schema

Document all state keys used across agents. Never read a state key without first checking it exists.

| Key | Type | Set By | Read By |
|---|---|---|---|
| `research_plan` | `str` | `plan_generator` | `research_pipeline`, `report_composer` |
| `section_research_findings` | `str` | `section_researcher`, `enhanced_search_executor` | `research_evaluator`, `report_composer` |
| `research_evaluation` | `dict` (Feedback schema) | `research_evaluator` | `EscalationChecker` |
| `report_sections` | `str` | `section_planner` | `report_composer` |
| `sources` | `list[dict]` | `collect_research_sources_callback` | `report_composer` |
| `final_cited_report` | `str` | `report_composer` | frontend |
| `generated_sql_plan` | `str` | `sql_plan_generator` | `sql_executor` |
| `sql_plan_output` | `str` | `call_sql_plan_generator` tool | `bq_root_agent` |
| `sql_execution_output` | `str` | `call_sql_executor` tool | `bq_root_agent` |
| `execution_result` | `dict` | `sql_executor` after_tool_callback | `bq_root_agent` |
| `bigquery_query_result` | `list[dict]` | `store_results_in_context` | `analytics_agent` |
| `database_settings` | `dict` | `load_database_settings_in_context` | all BQ sub-agents |
| `nl2sql_prompt` | `str` | `bigquery_nl2sql` tool | `sql_plan_generator` |

---

## Pydantic Schemas for Structured Output

Always define `output_schema` for agents that produce parseable structured data.

```python
from pydantic import BaseModel, Field
from typing import Literal

class Feedback(BaseModel):
    """Research quality evaluation result."""
    grade: Literal["pass", "fail"] = Field(
        description="'pass' if research is sufficient, 'fail' if follow-up needed."
    )
    comment: str = Field(
        description="Explanation of grade and what is missing."
    )
    follow_up_queries: list[str] = Field(
        default_factory=list,
        description="Specific search queries to fill gaps. Empty if grade is 'pass'.",
    )

class SearchQuery(BaseModel):
    """A single focused web search query."""
    query: str = Field(description="The exact search string to use.")
    rationale: str = Field(description="Why this query is needed.")
```

- Always set `output_key` when using `output_schema`.
- Always set `disallow_transfer_to_parent=True` and `disallow_transfer_to_peers=True` on structured-output agents.

---

## Callbacks — Required Patterns

### before_agent_callback — Load Settings Once

```python
def setup_before_agent_call(callback_context: CallbackContext) -> None:
    """Idempotent setup: loads schema and db settings into state on first call."""
    if "database_settings" in callback_context.state:
        return  # Already initialized — skip
    db_settings = {"bigquery": get_bq_database_settings()}
    callback_context.state["database_settings"] = db_settings
    schema = db_settings["bigquery"]["schema"]
    # Inject schema into agent instruction dynamically
    callback_context._invocation_context.agent.instruction += f"""
    <schema>
    {schema}
    </schema>
    """
```

### after_tool_callback — Persist Tool Results

```python
def store_results_in_context(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    """Saves execute_sql results to session state for downstream agents."""
    if tool.name == "execute_sql":
        tool_context.state["execution_result"] = tool_response
    return None  # Return None to pass through the original response unchanged
```

---

## Environment Variables Reference

All agents must read config from env vars — never hardcode project IDs or model names.

| Variable | Used By | Notes |
|---|---|---|
| `ROOT_AGENT_MODEL` | `bq_root_agent`, `interactive_planner_agent` | Default: `gemini-2.5-flash` |
| `BIGQUERY_AGENT_MODEL` | `sql_plan_generator`, `sql_executor` | Required |
| `ANALYTICS_AGENT_MODEL` | `analytics_agent` | Required |
| `BQML_AGENT_MODEL` | `bqml_agent` | Required |
| `CHASE_NL2SQL_MODEL` | Chase SQL tools | Required if `NL2SQL_METHOD=CHASE` |
| `BASELINE_NL2SQL_MODEL` | `bigquery_nl2sql` tool | Required if `NL2SQL_METHOD=BASELINE` |
| `NL2SQL_METHOD` | `bigquery/agent.py` | `"BASELINE"` or `"CHASE"` |
| `BQ_COMPUTE_PROJECT_ID` | All BQ tools | Billing project for query execution |
| `BQ_DATA_PROJECT_ID` | All BQ tools | Project where data lives |
| `BQ_DATASET_ID` | `bigquery/tools.py` | Target dataset |
| `GOOGLE_CLOUD_PROJECT` | Vertex AI client | Same as compute project |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI client | e.g., `us-central1` |
| `BQML_RAG_CORPUS_NAME` | `bqml/tools.py` | Vertex AI RAG corpus |
| `LOGS_BUCKET_NAME` | `telemetry.py` | GCS bucket for trace export |

---

## Observability & Debugging

- Telemetry auto-enabled in deployed environments via `app_utils/telemetry.py`.
- For local dev, set in `.env`:
  ```
  LOGS_BUCKET_NAME=your-bucket
  OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT
  ```
- Wandb/OTEL tracing wired in `app/bigquery_agent/agent.py` — requires `WANDB_API_KEY` and `WANDB_PROJECT_ID`.

### Debugging HITL Workflows

**Check if HITL is working:**
1. Look for log entries: `INFO - call_sql_plan_generator: <question>`
2. After SQL generation, agent should present SQL and STOP (no immediate execution)
3. User must type "yes" to trigger: `INFO - call_sql_executor: ...`
4. If logs show both calls consecutively without user input, HITL is broken

**Common HITL bypass causes:**
- AFC enabled (`automatic_function_calling` not disabled)
- SequentialAgent wrapping both plan and execute agents
- bq_root_agent instruction missing explicit "WAIT for user yes" step
- sql_plan_generator has execute_sql tool (shouldn't have it)

**Schema loading verification:**
```bash
# Check logs for schema loading with correct project
grep "Loading schema from project" logs.txt
grep "Loaded table:" logs.txt

# Should show:
# Loading schema from project: gcplab20250706, dataset: forecasting_sticker_sales
# Loaded table: `gcplab20250706.forecasting_sticker_sales.train`
```

---

## Anti-Patterns — Never Do These

- ❌ Never use `fsbq-agent` as a Python import prefix
- ❌ Never register the same tool twice in `tools=[]`
- ❌ Never call `execute_sql` without user approval
- ❌ Never hardcode `project_id` or `dataset_id` — always read from env/state
- ❌ Never let execution start before user approves the plan/SQL
- ❌ Never catch all exceptions silently in callbacks — log with `logger.exception()`
- ❌ Never use `include_contents="none"` without injecting all required data via state keys in the instruction
- ❌ Never import `bqml_agent` without adding it to `bq_root_agent.sub_agents` — routing will fail
- ❌ **Never wrap HITL approval workflows in SequentialAgent** — it executes all sub-agents automatically without waiting for user input. Use separate tools instead.
- ❌ Never give `sql_plan_generator` the `execute_sql` tool — it should only generate, not execute
- ❌ Never call `call_sql_executor` before `call_sql_plan_generator` — execution requires SQL in state
- ❌ Never enable AFC (automatic_function_calling) on HITL agents — it chains tools automatically, bypassing approval gates
- ❌ Never hallucinate table names — schema loading must use fully-qualified names with backticks: `` `project.dataset.table` ``
- ❌ Never add `dataset_id` parameter to `BigQueryToolConfig` — it doesn't exist and causes Pydantic validation error. Dataset context comes from fully-qualified table names in queries.
- ❌ **Never inject schema as raw Python dict (str(schema))** — format it as human-readable text with clear boundaries. Use `format_schema_for_llm()` to prevent LLM from hallucinating tables from training data.
