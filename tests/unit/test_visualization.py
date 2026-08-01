import json

import pytest

from app.bigquery_agent.visualization import (
    extract_vega_lite_specs,
    is_allowed_executor_reason,
    sanitize_analytics_output,
    validate_vega_lite_spec,
)
from app.bigquery_agent.sub_agents.analytics.prompts import (
    return_instructions_analytics,
)
from app.bigquery_agent.prompts import get_instruction_with_schema


def _spec() -> dict:
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"country": "Norway", "sales": 10}]},
        "mark": "bar",
        "encoding": {
            "x": {"field": "country", "type": "nominal"},
            "y": {"field": "sales", "type": "quantitative"},
        },
    }


def test_accepts_inline_vega_lite_spec() -> None:
    assert validate_vega_lite_spec(_spec())["mark"] == "bar"


def test_rejects_remote_data() -> None:
    spec = _spec()
    spec["data"] = {"url": "https://example.com/private.json"}
    with pytest.raises(ValueError, match="inline values"):
        validate_vega_lite_spec(spec)


def test_sanitizes_valid_block_and_replaces_invalid_block() -> None:
    valid = sanitize_analytics_output(
        f"Result\n```vega-lite\n{json.dumps(_spec())}\n```"
    )
    assert isinstance(valid, str) and "```vega-lite" in valid

    invalid = sanitize_analytics_output(
        '```vega-lite\n{"data":{"url":"https://example.com"},"mark":"bar"}\n```'
    )
    assert isinstance(invalid, str) and "could not be rendered safely" in invalid


def test_prompt_defaults_to_vega_and_allowlists_executor_reasons() -> None:
    prompt = return_instructions_analytics()
    assert "Vega-Lite is the default" in prompt
    assert "ADVANCED_ANALYSIS_REQUIRED: <allowlisted_reason>" in prompt
    assert "Visual complexity alone is NOT a reason" in prompt


def test_root_prompt_preserves_direct_plot_request_after_sql_approval() -> None:
    """A typed plot request remains mandatory after the HITL approval turn."""

    class PromptContext:
        state = {}

    prompt = get_instruction_with_schema(PromptContext())

    assert "persistent visualization requirement" in prompt
    assert "MUST call `call_analytics_agent`" in prompt
    assert "Return the Vega-Lite block" in prompt


def test_executor_reason_gate_is_fail_closed() -> None:
    assert is_allowed_executor_reason("forecasting")
    assert not is_allowed_executor_reason("complex_chart")
    assert not is_allowed_executor_reason("")


def test_extracts_only_valid_vega_lite_specs() -> None:
    output = (
        f"Insight\n```vega-lite\n{json.dumps(_spec())}\n```\n"
        '```vega-lite\n{"data":{"url":"https://example.com"},"mark":"bar"}\n```'
    )
    assert extract_vega_lite_specs(output) == [_spec()]
