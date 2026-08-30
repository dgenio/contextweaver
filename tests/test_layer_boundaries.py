"""Architecture regression tests for core/adapter dependency direction (#752)."""

from __future__ import annotations

import ast
from pathlib import Path

import contextweaver
from contextweaver._mcp_result import mcp_result_to_envelope as core_mcp_result_to_envelope
from contextweaver.adapters.mcp import mcp_result_to_envelope as adapter_mcp_result_to_envelope


def _imported_modules(path: Path) -> list[str]:
    """Return absolute module names imported by *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


# This is a mechanical architecture gate, not documentation-only guidance:
# context is a core layer and must stay independent of concrete adapter code.
def test_context_layer_does_not_import_adapters() -> None:
    """Core context modules must not depend on adapter implementations."""
    package_root = Path(contextweaver.__file__).resolve().parent
    context_root = package_root / "context"
    violations: list[str] = []

    for path in sorted(context_root.rglob("*.py")):
        for module in _imported_modules(path):
            if module == "contextweaver.adapters" or module.startswith("contextweaver.adapters."):
                violations.append(f"{path.relative_to(package_root)} -> {module}")

    assert violations == [], "core context imports adapter layer:\n" + "\n".join(violations)


def test_mcp_adapter_preserves_result_transform_import_path() -> None:
    """Moving the transform below adapters must not break its existing import path."""
    assert adapter_mcp_result_to_envelope is core_mcp_result_to_envelope
