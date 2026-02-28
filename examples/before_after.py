"""Before/after context firewall example.

Illustrates the difference between a raw tool result (without firewall) and
the processed version (with firewall) that the LLM actually sees.
"""

from __future__ import annotations

from contextweaver.context.firewall import apply_firewall
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ContextItem, ItemKind

RAW = """\
HTTP/1.1 200 OK
Content-Type: application/json
Content-Length: 512

{
  "users": [
    {"id": 1, "name": "Alice", "email": "alice@example.com", "created_at": "2024-01-01"},
    {"id": 2, "name": "Bob",   "email": "bob@example.com",   "created_at": "2024-01-02"}
  ],
  "total": 2,
  "page": 1
}
"""


def main() -> None:
    item = ContextItem(id="r1", kind=ItemKind.tool_result, text=RAW)
    store = InMemoryArtifactStore()

    print("=== BEFORE (raw — never shown to LLM) ===")
    print(item.text)
    print(f"Tokens (est.): ~{len(item.text) // 4}")

    processed, envelope = apply_firewall(item, store)

    print("\n=== AFTER (what LLM sees) ===")
    print(processed.text)
    print(f"Tokens (est.): ~{len(processed.text) // 4}")

    if envelope:
        print(f"\nExtracted facts: {envelope.facts}")


if __name__ == "__main__":
    main()
