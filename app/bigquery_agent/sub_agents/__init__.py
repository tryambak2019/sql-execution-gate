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

"""Lazy exports for the production sub-agents."""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "analytics_agent":
        from .analytics.agent import analytics_agent

        return analytics_agent
    if name == "advanced_analytics_agent":
        from .analytics.agent import advanced_analytics_agent

        return advanced_analytics_agent
    if name in {"sql_plan_generator", "sql_executor"}:
        from .bigquery.agent import sql_executor, sql_plan_generator

        return {
            "sql_plan_generator": sql_plan_generator,
            "sql_executor": sql_executor,
        }[name]
    if name == "bqml_agent":
        from .bqml.agent import bqml_agent

        return bqml_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "advanced_analytics_agent",
    "analytics_agent",
    "sql_plan_generator",
    "sql_executor",
    "bqml_agent",
]
