"""contextweaver — dynamic context management for tool-using AI agents.

Two integrated engines:

* **Context Engine** — phase-specific budgeted context compilation with a
  context firewall (raw tool outputs stored out-of-band; LLM sees summaries,
  handles, and structured extractions).

* **Routing Engine** — bounded-choice navigation over large tool catalogs via
  a DAG + beam search + LLM-friendly choice cards.

Quick start::

    from contextweaver.types import Phase, ContextItem, ItemKind
    from contextweaver.config import ContextBudget

    budget = ContextBudget()
    print(budget.for_phase(Phase.answer))  # 6000
"""

from contextweaver import config, exceptions, protocols, types
from contextweaver._utils import TfIdfScorer, jaccard, tokenize
from contextweaver.types import (
    ArtifactRef,
    BuildStats,
    ChoiceCard,
    ContextItem,
    ContextPack,
    ItemKind,
    Phase,
    ResultEnvelope,
    SelectableItem,
    Sensitivity,
    ToolCard,
    ViewSpec,
)

__version__ = "0.1.0"
__all__ = [
    # sub-modules
    "config",
    "exceptions",
    "protocols",
    "types",
    # utilities
    "TfIdfScorer",
    "jaccard",
    "tokenize",
    # types
    "ArtifactRef",
    "BuildStats",
    "ChoiceCard",
    "ContextItem",
    "ContextPack",
    "ItemKind",
    "Phase",
    "ResultEnvelope",
    "SelectableItem",
    "Sensitivity",
    "ToolCard",
    "ViewSpec",
]
