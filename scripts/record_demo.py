"""Regenerate the contextweaver demo recording (issue #257).

Produces two deterministic artifacts from a single source — the actual
stdout of ``python -m contextweaver demo``:

* ``docs/assets/demo.cast`` — asciinema v2 cast file (plain JSONL).
  Playable with ``asciinema play docs/assets/demo.cast`` if asciinema is
  installed; viewable on https://asciinema.org via upload.
* ``docs/assets/demo.svg`` — animated SVG terminal recording.  Renders
  natively in GitHub markdown so the README hero plays without any
  extra tooling on the viewer's side.

Run::

    python scripts/record_demo.py            # regenerate both artifacts
    python scripts/record_demo.py --check    # exit 1 if artifacts drift

The script is stdlib-only and deterministic (no real timestamps; the
asciinema header carries a fixed timestamp pinned to the v0.8 launch
date so re-running on a different day does not produce a diff).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAST_PATH = ROOT / "docs" / "assets" / "demo.cast"
SVG_PATH = ROOT / "docs" / "assets" / "demo.svg"

# Cast header constants — pinned for determinism.  Width matches the
# widest line in the captured output (108 chars in the ChoiceCard table
# row); height matches a comfortable terminal aspect ratio.
CAST_WIDTH = 110
CAST_HEIGHT = 32
CAST_TIMESTAMP = 1747699200  # 2026-05-20 00:00:00 UTC — launch window
CAST_TITLE = "contextweaver — end-to-end demo"

# Visual constants for the animated SVG.
SVG_FONT_FAMILY = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
)
SVG_FONT_SIZE = 13
SVG_LINE_HEIGHT = 18
SVG_LEFT_PAD = 16
SVG_TOP_PAD = 36
SVG_BG = "#0d1117"
SVG_FG = "#c9d1d9"
SVG_ACCENT = "#79c0ff"
SVG_HEADER_BG = "#161b22"
SVG_PROMPT = "#7ee787"
SVG_DIM = "#8b949e"

# Pacing — keep the whole recording inside 60-90 s per #257.
LINE_DELAY = 0.7  # seconds between successive lines appearing
PRE_DELAY = 0.4  # seconds before the first line


@dataclass
class Frame:
    """One captured line of the demo output plus its appearance time."""

    line: str
    t: float


def _filter_demo_output(raw: str) -> list[str]:
    """Drop tiktoken's offline-cache warning and trailing blank lines."""

    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.rstrip("\r")
        if stripped.startswith("tiktoken ") and "cl100k_base" in stripped:
            # offline-environment warning, not part of the demo narrative
            continue
        lines.append(stripped)
    # Drop trailing blanks so the recording ends crisply.
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _capture_demo() -> list[str]:
    """Run ``python -m contextweaver demo`` and return cleaned stdout lines."""

    env_python = sys.executable
    proc = subprocess.run(
        [env_python, "-m", "contextweaver", "demo"],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"`python -m contextweaver demo` exited with {proc.returncode}")
    return _filter_demo_output(proc.stdout)


def _frames(lines: list[str]) -> list[Frame]:
    """Pace the captured lines into a sequence of appearance times."""

    out: list[Frame] = []
    t = PRE_DELAY
    for line in lines:
        out.append(Frame(line=line, t=round(t, 3)))
        t += LINE_DELAY
    return out


def _render_cast(frames: list[Frame]) -> str:
    """Emit asciinema v2 (JSONL) for the captured frames."""

    header = {
        "version": 2,
        "width": CAST_WIDTH,
        "height": CAST_HEIGHT,
        "timestamp": CAST_TIMESTAMP,
        "title": CAST_TITLE,
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    out_lines = [json.dumps(header, separators=(", ", ": "))]
    prompt = "$ python -m contextweaver demo\r\n"
    out_lines.append(json.dumps([0.05, "o", prompt]))
    for fr in frames:
        # asciinema expects "\r\n" line terminators.
        out_lines.append(json.dumps([fr.t, "o", fr.line + "\r\n"]))
    return "\n".join(out_lines) + "\n"


def _svg_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _classify(line: str) -> str:
    """Map a line to one of the visual classes used in the SVG palette."""

    if line.startswith("===="):
        return "rule"
    if line.startswith("[") and "/5]" in line[:6]:
        return "step"
    if line.startswith("      ") and ("score=" in line or "(tool)" in line):
        return "card"
    if line.strip().startswith("[") and line.strip().endswith("]"):
        return "section"
    if line.startswith("Demo complete"):
        return "done"
    return "body"


_CLASS_FILL = {
    "rule": SVG_ACCENT,
    "step": SVG_PROMPT,
    "card": SVG_FG,
    "section": SVG_ACCENT,
    "done": SVG_PROMPT,
    "body": SVG_FG,
}


def _render_svg(frames: list[Frame]) -> str:
    """Render an animated SVG terminal recording of the frames."""

    width = SVG_LEFT_PAD * 2 + CAST_WIDTH * (SVG_FONT_SIZE * 0.6)
    # +1 row for the prompt line, +1 row of bottom padding
    rows = len(frames) + 2
    height = SVG_TOP_PAD + rows * SVG_LINE_HEIGHT + SVG_LINE_HEIGHT

    total_dur = frames[-1].t + 1.5 if frames else 2.0

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {int(width)} {int(height)}" '
        f'width="{int(width)}" height="{int(height)}" '
        f'role="img" aria-label="{_svg_escape(CAST_TITLE)}">'
    )
    parts.append(f'<rect width="100%" height="100%" rx="8" ry="8" fill="{SVG_BG}"/>')
    # Window chrome.
    parts.append(
        f'<rect x="0" y="0" width="100%" height="28" rx="8" ry="8" fill="{SVG_HEADER_BG}"/>'
    )
    for i, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        parts.append(f'<circle cx="{14 + i * 18}" cy="14" r="6" fill="{color}"/>')
    parts.append(
        f'<text x="{int(width / 2)}" y="18" font-family="{SVG_FONT_FAMILY}" '
        f'font-size="11" fill="{SVG_DIM}" text-anchor="middle">'
        f"{_svg_escape(CAST_TITLE)}</text>"
    )

    # Prompt line (always visible).
    prompt_y = SVG_TOP_PAD + SVG_LINE_HEIGHT
    parts.append(
        f'<text x="{SVG_LEFT_PAD}" y="{prompt_y}" font-family="{SVG_FONT_FAMILY}" '
        f'font-size="{SVG_FONT_SIZE}" fill="{SVG_PROMPT}">$ </text>'
        f'<text x="{SVG_LEFT_PAD + 14}" y="{prompt_y}" '
        f'font-family="{SVG_FONT_FAMILY}" font-size="{SVG_FONT_SIZE}" '
        f'fill="{SVG_FG}">python -m contextweaver demo</text>'
    )

    # One animated line per frame.
    for idx, fr in enumerate(frames):
        y = SVG_TOP_PAD + (idx + 2) * SVG_LINE_HEIGHT
        klass = _classify(fr.line)
        fill = _CLASS_FILL[klass]
        text = _svg_escape(fr.line) or "&#160;"  # nbsp so empty lines hold rhythm
        parts.append(
            f'<text x="{SVG_LEFT_PAD}" y="{y}" font-family="{SVG_FONT_FAMILY}" '
            f'font-size="{SVG_FONT_SIZE}" fill="{fill}" opacity="0">'
            f"{text}"
            f'<animate attributeName="opacity" from="0" to="1" '
            f'begin="{fr.t}s" dur="0.18s" fill="freeze"/>'
            f"</text>"
        )

    # Looping blinking cursor at the end.
    cursor_y = SVG_TOP_PAD + (len(frames) + 2) * SVG_LINE_HEIGHT
    parts.append(
        f'<rect x="{SVG_LEFT_PAD}" y="{cursor_y - 12}" width="8" height="14" '
        f'fill="{SVG_FG}" opacity="0">'
        f'<animate attributeName="opacity" values="0;1;1;0" '
        f'keyTimes="0;0.05;0.95;1" begin="{frames[-1].t + 0.3}s;cursor_blink.end" '
        f'dur="1s" repeatCount="indefinite" id="cursor_blink"/>'
        f"</rect>"
    )

    # Hint that the full recording loops via the cursor animation; the
    # asciinema-style "replay" affordance is implicit (refresh the page).
    parts.append(
        f'<text x="{int(width) - SVG_LEFT_PAD}" y="18" '
        f'font-family="{SVG_FONT_FAMILY}" font-size="10" '
        f'fill="{SVG_DIM}" text-anchor="end">'
        f"{total_dur:.0f}s · animated SVG</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _write_if_changed(path: Path, content: str) -> bool:
    """Write *content* to *path* if it differs.  Returns True on change."""

    if path.exists() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the on-disk artifacts would change",
    )
    args = parser.parse_args(argv)

    lines = _capture_demo()
    if not lines:
        sys.stderr.write("error: demo produced no output\n")
        return 1
    frames = _frames(lines)
    cast = _render_cast(frames)
    svg = _render_svg(frames)

    if args.check:
        drift = False
        for path, expected in ((CAST_PATH, cast), (SVG_PATH, svg)):
            if not path.exists() or path.read_text() != expected:
                sys.stderr.write(f"drift: {path.relative_to(ROOT)}\n")
                drift = True
        if drift:
            sys.stderr.write("Re-run `python scripts/record_demo.py` to refresh.\n")
            return 1
        return 0

    changed_cast = _write_if_changed(CAST_PATH, cast)
    changed_svg = _write_if_changed(SVG_PATH, svg)
    if changed_cast or changed_svg:
        sys.stdout.write(
            f"wrote {CAST_PATH.relative_to(ROOT)} "
            f"({'changed' if changed_cast else 'unchanged'}), "
            f"{SVG_PATH.relative_to(ROOT)} "
            f"({'changed' if changed_svg else 'unchanged'})\n"
        )
    else:
        sys.stdout.write(
            f"no changes — {CAST_PATH.relative_to(ROOT)} and "
            f"{SVG_PATH.relative_to(ROOT)} are up to date\n"
        )
    # asciinema CLI is optional; surface a one-line hint if it's present
    # so contributors know they can validate the cast locally.
    if shutil.which("asciinema"):
        sys.stdout.write(
            f"(asciinema detected: `asciinema play {CAST_PATH.relative_to(ROOT)}` to replay)\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
