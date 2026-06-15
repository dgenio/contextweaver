"""HTTP sidecar end-to-end demo + language-agnostic Python client (#427/#677).

Starts the contextweaver HTTP sidecar on an ephemeral loopback port in a
background thread, then drives it over plain HTTP/JSON using only the standard
library (``urllib``) — exactly what a non-Python agent would do against the
``/v1/route`` and ``/v1/compact`` endpoints.  Because it spins the server up and
tears it down in-process with no network access, ``make example`` exercises the
whole transport in CI.

Companion TypeScript client: ``examples/sidecar/client.ts``.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Any

from contextweaver.adapters._sidecar_http import make_sidecar_server
from contextweaver.adapters.sidecar import SidecarApp, SidecarConfig
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder


def _build_app() -> SidecarApp:
    """Build a sidecar over a small synthetic catalog."""
    items = load_catalog_dicts(generate_sample_catalog(n=24, seed=11))
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=20)
    return SidecarApp(router=router, config=SidecarConfig())


def _post(base: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST *payload* as JSON to *base + path* and return the decoded body."""
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body: dict[str, Any] = json.loads(resp.read())
        return body


def main() -> None:
    """Run the demo: serve the sidecar, then route + compact over HTTP."""
    server = make_sidecar_server(_build_app(), host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    base = f"http://{host!s}:{port!s}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"{base}/v1/health", timeout=10) as resp:
            print("health:", json.loads(resp.read()))

        routed = _post(base, "/v1/route", {"query": "send a follow-up email", "top_k": 3})
        print(f"\nroute -> {len(routed['candidate_ids'])} candidates:")
        for cid, score in zip(routed["candidate_ids"], routed["scores"], strict=False):
            print(f"  {cid:<32} score={score:.3f}")

        big = {"rows": [{"id": i, "blob": "payload-" * 8} for i in range(60)]}
        compacted = _post(base, "/v1/compact", {"data": big, "threshold_chars": 200})
        print(
            f"\ncompact -> firewalled={compacted['firewalled']} "
            f"tokens_saved={compacted['tokens_saved']} "
            f"artifact_ref={compacted['artifact_ref']}"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
