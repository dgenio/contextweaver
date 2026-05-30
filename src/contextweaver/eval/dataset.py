"""Gold-standard dataset types for the evaluation harness (issue #12).

An :class:`EvalDataset` is an ordered collection of :class:`EvalCase`
entries, each pairing a natural-language ``query`` with the set of
``expected`` tool ids that a correct router should surface.  The JSON
on-disk shape matches ``benchmarks/routing_gold.json``::

    [
      {"query": "export the audit log",
       "expected": ["admin.audit.export"],
       "tags": ["admin", "audit"],
       "namespace": "admin"}
    ]

Datasets are deserialised with :meth:`EvalDataset.load`; both types carry
the repo-standard ``to_dict`` / ``from_dict`` round-trip helpers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.exceptions import ConfigError

__all__ = ["EvalCase", "EvalDataset"]


@dataclass(frozen=True)
class EvalCase:
    """A single ``query -> expected tool ids`` evaluation case.

    Attributes:
        query: The user query string to route.
        expected: Tool ids that count as a correct match for *query*.
            At least one expected id should appear in the router's
            shortlist for the case to be considered satisfied.
        tags: Optional free-form tags describing the case (domain,
            difficulty, ...).  Not used for scoring.
        namespace: Optional namespace label for per-namespace breakdowns.
    """

    query: str
    expected: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    namespace: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "query": self.query,
            "expected": list(self.expected),
            "tags": list(self.tags),
            "namespace": self.namespace,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalCase:
        """Build an :class:`EvalCase` from a raw dict.

        Raises:
            ConfigError: If ``query`` is missing/blank or ``expected`` is
                not a list of strings.
        """
        query = data.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ConfigError("Eval case requires a non-empty 'query' string.")
        raw_expected = data.get("expected", [])
        if not isinstance(raw_expected, list) or not all(isinstance(e, str) for e in raw_expected):
            raise ConfigError(f"Eval case 'expected' must be a list of strings: {query!r}")
        raw_tags = data.get("tags", [])
        tags = [t for t in raw_tags if isinstance(t, str)] if isinstance(raw_tags, list) else []
        namespace = data.get("namespace", "")
        return cls(
            query=query,
            expected=list(raw_expected),
            tags=tags,
            namespace=namespace if isinstance(namespace, str) else "",
        )


@dataclass
class EvalDataset:
    """An ordered collection of :class:`EvalCase` entries."""

    cases: list[EvalCase] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self) -> Iterator[EvalCase]:
        return iter(self.cases)

    @classmethod
    def load(cls, path: str | Path) -> EvalDataset:
        """Load a dataset from a JSON file.

        The file must contain a top-level JSON array of case objects.

        Raises:
            ConfigError: If the file is not a JSON array.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ConfigError(f"Eval dataset must be a JSON array, got {type(data).__name__}.")
        return cls.from_dict({"cases": data})

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"cases": [c.to_dict() for c in self.cases]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalDataset:
        """Build an :class:`EvalDataset` from a raw dict.

        Accepts either ``{"cases": [...]}`` or is tolerant of a bare list
        passed through :meth:`load`.
        """
        raw_cases = data.get("cases", [])
        if not isinstance(raw_cases, list):
            raise ConfigError("Eval dataset 'cases' must be a list.")
        return cls(cases=[EvalCase.from_dict(c) for c in raw_cases])
