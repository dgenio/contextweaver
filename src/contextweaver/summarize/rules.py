"""Rule-based summarisation for contextweaver.

Provides a simple rule engine that maps media-type patterns and metadata tags
to summarisation strategies.  Concrete summariser implementations go in
separate modules; this module defines the rule table and dispatch logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SummarizationRule:
    """A single rule mapping a condition to a summariser name.

    Attributes:
        media_type_prefix: Match if the artifact's media_type starts with this
            string.  Empty string matches everything.
        required_tags: All listed tags must be present in the metadata's
            ``tags`` list for the rule to fire.
        summarizer_name: Logical name of the summariser to use when this rule
            fires.  Resolved at runtime by the summarisation dispatcher.
        priority: Higher priority rules are evaluated first.
    """

    media_type_prefix: str = ""
    required_tags: list[str] = field(default_factory=list)
    summarizer_name: str = "default"
    priority: int = 0

    def matches(self, media_type: str, metadata: dict[str, Any]) -> bool:
        """Return ``True`` if this rule fires for the given artifact context.

        Args:
            media_type: The MIME type of the artifact.
            metadata: Arbitrary metadata dict associated with the artifact.

        Returns:
            ``True`` when all conditions are satisfied.
        """
        if self.media_type_prefix and not media_type.startswith(self.media_type_prefix):
            return False
        tags: list[str] = metadata.get("tags", [])
        return all(t in tags for t in self.required_tags)


class RuleEngine:
    """Dispatch summarisation by evaluating a ranked list of :class:`SummarizationRule` objects.

    Rules are sorted by descending priority; the first match wins.
    """

    def __init__(self, rules: list[SummarizationRule] | None = None) -> None:
        self._rules: list[SummarizationRule] = sorted(
            rules or [], key=lambda r: r.priority, reverse=True
        )

    def add_rule(self, rule: SummarizationRule) -> None:
        """Append *rule* and re-sort the rule list.

        Args:
            rule: The rule to add.
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def resolve(self, media_type: str, metadata: dict[str, Any]) -> str:
        """Return the summariser name for the first matching rule.

        Args:
            media_type: MIME type of the artifact.
            metadata: Metadata dict.

        Returns:
            The ``summarizer_name`` of the first matching rule, or ``"default"``
            if no rule matches.
        """
        for rule in self._rules:
            if rule.matches(media_type, metadata):
                return rule.summarizer_name
        return "default"

    def all_rules(self) -> list[SummarizationRule]:
        """Return a copy of the rule list in priority order."""
        return list(self._rules)
