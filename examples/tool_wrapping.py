"""Tool wrapping example.

Shows how to wrap a raw tool output through the context firewall to produce
a ResultEnvelope with summary, facts, and an artifact reference.
"""

from __future__ import annotations

from contextweaver.context.firewall import apply_firewall
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ContextItem, ItemKind

RAW_TOOL_OUTPUT = """
status: ok
rows_found: 42
execution_time_ms: 123

1. Alice Smith (id=1, email=alice@example.com)
2. Bob Jones (id=2, email=bob@example.com)
3. Carol White (id=3, email=carol@example.com)

- Total pages: 5
- Page size: 10
"""


def main() -> None:
    item = ContextItem(
        id="tr1",
        kind=ItemKind.tool_result,
        text=RAW_TOOL_OUTPUT.strip(),
        metadata={"tool": "db_query"},
    )
    store = InMemoryArtifactStore()
    processed, envelope = apply_firewall(item, store)

    print("=== Processed Item (LLM sees this) ===")
    print(processed.text)
    print(f"\nArtifact ref: {processed.artifact_ref}")

    print("\n=== ResultEnvelope ===")
    if envelope:
        print(f"Status: {envelope.status}")
        print(f"Summary: {envelope.summary}")
        print(f"Facts ({len(envelope.facts)}):")
        for fact in envelope.facts:
            print(f"  - {fact}")

    print("\n=== Artifact Store ===")
    for ref in store.list_refs():
        raw = store.get(ref.handle)
        print(f"Handle: {ref.handle}, size: {ref.size_bytes} bytes")
        print(f"First 80 chars of raw: {raw[:80].decode()!r}")


if __name__ == "__main__":
    main()
