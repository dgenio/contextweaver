"""Generate a small contextweaver adapter scaffold.

This script is intentionally local-only: it performs no network calls, does
not install packages, and refuses to overwrite files unless --force is used.

Usage:

    python scaffolds/new_adapter.py my_provider --dry-run
    python scaffolds/new_adapter.py my_provider
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALID_MODULE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ScaffoldFile:
    """A file emitted by the scaffold generator."""

    path: Path
    content: str


def _module_name(raw: str) -> str:
    """Normalize *raw* to a safe Python module name."""
    name = raw.strip().lower().replace("-", "_")
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not _VALID_MODULE.fullmatch(name):
        raise SystemExit(
            "Adapter name must normalize to a Python module name like "
            "'my_provider' or 'provider_sdk'."
        )
    return name


def _title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("_"))


def _adapter_py(name: str) -> str:
    title = _title(name)
    return f'''"""{title} adapter scaffold.

Replace the TODO sections with provider-specific conversions. Keep provider
SDK imports guarded so importing ``contextweaver.adapters.{name}`` does not
require optional dependencies in a base install.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from contextweaver.types import ContextItem, ItemKind, SelectableItem


class {title.replace(" ", "")}AdapterError(RuntimeError):
    """Raised when {title} objects cannot be converted safely."""


def _require_provider_sdk() -> Any:
    """Import the optional provider SDK lazily.

    Replace ``{name}_sdk`` with the real package name. This helper keeps the
    base contextweaver install free of provider-specific dependencies.
    """
    try:
        import {name}_sdk  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional SDK
        raise {title.replace(" ", "")}AdapterError(
            "Install the provider SDK extra before using the {title} adapter."
        ) from exc
    return {name}_sdk


def from_{name}_tools(tools: Iterable[Mapping[str, Any]]) -> list[SelectableItem]:
    """Convert {title} tool descriptions into contextweaver SelectableItems.

    Public return values must be contextweaver primitives, not provider SDK
    objects. Keep only serialisable metadata needed for routing/hydration.
    """
    items: list[SelectableItem] = []
    for raw in tools:
        tool_id = str(raw.get("id") or raw.get("name") or "").strip()
        if not tool_id:
            raise {title.replace(" ", "")}AdapterError("Tool is missing an id/name")
        name_value = str(raw.get("name") or tool_id)
        description = str(raw.get("description") or name_value)
        namespace = str(raw.get("namespace") or "{name}")
        tags = [str(tag) for tag in raw.get("tags", [])]
        items.append(
            SelectableItem(
                id=tool_id,
                kind="tool",
                name=name_value,
                description=description,
                namespace=namespace,
                tags=tags,
                metadata={{"adapter": "{name}"}},
            )
        )
    return items


def from_{name}_history(messages: Sequence[Mapping[str, Any]]) -> list[ContextItem]:
    """Convert {title} conversation history into ContextItems.

    TODO: Map provider-specific roles to ItemKind values. This starter maps
    ``role == 'user'`` to ``ItemKind.user_turn`` and everything else to
    ``ItemKind.assistant_turn``.
    """
    items: list[ContextItem] = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or "assistant")
        text = str(message.get("content") or "")
        kind = ItemKind.user_turn if role == "user" else ItemKind.assistant_turn
        items.append(
            ContextItem(
                id=str(message.get("id") or f"{name}-msg-{{index}}"),
                kind=kind,
                text=text,
                metadata={{"adapter": "{name}", "role": role}},
            )
        )
    return items


__all__ = [
    "{title.replace(" ", "")}AdapterError",
    "from_{name}_history",
    "from_{name}_tools",
]
'''


def _test_py(name: str) -> str:
    class_name = _title(name).replace(" ", "") + "AdapterError"
    return f'''from __future__ import annotations

import pytest

from contextweaver.adapters.{name} import (
    {class_name},
    from_{name}_history,
    from_{name}_tools,
)
from contextweaver.types import ItemKind


def test_from_{name}_tools_converts_plain_dicts() -> None:
    items = from_{name}_tools(
        [
            {{
                "id": "{name}.search",
                "name": "search",
                "description": "Search provider data",
                "tags": ["search"],
            }}
        ]
    )

    assert len(items) == 1
    assert items[0].id == "{name}.search"
    assert items[0].namespace == "{name}"
    assert items[0].metadata["adapter"] == "{name}"


def test_from_{name}_tools_rejects_missing_id() -> None:
    with pytest.raises({class_name}):
        from_{name}_tools([{{"description": "missing id"}}])


def test_from_{name}_history_converts_roles() -> None:
    items = from_{name}_history(
        [
            {{"role": "user", "content": "hello"}},
            {{"role": "assistant", "content": "hi"}},
        ]
    )

    assert [item.kind for item in items] == [ItemKind.user_turn, ItemKind.assistant_turn]
    assert all(item.metadata["adapter"] == "{name}" for item in items)
'''


def _docs_md(name: str) -> str:
    title = _title(name)
    return f'''# {title} integration

This page is a scaffold for a {title} adapter. Replace this text with provider-specific setup and examples.

## Install

Keep provider SDK dependencies optional. A base `pip install contextweaver` should still work without the {title} SDK installed.

```bash
pip install contextweaver
# Optional provider SDK install goes here.
```

## Adapter functions

The scaffold creates:

- `from_{name}_tools(...)` for converting provider tool descriptions into `SelectableItem` values.
- `from_{name}_history(...)` for converting provider message history into `ContextItem` values.

## Invariants

- Provider SDK imports are guarded or local to functions.
- Public return values are contextweaver primitives, not provider SDK objects.
- Examples run without API keys or network access unless explicitly marked otherwise.
- Tests use fake data only.
- Tool output that may be large or sensitive should pass through the context firewall before becoming prompt-visible.

## Local checks

```bash
python examples/{name}_adapter_demo.py
python -m pytest tests/test_adapters_{name}.py
```
'''


def _example_py(name: str) -> str:
    return f'''"""Offline demo for the {name} adapter scaffold."""

from __future__ import annotations

from contextweaver.adapters.{name} import from_{name}_history, from_{name}_tools


def main() -> None:
    tools = from_{name}_tools(
        [
            {{
                "id": "{name}.search",
                "name": "search",
                "description": "Search fake provider records",
                "tags": ["search", "demo"],
            }}
        ]
    )
    history = from_{name}_history([{{"role": "user", "content": "find the demo record"}}])

    print(f"converted tools: {{[tool.id for tool in tools]}}")
    print(f"converted history items: {{[item.kind.value for item in history]}}")


if __name__ == "__main__":
    main()
'''


def _files_for(name: str) -> list[ScaffoldFile]:
    return [
        ScaffoldFile(Path("src") / "contextweaver" / "adapters" / f"{name}.py", _adapter_py(name)),
        ScaffoldFile(Path("tests") / f"test_adapters_{name}.py", _test_py(name)),
        ScaffoldFile(Path("docs") / f"integration_{name}.md", _docs_md(name)),
        ScaffoldFile(Path("examples") / f"{name}_adapter_demo.py", _example_py(name)),
    ]


def _write(files: list[ScaffoldFile], *, dry_run: bool, force: bool) -> None:
    for file in files:
        absolute = _REPO_ROOT / file.path
        if absolute.exists() and not force:
            raise SystemExit(f"Refusing to overwrite existing file: {file.path} (use --force)")

    for file in files:
        absolute = _REPO_ROOT / file.path
        if dry_run:
            print(f"would write {file.path}")
            continue
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(file.content, encoding="utf-8")
        print(f"wrote {file.path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a contextweaver adapter scaffold")
    parser.add_argument("name", help="Provider name, e.g. my_provider")
    parser.add_argument("--dry-run", action="store_true", help="Print files that would be written")
    parser.add_argument("--force", action="store_true", help="Overwrite existing scaffold files")
    args = parser.parse_args()

    name = _module_name(args.name)
    _write(_files_for(name), dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
