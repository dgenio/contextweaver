"""Library verification helpers for the CLI ``verify`` subcommand (issue #657).

Pure, deterministic, network-free checks that give first-run adopters
confidence the library is installed correctly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _VerifyCheck:
    name: str
    ok: bool
    detail: str
    fix_hint: str | None = None


def _check_import() -> _VerifyCheck:
    """Verify contextweaver imports and version is readable."""
    try:
        from contextweaver._version import __version__

        return _VerifyCheck(name="import", ok=True, detail=f"version {__version__}")
    except Exception as exc:
        return _VerifyCheck(
            name="import",
            ok=False,
            detail=str(exc),
            fix_hint="Ensure contextweaver is installed: pip install contextweaver",
        )


def _check_manager() -> _VerifyCheck:
    """Verify ContextManager instantiates without error."""
    try:
        from contextweaver.context.manager import ContextManager

        mgr = ContextManager()
        event_count = len(mgr.event_log.all())
        artifact_count = len(mgr.artifact_store.list_refs())
        return _VerifyCheck(
            name="manager",
            ok=True,
            detail=f"event_log={event_count}, artifact_store={artifact_count}",
        )
    except Exception as exc:
        return _VerifyCheck(
            name="manager",
            ok=False,
            detail=str(exc),
            fix_hint="Check that core dependencies (tiktoken, PyYAML, rank-bm25) are installed",
        )


def _check_build() -> _VerifyCheck:
    """Verify a minimal context build produces a non-empty pack."""
    try:
        from contextweaver.context.manager import ContextManager
        from contextweaver.types import ContextItem, ItemKind, Phase

        mgr = ContextManager()
        mgr.ingest(
            ContextItem(
                id="u1",
                kind=ItemKind.user_turn,
                text="Hello, how many active users?",
            )
        )
        mgr.ingest(
            ContextItem(
                id="a1",
                kind=ItemKind.agent_msg,
                text="I'll check that for you.",
            )
        )
        mgr.ingest(
            ContextItem(
                id="tc1",
                kind=ItemKind.tool_call,
                text='db_query(sql="SELECT COUNT(*) FROM users")',
                parent_id="u1",
            )
        )
        mgr.ingest(
            ContextItem(
                id="tr1",
                kind=ItemKind.tool_result,
                text="count: 1042",
                parent_id="tc1",
            )
        )
        pack = mgr.build_sync(phase=Phase.answer, query="active user count")
        prompt_tokens = pack.stats.prompt_tokens
        included = pack.stats.included_count
        return _VerifyCheck(
            name="build",
            ok=True,
            detail=f"prompt_tokens={prompt_tokens}, included={included}",
        )
    except Exception as exc:
        return _VerifyCheck(
            name="build",
            ok=False,
            detail=str(exc),
            fix_hint=(
                "File an issue with the full traceback at github.com/dgenio/contextweaver/issues"
            ),
        )


def _check_tokens() -> _VerifyCheck:
    """Verify the token counter returns a positive count."""
    try:
        from contextweaver.tokens import count as count_tokens

        sample = "Hello, world! This is a sample text for token counting."
        n = count_tokens(sample)
        return _VerifyCheck(
            name="tokens",
            ok=True,
            detail=f"count={n} for {len(sample)} chars",
        )
    except Exception as exc:
        return _VerifyCheck(
            name="tokens",
            ok=False,
            detail=str(exc),
            fix_hint="tiktoken cache may be missing; set TIKTOKEN_CACHE_DIR",
        )


def _check_routing() -> _VerifyCheck:
    """Verify a minimal routing graph builds and routes a query."""
    try:
        from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
        from contextweaver.routing.router import Router
        from contextweaver.routing.tree import TreeBuilder

        raw_items = generate_sample_catalog(n=10, seed=42)
        items = load_catalog_dicts(raw_items)
        graph = TreeBuilder(max_children=5).build(items)
        router = Router(graph, items=items, beam_width=2, top_k=5)
        result = router.route("send an email")
        candidates = len(result.candidate_ids)
        top1 = result.candidate_ids[0] if result.candidate_ids else "none"
        return _VerifyCheck(
            name="routing",
            ok=True,
            detail=f"candidates={candidates}, top1={top1}",
        )
    except Exception as exc:
        return _VerifyCheck(
            name="routing",
            ok=False,
            detail=str(exc),
            fix_hint=(
                "File an issue with the full traceback at github.com/dgenio/contextweaver/issues"
            ),
        )
