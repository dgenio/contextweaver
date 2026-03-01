"""Summarize sub-package for contextweaver.

Exports the rule engine, extraction utilities, and concrete summarizer/extractor
implementations used by the context firewall.
"""

from contextweaver.summarize.extract import (
    StructuredExtractor,
    extract_bullet_list,
    extract_facts,
    extract_key_value_pairs,
    extract_numbered_list,
)
from contextweaver.summarize.rules import RuleBasedSummarizer, RuleEngine, SummarizationRule

__all__ = [
    "RuleBasedSummarizer",
    "RuleEngine",
    "StructuredExtractor",
    "SummarizationRule",
    "extract_bullet_list",
    "extract_facts",
    "extract_key_value_pairs",
    "extract_numbered_list",
]
