"""Summarize sub-package for contextweaver.

Exports the rule engine, extraction utilities, and concrete summarizer/extractor
implementations used by the context firewall.
"""

from __future__ import annotations

from contextweaver.summarize.extract import (
    StructuredExtractor,
    extract_bullet_list,
    extract_facts,
    extract_key_value_pairs,
    extract_numbered_list,
)
from contextweaver.summarize.rules import RuleBasedSummarizer, RuleEngine, SummarizationRule
from contextweaver.summarize.structured import StructuredFirewall, parse_path, project

__all__ = [
    "RuleBasedSummarizer",
    "RuleEngine",
    "StructuredExtractor",
    "StructuredFirewall",
    "SummarizationRule",
    "extract_bullet_list",
    "extract_facts",
    "extract_key_value_pairs",
    "extract_numbered_list",
    "parse_path",
    "project",
]
