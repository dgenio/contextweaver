#!/usr/bin/env python3
"""Regenerate llms.txt and llms-full.txt from source documentation.

Run via `make llms`. Both output files are deterministic and should
match the committed copies byte-for-byte after each documentation
change. Intended for CI to run with `--check` to fail builds when
drift is introduced.

Layout of each output:

* `llms-full.txt` — concatenation of every doc listed in
  ``LLMS_FULL_FILES``, separated by ``---`` rules and per-file
  ``<!-- FILE: <path> -->`` markers, prefixed by a generated header
  block that lists the source files (so a reader of the file can find
  the originals without consulting the script).
* `llms.txt` — the llmstxt.org index: header, link sections, and a
  one-line description per linked target. Links are produced from the
  ``LLMS_INDEX`` data structure below.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from _golden import check_text_artifacts, write_text_artifacts

REPO_ROOT = Path(__file__).resolve().parent.parent

# Order matters — this is the concatenation order used in llms-full.txt.
# Add new docs here when shipping a new file under docs/ or a new
# top-level guide so llms-full.txt stays in sync.
LLMS_FULL_FILES: list[str] = [
    "README.md",
    "docs/architecture.md",
    "docs/concepts.md",
    "docs/quickstart.md",
    "docs/daily_driver.md",
    "docs/security_model.md",
    "docs/security_mcp_gateway.md",
    "docs/sensitivity.md",
    "docs/recipes/index.md",
    "docs/recipes/claude_code.md",
    "docs/recipes/okf_bundle.md",
    "docs/integration_mcp.md",
    "docs/integration_a2a.md",
    "docs/errors.md",
    "docs/agent-context/architecture.md",
    "docs/agent-context/invariants.md",
    "docs/agent-context/workflows.md",
    "docs/agent-context/lessons-learned.md",
    "docs/agent-context/review-checklist.md",
    "docs/guide_agent_loop.md",
]

LLMS_FULL_HEADER = """\
# contextweaver — Full Documentation

> Dynamic context management for tool-using AI agents.

---

<!--
  GENERATED FILE — do not edit by hand.

  Source files (concatenated in order):
{file_list}

  To regenerate: `make llms` (or `python scripts/gen_llms.py`).
-->
"""

LLMS_INDEX_HEADER = """\
# contextweaver

> Dynamic context management for tool-using AI agents.

contextweaver is a Python library that provides two integrated engines: a
phase-specific, budget-aware Context Engine and a bounded-choice Routing Engine
for large tool catalogs. Zero runtime dependencies, deterministic output,
Python ≥ 3.10.
"""

# llmstxt.org index sections. Each entry is (link_text, target_path,
# one_line_description). Order within each section is preserved.
LLMS_INDEX: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Docs",
        [
            (
                "Architecture",
                "docs/architecture.md",
                "Package layout, pipeline stages, design principles",
            ),
            (
                "Concepts",
                "docs/concepts.md",
                "ContextItem, phases, firewall, ChoiceGraph, sensitivity enforcement",
            ),
            (
                "Quickstart",
                "docs/quickstart.md",
                "10-minute guide to context builds, firewall, and routing",
            ),
            (
                "Daily Driver",
                "docs/daily_driver.md",
                "Operator workflow, client instructions, fit guidance, and debugging",
            ),
            (
                "MCP Security Model",
                "docs/security_model.md",
                "Gateway data flow, trust boundaries, artifact exposure, and hardening",
            ),
            (
                "MCP Gateway Security Guide",
                "docs/security_mcp_gateway.md",
                "Least-privilege gateway ops: secrets, destructive tools, policy gate",
            ),
            (
                "Sensitivity & Redaction",
                "docs/sensitivity.md",
                "Levels, floor/action, redaction hooks, verification, and limits",
            ),
            (
                "MCP Client Recipes",
                "docs/recipes/index.md",
                "Claude Desktop, Claude Code, GitHub Copilot, and Cursor setup",
            ),
            (
                "Knowledge Bundle Recipes",
                "docs/recipes/okf_bundle.md",
                "OKF, repository knowledge, lessons, and expertise-pack context sources",
            ),
            (
                "MCP Integration",
                "docs/integration_mcp.md",
                "MCP adapter functions, JSONL format, end-to-end example",
            ),
            (
                "A2A Integration",
                "docs/integration_a2a.md",
                "A2A adapter functions, multi-agent sessions",
            ),
            (
                "Agent Loop Guide",
                "docs/guide_agent_loop.md",
                "Flow diagram and phase guidance for building a complete agent loop",
            ),
            (
                "Error Reference",
                "docs/errors.md",
                "Exception hierarchy with stable error codes, causes, and fixes",
            ),
        ],
    ),
    (
        "Agent Context",
        [
            (
                "Agent Architecture",
                "docs/agent-context/architecture.md",
                "Non-obvious architectural guidance, design tradeoffs, and async/sync boundaries",
            ),
            (
                "Invariants",
                "docs/agent-context/invariants.md",
                "Hard constraints and forbidden shortcuts in the codebase",
            ),
            (
                "Workflows",
                "docs/agent-context/workflows.md",
                "Authoritative commands, sequencing, and definition of done",
            ),
            (
                "Lessons Learned",
                "docs/agent-context/lessons-learned.md",
                "Recurring failure patterns and how to avoid them",
            ),
            (
                "Review Checklist",
                "docs/agent-context/review-checklist.md",
                "Self-check and review gates for contributors",
            ),
        ],
    ),
    (
        "API",
        [
            (
                "Types",
                "src/contextweaver/types.py",
                "Core dataclasses (SelectableItem, ContextItem, Phase, ItemKind, Sensitivity)",
            ),
            (
                "Config",
                "src/contextweaver/config.py",
                "Configuration dataclasses (ContextBudget, ContextPolicy, ScoringConfig)",
            ),
            (
                "Protocols",
                "src/contextweaver/protocols.py",
                "Protocol interfaces (TokenEstimator, EventHook, Summarizer, Extractor)",
            ),
            (
                "Envelope",
                "src/contextweaver/envelope.py",
                "Result types (ResultEnvelope, BuildStats, ContextPack, ChoiceCard)",
            ),
            (
                "Context Manager",
                "src/contextweaver/context/manager.py",
                "Main entry point for context builds (build, ingest, ingest_tool_result)",
            ),
            (
                "Router",
                "src/contextweaver/routing/router.py",
                "Beam search routing over ChoiceGraph",
            ),
            (
                "Catalog",
                "src/contextweaver/routing/catalog.py",
                "Tool catalog management and hydration",
            ),
        ],
    ),
    (
        "Examples",
        [
            ("Minimal Loop", "examples/minimal_loop.py", "Basic event ingestion → context build"),
            ("Tool Wrapping", "examples/tool_wrapping.py", "Context firewall in action"),
            (
                "Routing Demo",
                "examples/routing_demo.py",
                "Build catalog → route queries → choice cards",
            ),
            (
                "Before/After",
                "examples/before_after.py",
                "Comparing raw vs. firewall-processed context",
            ),
            (
                "MCP Adapter Demo",
                "examples/mcp_adapter_demo.py",
                "End-to-end MCP session ingestion and context build",
            ),
            (
                "A2A Adapter Demo",
                "examples/a2a_adapter_demo.py",
                "End-to-end A2A multi-agent session demo",
            ),
            (
                "Hydrate Call Demo",
                "examples/hydrate_call_demo.py",
                "Enrich a tool call with context-aware hydration",
            ),
            (
                "Full Agent Loop",
                "examples/full_agent_loop.py",
                "End-to-end 4-phase runtime loop (route → call → interpret → answer)",
            ),
            (
                "LangChain Memory Demo",
                "examples/langchain_memory_demo.py",
                "Replacing LangChain InMemoryChatMessageHistory with contextweaver budgets",
            ),
        ],
    ),
]


def _read_file_stripped(path: Path) -> str:
    """Return file contents with trailing whitespace stripped.

    Sources end with a newline by convention; the concatenated
    `llms-full.txt` instead uses explicit `---` rules between files,
    so the trailing newline is consumed by the separator.
    """
    return path.read_text(encoding="utf-8").rstrip()


def render_llms_full() -> str:
    file_list_lines = [f"    {p}" for p in LLMS_FULL_FILES]
    header = LLMS_FULL_HEADER.format(file_list="\n".join(file_list_lines))

    parts: list[str] = [header]
    for i, rel in enumerate(LLMS_FULL_FILES):
        absolute = REPO_ROOT / rel
        if not absolute.is_file():
            raise FileNotFoundError(f"Source not found: {rel}")
        if i == 0:
            parts.append(f"\n<!-- FILE: {rel} -->\n\n{_read_file_stripped(absolute)}")
        else:
            parts.append(f"\n\n---\n\n<!-- FILE: {rel} -->\n\n{_read_file_stripped(absolute)}")
    return "".join(parts)


def render_llms_index() -> str:
    parts: list[str] = [LLMS_INDEX_HEADER]
    for section_title, entries in LLMS_INDEX:
        parts.append(f"\n## {section_title}\n\n")
        for label, path, desc in entries:
            parts.append(f"- [{label}]({path}): {desc}\n")
    return "".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if regenerated output differs from the committed files",
    )
    args = parser.parse_args(argv)

    rendered = {
        REPO_ROOT / "llms-full.txt": render_llms_full(),
        REPO_ROOT / "llms.txt": render_llms_index(),
    }

    if args.check:
        return check_text_artifacts(rendered, label="llms", regen="make llms")

    write_text_artifacts(rendered)
    for target, body in rendered.items():
        print(f"Wrote {target.name} ({len(body)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
