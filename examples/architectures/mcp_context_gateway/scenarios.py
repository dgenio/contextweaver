"""Scripted scenarios consumed by :mod:`main_real.py` (issue #280).

Pure data + the :class:`_Scenario` dataclass lifted out of
``main_real.py`` to keep the runner module under the repo's ≤ 300 line
guideline (see ``AGENTS.md`` -- "Coding Style"). Each scenario pairs a
committed real-MCP-server snapshot with a deterministic natural-language
query and a plausibly-large fake upstream response, so the context
firewall is exercised on every run.

The split is intentionally data-only: behaviour stays in ``main_real.py``
so the route -> call -> interpret -> answer narrative remains in one
place when readers walk the architecture top-to-bottom.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Scenario:
    """One scripted run against a real MCP-server snapshot."""

    snapshot: str
    user_query: str
    routing_query: str
    intent_tool_name: str  # upstream-server tool name; we match by suffix
    fake_result_text: str

    @property
    def title(self) -> str:
        return self.snapshot.removesuffix("_mcp.json")


# Three deterministic scenarios -- one per snapshot. Each ``fake_result_text``
# is large enough to trigger the firewall (>2000 chars) so the summary path
# is exercised on every run, just like in ``main.py``.
_SCENARIOS: tuple[_Scenario, ...] = (
    _Scenario(
        snapshot="filesystem_mcp.json",
        user_query="Find every Python file under /workspace that imports `httpx`.",
        routing_query="recursively search files matching a pattern",
        intent_tool_name="search_files",
        fake_result_text=(
            "search results for pattern 'import httpx' under /workspace:\n"
            + "\n".join(
                f"/workspace/pkg{i // 50}/module_{i:04d}.py: import httpx  # line {i}"
                for i in range(160)
            )
            + "\n"
        ),
    ),
    _Scenario(
        snapshot="git_mcp.json",
        user_query="What changed in the working tree of /repo since the last commit?",
        routing_query="show working tree status for repo",
        intent_tool_name="git_status",
        fake_result_text=(
            "On branch feat/context-recipes\n"
            "Your branch is up to date with 'origin/main'.\n\n"
            "Changes not staged for commit:\n"
            + "\n".join(
                f"\tmodified:   examples/recipes/auto_generated_{i:03d}.py" for i in range(40)
            )
            + "\n\nUntracked files:\n"
            + "\n".join(f"\tdocs/recipes/scratch_{i:03d}.md" for i in range(30))
            + '\n\nno changes added to commit (use "git add" and/or "git commit -a")\n'
        ),
    ),
    _Scenario(
        snapshot="fetch_mcp.json",
        user_query="Read the contextweaver MCP integration page so I can quote from it.",
        routing_query="fetch URL and extract markdown contents",
        intent_tool_name="fetch",
        fake_result_text=(
            "# MCP Integration\n\n"
            + (
                "contextweaver ships a thin adapter on top of the Model Context Protocol "
                "that converts MCP tool definitions and results into contextweaver's native "
                "shapes. The adapter lives in `src/contextweaver/adapters/mcp.py`. "
            )
            * 32
            + "\n"
        ),
    ),
)
