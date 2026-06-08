#!/usr/bin/env python3
"""Record asciinema casts for the showcase demos (issue #281).

Stdlib-only writer for the asciinema v2 cast format (no `asciinema`
binary required). For each showcase demo, the script:

1. Runs the demo and captures its stdout.
2. Synthesises a deterministic v2 cast (fixed pseudo-timestamps so
   re-runs are byte-identical).
3. Writes the cast to ``docs/assets/casts/<demo>.cast``.

asciinema v2 is JSONL: line 1 is a JSON header, subsequent lines are
``[time_seconds, "o", "text"]`` event tuples. The format is widely
embeddable — GitHub renders ``.cast`` files via inline player snippets;
mkdocs/material can embed via ``asciinema-player`` (an optional ~30 KB
JS asset).

Determinism contract: ``--check`` exits non-zero if the rendered casts
differ from the committed files. CI runs this check to catch drift when
a demo's stdout changes without the cast being regenerated.

Usage::

    python scripts/record_demo.py             # write all casts
    python scripts/record_demo.py --check     # drift gate
    python scripts/record_demo.py --demo large-catalog   # one demo only

The committed casts under ``docs/assets/casts/*.cast`` are the public
artefacts referenced by ``docs/showcase.md``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CASTS_DIR = _REPO_ROOT / "docs" / "assets" / "casts"

# Pseudo-recording parameters. These are deterministic so re-runs produce
# byte-identical casts. Real asciinema would emit fractional-second
# timestamps tied to the human's typing; for a synthesised cast we space
# events evenly across the duration.
_TERMINAL_WIDTH = 100
_TERMINAL_HEIGHT = 30
_PSEUDO_DURATION_S = 30.0


@dataclass(frozen=True)
class Demo:
    """A single showcase demo to capture."""

    name: str
    command: tuple[str, ...]
    description: str


_DEMOS: tuple[Demo, ...] = (
    Demo(
        name="default",
        command=(sys.executable, "-m", "contextweaver", "demo"),
        description="contextweaver demo  (friendly walkthrough)",
    ),
    Demo(
        name="large-catalog",
        command=(sys.executable, "-m", "contextweaver", "demo", "--scenario", "large-catalog"),
        description="contextweaver demo --scenario large-catalog  (1,000 tools → 5 cards)",
    ),
    Demo(
        name="huge-tool-output",
        command=(sys.executable, "-m", "contextweaver", "demo", "--scenario", "huge-tool-output"),
        description="contextweaver demo --scenario huge-tool-output  (context firewall)",
    ),
    Demo(
        name="mcp-gateway-full",
        command=(
            sys.executable,
            "-m",
            "contextweaver",
            "demo",
            "--scenario",
            "mcp-gateway-full",
        ),
        description="contextweaver demo --scenario mcp-gateway-full  (60-tool architecture)",
    ),
)


def _capture_stdout(command: tuple[str, ...]) -> str:
    """Run *command* and return its stdout as a single string."""
    result = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"warning: {' '.join(command)!r} exited {result.returncode}; "
            f"stderr={result.stderr[:200]!r}\n"
        )
    # Strip a known noise line from environments where tiktoken cannot
    # reach its CDN — it adds non-determinism via the error URL.
    out = result.stdout
    out_lines = [
        line
        for line in out.splitlines(keepends=True)
        if "tiktoken cl100k_base encoding unavailable" not in line
    ]
    return "".join(out_lines)


def _synthesise_cast(demo: Demo, stdout: str) -> str:
    """Produce an asciinema v2 cast JSONL string for *demo*.

    The header is a JSON object. Subsequent lines are ``[t, "o", text]``
    triples where ``t`` ramps from 0 to ``_PSEUDO_DURATION_S`` across the
    output lines. The first event prints the demo's narration banner so
    a reader sees the command that was run.
    """
    header = {
        "version": 2,
        "width": _TERMINAL_WIDTH,
        "height": _TERMINAL_HEIGHT,
        "timestamp": 0,
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": demo.description,
    }
    lines = stdout.splitlines(keepends=True)
    if not lines:
        lines = ["(demo produced no stdout)\n"]
    # First event: simulate the prompt + typed command + newline so the
    # cast reads like a real terminal session.
    events: list[list[float | str]] = [
        [0.0, "o", f"$ {demo.description}\r\n"],
    ]
    # Distribute the captured lines evenly across the pseudo-duration.
    n = len(lines)
    for idx, line in enumerate(lines):
        t = round(0.5 + (_PSEUDO_DURATION_S - 0.5) * (idx + 1) / n, 4)
        # asciinema event text uses CRLF, not LF.
        events.append([t, "o", line.replace("\n", "\r\n")])
    # Newline + end-of-recording prompt.
    events.append([round(_PSEUDO_DURATION_S, 4), "o", "$ "])
    out_lines = [json.dumps(header, sort_keys=True, ensure_ascii=False)]
    for ev in events:
        out_lines.append(json.dumps(ev, ensure_ascii=False))
    return "\n".join(out_lines) + "\n"


def _select_demos(names: list[str] | None) -> list[Demo]:
    if not names:
        return list(_DEMOS)
    valid = {d.name: d for d in _DEMOS}
    missing = [n for n in names if n not in valid]
    if missing:
        sys.stderr.write(f"error: unknown demo(s): {missing!r}; valid: {sorted(valid)}\n")
        raise SystemExit(2)
    return [valid[n] for n in names]


def main() -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo",
        action="append",
        help=(
            "Limit recording to a specific demo name. Repeat for multiple. "
            f"Choices: {', '.join(d.name for d in _DEMOS)}"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if a committed cast differs from a fresh run.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_CASTS_DIR,
        help="Directory to write/check casts in.",
    )
    args = parser.parse_args()
    demos = _select_demos(args.demo)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rc = 0
    for demo in demos:
        stdout = _capture_stdout(demo.command)
        cast = _synthesise_cast(demo, stdout)
        target = args.output_dir / f"{demo.name}.cast"
        if args.check:
            if not target.exists():
                sys.stderr.write(f"error: {target} missing — run scripts/record_demo.py\n")
                rc = 1
                continue
            existing = target.read_text(encoding="utf-8")
            if existing != cast:
                sys.stderr.write(f"error: {target} is stale — re-run scripts/record_demo.py\n")
                rc = 1
        else:
            target.write_text(cast, encoding="utf-8", newline="\n")
            print(f"Wrote {target} ({len(cast)} chars)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
