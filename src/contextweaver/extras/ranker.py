"""Telemetry-trained tool ranker (issue #388), gated behind the ``[ranker]`` extra.

Turns gateway diagnostics (:class:`~contextweaver.diagnostics.DiagnosticEvent`
streams) into training examples and fits a small logistic-regression reranker
over deterministic lexical/metadata features.  Install via
``pip install 'contextweaver[ranker]'``.

Importing this module succeeds without scikit-learn; only instantiating
:class:`ToolRanker` raises ``ImportError`` with the install hint — the same
convention as :mod:`contextweaver.extras.embeddings`.  Everything else
(:func:`examples_from_events`, :func:`featurize`, :class:`RankingExample`)
works without the extra.  Persistence is JSON (coefficients + model card),
never pickle.

Telemetry-example derivation lives in the private sibling
:mod:`contextweaver.extras._ranker_examples` (module-size ceiling); the
public names are re-exported here — import them from this module.
"""

from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from contextweaver._utils import jaccard, tokenize
from contextweaver.eval.metrics import recall_at_k, reciprocal_rank
from contextweaver.exceptions import ConfigError
from contextweaver.extras._ranker_examples import RankingExample, examples_from_events

if TYPE_CHECKING:
    from contextweaver.types import SelectableItem

_INSTALL_HINT = (
    "contextweaver.extras.ranker requires the [ranker] extra: pip install 'contextweaver[ranker]'"
)

# Module-level guard, mirroring the sentence-transformers guard in
# extras/embeddings.py.  Resolved via importlib so the module type-checks
# without sklearn stubs; ``None`` means the extra is not installed.
try:  # pragma: no cover - exercised in the ranker-extra test path
    LogisticRegression: Any = importlib.import_module("sklearn.linear_model").LogisticRegression
except ImportError:  # pragma: no cover - exercised in the default-install path
    LogisticRegression = None

#: Stable feature order used to vectorise :func:`featurize` output.
FEATURE_NAMES: tuple[str, ...] = (
    "cost_hint",
    "jaccard",
    "name_exact",
    "namespace_match",
    "schema_size",
    "side_effects",
    "tag_overlap",
)

_MODEL_FILE_VERSION = 1


def featurize(query: str, item: SelectableItem) -> dict[str, float]:
    """Return the deterministic feature map for a (query, item) pair.

    Features (see :data:`FEATURE_NAMES` for the stable vector order):
    ``jaccard`` (query vs name+description token overlap), ``name_exact``
    (any query token appears in the normalised name), ``tag_overlap``
    (query vs tag tokens), ``schema_size`` (args-schema property count,
    capped at 10 and scaled to ``[0, 1]``), ``namespace_match``,
    ``cost_hint``, and the ``side_effects`` flag.
    """
    query_tokens = tokenize(query)
    props = item.args_schema.get("properties", {})
    n_props = len(props) if isinstance(props, dict) else 0
    return {
        "cost_hint": float(item.cost_hint),
        "jaccard": jaccard(query_tokens, tokenize(item.name + " " + item.description)),
        "name_exact": 1.0 if query_tokens & tokenize(item.name) else 0.0,
        "namespace_match": 1.0 if query_tokens & tokenize(item.namespace) else 0.0,
        "schema_size": min(n_props, 10) / 10.0,
        "side_effects": 1.0 if item.side_effects else 0.0,
        "tag_overlap": jaccard(query_tokens, tokenize(" ".join(item.tags))),
    }


class ToolRanker:
    """Logistic-regression tool reranker trained on gateway telemetry.

    Training uses scikit-learn (``[ranker]`` extra) with fixed
    ``random_state=0`` and the ``liblinear`` solver for determinism.
    Prediction is pure Python over the extracted coefficients, so a fitted
    or loaded ranker scores identically across processes.

    Raises:
        ImportError: On construction when scikit-learn is not installed.
    """

    def __init__(self) -> None:
        if LogisticRegression is None:
            raise ImportError(_INSTALL_HINT)
        self._coef: list[float] = []
        self._intercept: float = 0.0
        self._feature_names: tuple[str, ...] = FEATURE_NAMES
        #: Provenance: created-from counts, feature list, sklearn version.
        self.model_card: dict[str, Any] = {}

    def fit(
        self,
        examples: list[RankingExample],
        items_by_id: dict[str, SelectableItem],
    ) -> None:
        """Train on *examples* featurized against *items_by_id*.

        The positive label is ``executed and success``.  Examples whose
        ``tool_id`` is missing from *items_by_id* are skipped (counted in the
        model card).  Each trained example's ``features`` field is filled.

        Raises:
            ConfigError: When no trainable examples remain or all labels
                belong to a single class.
        """
        rows: list[list[float]] = []
        labels: list[int] = []
        skipped = 0
        for example in examples:
            item = items_by_id.get(example.tool_id)
            if item is None:
                skipped += 1
                continue
            feats = featurize(example.query, item)
            example.features = feats
            rows.append([feats[name] for name in self._feature_names])
            labels.append(1 if example.executed and example.success else 0)
        if not rows:
            raise ConfigError("no trainable examples: no tool_id matched items_by_id")
        if len(set(labels)) < 2:
            raise ConfigError("training requires both positive and negative examples")
        model = LogisticRegression(random_state=0, solver="liblinear")
        model.fit(rows, labels)
        self._coef = [float(c) for c in model.coef_[0]]
        self._intercept = float(model.intercept_[0])
        version = getattr(importlib.import_module("sklearn"), "__version__", "unknown")
        self.model_card = {
            "feature_names": list(self._feature_names),
            "n_examples": len(rows),
            "n_positive": sum(labels),
            "n_skipped": skipped,
            "sklearn_version": str(version),
        }

    def predict_scores(self, query: str, items: list[SelectableItem]) -> dict[str, float]:
        """Return the sigmoid relevance score per item id for *query*.

        Raises:
            ConfigError: If the ranker has not been fitted or loaded.
        """
        if not self._coef:
            raise ConfigError("ranker is not fitted; call fit() or load() first")
        scores: dict[str, float] = {}
        for item in items:
            feats = featurize(query, item)
            z = self._intercept + sum(
                coef * feats[name]
                for coef, name in zip(self._coef, self._feature_names, strict=True)
            )
            scores[item.id] = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))
        return scores

    def rerank(self, query: str, items: list[SelectableItem]) -> list[SelectableItem]:
        """Return *items* sorted by predicted score descending, ties by id."""
        scores = self.predict_scores(query, items)
        return sorted(items, key=lambda it: (-scores[it.id], it.id))

    def save(self, path: str | Path) -> None:
        """Write coefficients + model card as deterministic JSON (never pickle).

        Raises:
            ConfigError: If the ranker has not been fitted or loaded.
        """
        if not self._coef:
            raise ConfigError("ranker is not fitted; nothing to save")
        payload = {
            "version": _MODEL_FILE_VERSION,
            "coef": self._coef,
            "intercept": self._intercept,
            "feature_names": list(self._feature_names),
            "model_card": self.model_card,
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        Path(path).write_text(text + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ToolRanker:
        """Restore a ranker from a :meth:`save` JSON file.

        Raises:
            ConfigError: On a malformed or version-incompatible file.
            ImportError: When scikit-learn is not installed.
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot load ranker from {path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != _MODEL_FILE_VERSION:
            raise ConfigError(f"unsupported ranker file version in {path}")
        names = tuple(str(n) for n in data.get("feature_names", []))
        coef = [float(c) for c in data.get("coef", [])]
        if len(names) != len(coef) or not coef:
            raise ConfigError(f"ranker file {path} has mismatched coefficients/features")
        ranker = cls()
        ranker._coef = coef
        ranker._intercept = float(data.get("intercept", 0.0))
        ranker._feature_names = names
        ranker.model_card = dict(data.get("model_card", {}))
        return ranker


def evaluate_ranker(
    ranker: ToolRanker,
    examples: list[RankingExample],
    items_by_id: dict[str, SelectableItem],
    k: int = 5,
) -> dict[str, float]:
    """Compare *ranker* against a lexical-only baseline on *examples*.

    Gold ids per query are the successful executed examples.  The baseline
    orders items by the ``jaccard`` feature alone (ties by id).  Metrics are
    :func:`~contextweaver.eval.metrics.recall_at_k` and
    :func:`~contextweaver.eval.metrics.reciprocal_rank`, averaged over
    queries with at least one gold id.  Returns ``{"recall_at_k",
    "baseline_recall_at_k", "mrr", "baseline_mrr", "n_queries"}`` (all zero
    when no query is evaluable).
    """
    gold: dict[str, set[str]] = {}
    for example in examples:
        if example.executed and example.success:
            gold.setdefault(example.query, set()).add(example.tool_id)
    items = sorted(items_by_id.values(), key=lambda it: it.id)
    recalls: list[float] = []
    base_recalls: list[float] = []
    mrrs: list[float] = []
    base_mrrs: list[float] = []
    for query in sorted(gold):
        expected = gold[query]
        ranked = [it.id for it in ranker.rerank(query, items)]
        baseline = [
            it.id for it in sorted(items, key=lambda it: (-featurize(query, it)["jaccard"], it.id))
        ]
        recalls.append(recall_at_k(ranked, expected, k))
        base_recalls.append(recall_at_k(baseline, expected, k))
        mrrs.append(reciprocal_rank(ranked, expected))
        base_mrrs.append(reciprocal_rank(baseline, expected))

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "recall_at_k": _mean(recalls),
        "baseline_recall_at_k": _mean(base_recalls),
        "mrr": _mean(mrrs),
        "baseline_mrr": _mean(base_mrrs),
        "n_queries": float(len(recalls)),
    }


__all__ = [
    "FEATURE_NAMES",
    "RankingExample",
    "ToolRanker",
    "evaluate_ranker",
    "examples_from_events",
    "featurize",
]
