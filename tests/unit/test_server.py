"""Tests for the unified production web surface."""

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pytest import CaptureFixture, MonkeyPatch

from app.bigquery_agent.sql_gate import SqlReview
from server import create_app


def _built_frontend(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><title>SQL Execution Gate</title>",
        encoding="utf-8",
    )
    return dist


def _config(tmp_path: Path, max_user_queries: int) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"usage_limits:\n  max_user_queries: {max_user_queries}\n",
        encoding="utf-8",
    )
    return config


def test_root_redirects_to_frontend(tmp_path: Path) -> None:
    client = TestClient(create_app(_built_frontend(tmp_path)))

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


def test_health_and_frontend_are_served(tmp_path: Path) -> None:
    client = TestClient(create_app(_built_frontend(tmp_path)))

    health = client.get("/healthz")
    frontend = client.get("/app/")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert frontend.status_code == 200
    assert "SQL Execution Gate" in frontend.text


def test_selected_table_schema_returns_only_requested_table(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "server.get_bigquery_schema_and_samples",
        lambda: {
            "`project.dataset.train`": {
                "table_schema": [("id", "INTEGER"), ("country", "STRING")]
            },
            "`project.dataset.test`": {
                "table_schema": [("id", "INTEGER")]
            },
        },
    )
    client = TestClient(create_app(_built_frontend(tmp_path)))

    response = client.get("/schema", params={"table": "project.dataset.train"})

    assert response.status_code == 200
    assert response.json() == {
        "table": "project.dataset.train",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "country", "type": "STRING"},
        ],
    }


def test_selected_table_schema_rejects_unknown_table(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "server.get_bigquery_schema_and_samples",
        lambda: {},
    )
    client = TestClient(create_app(_built_frontend(tmp_path)))

    response = client.get("/schema", params={"table": "project.dataset.missing"})

    assert response.status_code == 404


def test_adk_api_is_prefixed(tmp_path: Path) -> None:
    client = TestClient(create_app(_built_frontend(tmp_path)))

    prefixed_docs = client.get("/api/openapi.json")
    unprefixed_docs = client.get("/openapi.json")

    assert prefixed_docs.status_code == 200
    assert unprefixed_docs.status_code == 404


def test_query_limit_blocks_substantive_requests_but_not_approval(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(_built_frontend(tmp_path), _config(tmp_path, 1))
    )
    base_payload = {
        "appName": "app",
        "userId": "demo-user",
        "sessionId": "limited-session",
        "streaming": True,
    }

    first = client.post(
        "/api/run_sse",
        json={
            **base_payload,
            "newMessage": {"role": "user", "parts": [{"text": "Count rows"}]},
        },
    )
    approval = client.post(
        "/api/run_sse",
        json={
            **base_payload,
            "newMessage": {"role": "user", "parts": [{"text": "yes"}]},
        },
    )
    blocked = client.post(
        "/api/run_sse",
        json={
            **base_payload,
            "newMessage": {"role": "user", "parts": [{"text": "Count orders"}]},
        },
    )

    assert first.status_code != 429
    assert approval.status_code != 429
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == (
        "This visitor has reached the demo's 1-query limit."
    )


def test_query_limit_is_independent_per_visitor(tmp_path: Path) -> None:
    client = TestClient(
        create_app(_built_frontend(tmp_path), _config(tmp_path, 1))
    )

    for session_id, client_ip in (
        ("session-one", "203.0.113.10"),
        ("session-two", "203.0.113.11"),
    ):
        response = client.post(
            "/api/run_sse",
            headers={"x-forwarded-for": client_ip},
            json={
                "appName": "app",
                "userId": "demo-user",
                "sessionId": session_id,
                "newMessage": {
                    "role": "user",
                    "parts": [{"text": "Show available tables"}],
                },
                "streaming": True,
            },
        )
        assert response.status_code != 429


def test_missing_frontend_returns_actionable_error(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "missing"))

    response = client.get("/app/")

    assert response.status_code == 503
    assert "npm --prefix frontend run build" in response.json()["detail"]


def test_runtime_agents_directory_does_not_expose_repo_folders(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    agents_dir = tmp_path / "agents"
    (agents_dir / "app").mkdir(parents=True)
    monkeypatch.setenv("SQL_EXECUTION_GATE_AGENTS_DIR", str(agents_dir))
    client = TestClient(create_app(_built_frontend(tmp_path)))

    response = client.get("/api/list-apps")

    assert response.status_code == 200
    assert response.json() == ["app"]


def test_sql_preflight_returns_review_evidence(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "server.dry_run_sql",
        lambda _sql: SqlReview(
            sql_fingerprint="abc123",
            referenced_tables=("`project.dataset.orders`",),
            estimated_bytes=640,
            maximum_bytes_billed=1000,
        ),
    )
    client = TestClient(create_app(_built_frontend(tmp_path)))

    response = client.post("/sql/preflight", json={"sql": "SELECT 1"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready_for_approval",
        "sql_fingerprint": "abc123",
        "referenced_tables": ["`project.dataset.orders`"],
        "estimated_bytes": 640,
        "maximum_bytes_billed": 1000,
    }


def test_explicit_write_request_is_blocked_before_agent_execution(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(_built_frontend(tmp_path)))

    response = client.post(
        "/api/run_sse",
        json={
            "appName": "app",
            "userId": "demo-user",
            "sessionId": "blocked-session",
            "newMessage": {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Delete all cancelled orders and return the "
                            "remaining revenue."
                        )
                    }
                ],
            },
            "streaming": True,
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "Blocked: SQL Execution Gate permits read-only queries only.\n\n"
            "No BigQuery query job was submitted."
        )
    }


def test_demo_visit_emits_structured_log(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    client = TestClient(create_app(_built_frontend(tmp_path)))

    response = client.get(
        "/app/",
        headers={"x-forwarded-for": "203.0.113.7", "user-agent": "Test Browser"},
    )

    assert response.status_code == 200
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "demo_visit_started"
    assert event["client_ip"] == "203.0.113.7"
    assert event["request_path"] == "/app/"


def test_visualize_reads_session_data_and_returns_validated_spec(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"country": "Norway", "sales": 10}]},
        "mark": "bar",
        "encoding": {
            "x": {"field": "country", "type": "nominal"},
            "y": {"field": "sales", "type": "quantitative"},
        },
    }

    class FakeRunner:
        def __init__(self, **_kwargs) -> None:
            pass

        async def run_async(self, **_kwargs):
            yield SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text=(
                                "Norway leads.\n```vega-lite\n"
                                f"{json.dumps(spec)}\n```"
                            )
                        )
                    ]
                )
            )

    monkeypatch.setattr("server.Runner", FakeRunner)
    client = TestClient(create_app(_built_frontend(tmp_path)))
    created = client.post(
        "/api/apps/app/users/test-user/sessions/test-session",
        json={"bigquery_query_result": [{"country": "Norway", "sales": 10}]},
    )
    assert created.status_code == 200

    response = client.post(
        "/visualize",
        json={
            "app_name": "app",
            "user_id": "test-user",
            "session_id": "test-session",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"insight": "Norway leads.", "spec": spec}
