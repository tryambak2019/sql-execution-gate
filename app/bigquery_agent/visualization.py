"""Validation helpers for model-generated Vega-Lite visualizations."""

import json
import re
from typing import Any

VEGA_LITE_BLOCK = re.compile(r"```vega-lite\s*([\s\S]*?)```", re.IGNORECASE)
MAX_INLINE_ROWS = 500
ALLOWED_EXECUTOR_REASONS = frozenset(
    {
        "statistical_test",
        "forecasting",
        "clustering",
        "optimization",
        "simulation",
        "unsupported_transform",
        "specialized_visualization",
    }
)


def is_allowed_executor_reason(reason_code: str) -> bool:
    """Return whether advanced code execution is allowed for this reason."""
    return reason_code in ALLOWED_EXECUTOR_REASONS


def _validate_inline_data(data: object) -> None:
    if not isinstance(data, dict) or set(data) != {"values"}:
        raise ValueError("Vega-Lite data must contain inline values only")
    values = data["values"]
    if not isinstance(values, list) or len(values) > MAX_INLINE_ROWS:
        raise ValueError("Vega-Lite inline data exceeds the allowed size")
    if not all(isinstance(row, dict) for row in values):
        raise ValueError("Vega-Lite values must be row objects")


def validate_vega_lite_spec(spec: object) -> dict[str, Any]:
    """Validate the security and size boundary around a Vega-Lite spec.

    Vega-Lite itself validates chart grammar in the browser. This boundary
    prevents model output from fetching remote data or embedding executable
    expressions through configuration intended for a different renderer.
    """
    if not isinstance(spec, dict):
        raise ValueError("Vega-Lite specification must be a JSON object")
    if "$schema" not in spec or "vega-lite" not in str(spec["$schema"]).lower():
        raise ValueError("Vega-Lite schema is required")
    _validate_inline_data(spec.get("data"))
    if not any(key in spec for key in ("mark", "layer", "facet", "concat", "hconcat", "vconcat")):
        raise ValueError("Vega-Lite specification has no chart composition")
    serialized = json.dumps(spec)
    if len(serialized) > 250_000:
        raise ValueError("Vega-Lite specification is too large")
    return spec


def sanitize_analytics_output(output: object) -> object:
    """Keep valid chart blocks and degrade invalid ones to an explanation."""
    if not isinstance(output, str):
        return output

    def replace(match: re.Match[str]) -> str:
        try:
            spec = validate_vega_lite_spec(json.loads(match.group(1)))
        except (json.JSONDecodeError, ValueError) as exc:
            return f"Visualization could not be rendered safely: {exc}."
        return f"```vega-lite\n{json.dumps(spec, separators=(',', ':'))}\n```"

    return VEGA_LITE_BLOCK.sub(replace, output)


def extract_vega_lite_specs(output: str) -> list[dict[str, Any]]:
    """Extract only validated specifications from an analytics response."""
    specs: list[dict[str, Any]] = []
    for match in VEGA_LITE_BLOCK.finditer(output):
        try:
            specs.append(validate_vega_lite_spec(json.loads(match.group(1))))
        except (json.JSONDecodeError, ValueError):
            continue
    return specs
