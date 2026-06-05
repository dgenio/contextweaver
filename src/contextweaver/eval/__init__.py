"""Evaluation harness for contextweaver routing and context (issue #12).

Public API:

- :class:`EvalCase` / :class:`EvalDataset` — gold-standard dataset types.
- :func:`evaluate_routing` → :class:`RoutingEvalReport` — routing quality
  (top-k recall, MRR, confidence gap, beam steps).
- :func:`evaluate_context` → :class:`ContextEvalReport` — context-build
  budget utilisation and token savings versus naive concatenation.
- :func:`recall_at_k` / :func:`precision_at_k` / :func:`reciprocal_rank` —
  canonical rank-based routing metrics shared with ``benchmarks/benchmark.py``
  (issue #354).
"""

from __future__ import annotations

from contextweaver.eval.context import ContextEvalReport, evaluate_context
from contextweaver.eval.dataset import EvalCase, EvalDataset
from contextweaver.eval.metrics import precision_at_k, recall_at_k, reciprocal_rank
from contextweaver.eval.routing import RoutingEvalReport, evaluate_routing

__all__ = [
    "ContextEvalReport",
    "EvalCase",
    "EvalDataset",
    "RoutingEvalReport",
    "evaluate_context",
    "evaluate_routing",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]
