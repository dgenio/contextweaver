#!/usr/bin/env python3
"""Generate and gate the public-API manifest (issue #518).

The README and ``docs/stability.md`` promise that documented public APIs "change
deliberately".  The repo already gates generated schemas, scorecards, and
``llms.txt`` against drift — but the most important generated artifact of all,
the public API surface itself, had no drift gate.  This script renders a
deterministic, signature-level snapshot of the public surface and gates it like
any other generated artifact, so every addition, removal, or signature change to
the public API is an explicit, reviewable diff.

The public surface is defined exactly as the package defines it: the top-level
``contextweaver.__all__`` plus the ``__all__`` of each public subpackage.  For
every exported name we record:

- functions: ``def name(signature) -> return``
- classes: ``class Name(constructor-signature)`` plus each public method's
  signature (``Name.method(...)``)
- everything else (constants, sentinels): ``name: <type>``

Usage::

    python scripts/gen_api_manifest.py            # regenerate api/public_api.txt
    python scripts/gen_api_manifest.py --check     # exit non-zero on drift

Wired into ``make api-check`` and the unified ``make drift-check`` gate.
"""

from __future__ import annotations

import argparse
import enum
import importlib
import inspect
from collections.abc import Sequence

from _golden import REPO_ROOT, _rel, check_text_artifacts, write_text_artifacts

MANIFEST_PATH = REPO_ROOT / "api" / "public_api.txt"

# Public surface = the top-level package plus every subpackage that curates an
# ``__all__``.  Keep this list sorted; it is the only hand-maintained input.
PUBLIC_MODULES = (
    "contextweaver",
    "contextweaver.adapters",
    "contextweaver.context",
    "contextweaver.data",
    "contextweaver.eval",
    "contextweaver.routing",
    "contextweaver.store",
    "contextweaver.summarize",
)


class _StableDefault:
    """Sentinel whose ``repr`` is a constant ``...``.

    ``inspect.Signature`` renders parameter defaults via ``repr(default)``, and
    those reprs are *not* stable across Python versions (enum members and some
    objects render differently on 3.10 vs 3.12). Replacing every default with
    this sentinel keeps the manifest byte-identical across the CI matrix while
    still recording *that* a parameter has a default — drift detection on param
    names, types, and arity is unaffected.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "..."


_STABLE_DEFAULT = _StableDefault()


def _signature(obj: object) -> str:
    """Version-independent signature string; stable and never raises.

    Parameter annotations are recorded verbatim (they are string annotations
    under ``from __future__ import annotations``, so they are already stable);
    default *values* are normalised to ``...`` so the manifest does not drift on
    Python-version-specific default reprs.
    """
    try:
        sig = inspect.signature(obj)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return "(...)"
    params = [
        p.replace(default=_STABLE_DEFAULT) if p.default is not inspect.Parameter.empty else p
        for p in sig.parameters.values()
    ]
    try:
        return str(sig.replace(parameters=params))
    except (ValueError, TypeError):
        return "(...)"


def _class_lines(name: str, cls: type) -> list[str]:
    """Render a class as its constructor signature plus public method signatures."""
    bases = ", ".join(b.__name__ for b in cls.__bases__ if b is not object)
    header = f"class {name}({bases})" if bases else f"class {name}"
    if issubclass(cls, enum.Enum):
        # ``inspect.signature`` on an Enum returns the *metaclass* ``__call__``,
        # whose rendering changes across Python versions (``(value, names=...)``
        # on 3.10/3.11 vs ``(*values)`` on 3.12+). The stable, meaningful API of
        # an enum is its member set, so record that instead of the constructor.
        members = ", ".join(member.name for member in cls)
        lines = [header, f"    enum members: {members}"]
    else:
        lines = [f"{header}{_signature(cls)}"]
    for attr in sorted(vars(cls)):
        if attr.startswith("_"):
            continue
        member = inspect.getattr_static(cls, attr, None)
        if inspect.isfunction(member) or isinstance(member, (staticmethod, classmethod)):
            func = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
            lines.append(f"    def {attr}{_signature(func)}")
    return lines


def _render_member(name: str, obj: object) -> list[str]:
    if inspect.isclass(obj):
        return _class_lines(name, obj)
    if inspect.isfunction(obj) or inspect.isbuiltin(obj):
        return [f"def {name}{_signature(obj)}"]
    if callable(obj) and not isinstance(obj, type):
        # Callable instance (e.g. a configured estimator); record its type.
        return [f"{name}: {type(obj).__name__}"]
    return [f"{name}: {type(obj).__name__}"]


def render_manifest() -> str:
    """Render the deterministic public-API manifest text."""
    out: list[str] = [
        "# contextweaver public API manifest (generated by scripts/gen_api_manifest.py)",
        "# Drift here means the public surface changed — review the diff deliberately.",
        "",
    ]
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        exported: Sequence[str] = getattr(module, "__all__", ())
        out.append(f"## {module_name}")
        if not exported:
            out.append("  (no __all__)")
            out.append("")
            continue
        for name in sorted(exported):
            obj = getattr(module, name)
            for line in _render_member(name, obj):
                out.append(f"  {line}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Render or check the public-API manifest. Returns 0 on success, 1 on drift."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the manifest is stale instead of writing it.",
    )
    args = parser.parse_args(argv)

    rendered = {MANIFEST_PATH: render_manifest()}
    if args.check:
        return check_text_artifacts(rendered, label="public-API manifest", regen="make api")
    write_text_artifacts(rendered)
    print(f"wrote {_rel(MANIFEST_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
