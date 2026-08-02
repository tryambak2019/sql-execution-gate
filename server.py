"""Unified ADK API and React server for local production parity and Cloud Run."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field
from google.adk.cli.fast_api import get_fast_api_app

from app.bigquery_agent.sub_agents.analytics.visualization_agent import (
    visualization_agent,
)
from app.bigquery_agent.sub_agents.bigquery.tools import (
    get_bigquery_schema_and_samples,
)
from app.bigquery_agent.sql_gate import (
    SqlGateBlocked,
    dry_run_sql,
    write_intent_block_reason,
)
from app.bigquery_agent.visualization import extract_vega_lite_specs

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def _load_max_user_queries(config_path: Path) -> int:
    """Load the per-visitor query allowance from application configuration."""
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        value = config["usage_limits"]["max_user_queries"]
    except (OSError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Invalid or missing application config: {config_path}") from exc

    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RuntimeError("usage_limits.max_user_queries must be a positive integer")
    return value


def _is_counted_user_query(payload: object) -> bool:
    """Count substantive chat requests, but not HITL approval/cancellation turns."""
    text = _user_message_text(payload)
    return bool(text) and text.casefold() not in {"yes", "no"}


def _user_message_text(payload: object) -> str:
    """Extract one user turn from the ADK request envelope."""
    if not isinstance(payload, dict):
        return ""
    new_message = payload.get("newMessage")
    if not isinstance(new_message, dict) or new_message.get("role") != "user":
        return ""
    parts = new_message.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()


class VisualizationRequest(BaseModel):
    """Identify the approved ADK session whose latest result should be plotted."""

    app_name: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)


class SqlPreflightRequest(BaseModel):
    """Generated SQL requiring deterministic review evidence."""

    sql: str = Field(min_length=1, max_length=100_000)


def _client_ip(request: Request) -> str:
    """Return the original address supplied by Cloud Run's trusted proxy."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(
    frontend_dist: Path | None = None,
    config_path: Path | None = None,
) -> FastAPI:
    """Create one same-origin server with ADK under /api and React under /app."""
    dist = frontend_dist or DEFAULT_FRONTEND_DIST
    max_user_queries = _load_max_user_queries(config_path or DEFAULT_CONFIG_PATH)
    query_counts: defaultdict[str, int] = defaultdict(int)
    agents_dir = Path(os.getenv("SQL_EXECUTION_GATE_AGENTS_DIR", str(PROJECT_ROOT)))
    adk_app = get_fast_api_app(
        agents_dir=str(agents_dir),
        allow_origins=[],
        web=False,
        use_local_storage=False,
    )
    app = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)

    @app.middleware("http")
    async def enforce_query_limit(request: Request, call_next):
        """Log demo visits and apply a per-visitor cap before invoking an LLM."""
        if request.method == "GET" and request.url.path in {"/app", "/app/"}:
            print(
                json.dumps(
                    {
                        "severity": "NOTICE",
                        "event": "demo_visit_started",
                        "request_path": request.url.path,
                        "client_ip": _client_ip(request),
                        "user_agent": request.headers.get("user-agent", "unknown")[:500],
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        if request.method == "POST" and request.url.path == "/api/run_sse":
            try:
                payload = json.loads(await request.body())
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None
            block_reason = write_intent_block_reason(_user_message_text(payload))
            if block_reason:
                return JSONResponse(
                    {
                        "detail": (
                            f"Blocked: {block_reason}\n\n"
                            "No BigQuery query job was submitted."
                        )
                    },
                    status_code=422,
                )
            if _is_counted_user_query(payload):
                visitor = _client_ip(request)
                if query_counts[visitor] >= max_user_queries:
                    return JSONResponse(
                        {
                            "detail": (
                                "This visitor has reached the demo's "
                                f"{max_user_queries}-query limit."
                            )
                        },
                        status_code=429,
                    )
                query_counts[visitor] += 1
        return await call_next(request)

    app.mount("/api", adk_app, name="adk-api")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/sql/preflight", include_in_schema=False)
    async def sql_preflight(payload: SqlPreflightRequest) -> dict[str, object]:
        """Return read-only policy evidence before approval is enabled."""
        try:
            review = dry_run_sql(payload.sql)
        except SqlGateBlocked as exc:
            raise HTTPException(status_code=422, detail=f"Blocked: {exc}") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="BigQuery dry run could not be completed.",
            ) from exc
        return {
            "status": "ready_for_approval",
            "sql_fingerprint": review.sql_fingerprint,
            "referenced_tables": list(review.referenced_tables),
            "estimated_bytes": review.estimated_bytes,
            "maximum_bytes_billed": review.maximum_bytes_billed,
        }

    @app.post("/visualize", include_in_schema=False)
    async def visualize(payload: VisualizationRequest) -> dict[str, object]:
        """Render the saved query result through the non-executor Vega agent."""
        session_path = (
            f"/apps/{payload.app_name}/users/{payload.user_id}/sessions/"
            f"{payload.session_id}"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=adk_app),
            base_url="http://adk.internal",
        ) as client:
            response = await client.get(session_path)
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="The analytics session expired.")

        query_result = response.json().get("state", {}).get("bigquery_query_result")
        if not query_result:
            raise HTTPException(
                status_code=409,
                detail="Run and approve a BigQuery query before plotting it.",
            )

        prompt = f"""Create one useful Vega-Lite visualization of this approved
BigQuery result. Return a short insight followed by exactly one fenced
```vega-lite JSON block. Do not use Python, code execution, or Matplotlib.

<BIGQUERY>
{query_result}
</BIGQUERY>
"""
        sessions = InMemorySessionService()
        chart_session = await sessions.create_session(
            app_name="sql_execution_gate_visualization",
            user_id=payload.user_id,
        )
        runner = Runner(
            app_name="sql_execution_gate_visualization",
            agent=visualization_agent,
            session_service=sessions,
        )
        message = types.Content(
            role="user", parts=[types.Part.from_text(text=prompt)]
        )
        output_parts: list[str] = []
        async for event in runner.run_async(
            user_id=payload.user_id,
            session_id=chart_session.id,
            new_message=message,
        ):
            if event.content and event.content.parts:
                output_parts.extend(part.text for part in event.content.parts if part.text)

        output = "".join(output_parts)
        specs = extract_vega_lite_specs(output)
        if not specs:
            raise HTTPException(
                status_code=422,
                detail="The chart specification was invalid. Please try again.",
            )
        insight = output.split("```vega-lite", 1)[0].strip()
        return {"insight": insight, "spec": specs[0]}

    @app.get("/schema", include_in_schema=False)
    async def selected_table_schema(
        table: str = Query(min_length=1),
    ) -> dict[str, object]:
        """Return fields for one table selected by generated SQL."""
        normalized_table = table.strip().strip("`")
        try:
            schema = get_bigquery_schema_and_samples()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="BigQuery schema is temporarily unavailable.",
            ) from exc

        for qualified_name, table_info in schema.items():
            if qualified_name.strip("`") != normalized_table:
                continue
            return {
                "table": normalized_table,
                "columns": [
                    {"name": name, "type": field_type}
                    for name, field_type in table_info.get("table_schema", [])
                ],
            }

        raise HTTPException(status_code=404, detail="Selected table was not found.")

    if (dist / "index.html").is_file():
        app.mount(
            "/app",
            StaticFiles(directory=dist, html=True),
            name="frontend",
        )
    else:

        @app.get("/app/", include_in_schema=False)
        async def frontend_not_built() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": (
                        "Frontend assets are missing. Run `npm --prefix frontend "
                        "run build` before starting the production server."
                    )
                },
                status_code=503,
            )

    return app


app = create_app()
