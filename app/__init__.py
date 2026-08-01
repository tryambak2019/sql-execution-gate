"""SQL Execution Gate ADK application package.

Keep agent construction lazy so importing utility modules does not require the
full production environment. ADK still discovers ``root_agent`` through the
module attribute when it loads the application.
"""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "root_agent":
        from app.agent import root_agent

        return root_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["root_agent"]
