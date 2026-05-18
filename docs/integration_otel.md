# OpenTelemetry GenAI integration

> **Status:** stable as of v0.5 ([#224][i224])
> **Extra:** `pip install 'contextweaver[otel]'`

contextweaver emits OpenTelemetry spans and metrics that conform to the
official [OpenTelemetry GenAI Semantic Conventions][gen-ai-spans]. Modern
agent-observability platforms — [Laminar], [Phoenix], [Langfuse],
[LangSmith] — speak `gen_ai.*` natively, so contextweaver activity renders
as agent / tool spans rather than generic ones.

## Install

```bash
pip install 'contextweaver[otel]'
```

The extra pulls `opentelemetry-api>=1.27`, `opentelemetry-sdk>=1.27`, and
`opentelemetry-semantic-conventions>=0.48b0`. The core install
(`pip install contextweaver`) does not pull these — the integration is
strictly opt-in.

## Wire the hook

```python
from contextweaver.context.manager import ContextManager
from contextweaver.extras.otel import OTelEventHook

hook = OTelEventHook(service_name="my-agent")
mgr = ContextManager(hook=hook)
```

Every `mgr.build_sync(...)` and `Router.route(...)` call now emits the
appropriate GenAI span automatically. See
[contextweaver/extras/otel.py][otel-source] for the full method set.

## Span shapes

| Span name | When emitted | Attributes |
|---|---|---|
| `invoke_agent` | One per `ContextManager.build()` | `gen_ai.system="contextweaver"`, `gen_ai.operation.name="invoke_agent"`, `gen_ai.usage.input_tokens` (= `BuildStats.prompt_tokens`), plus `contextweaver.phase`, `contextweaver.candidates.*` (engine-specific). |
| `execute_tool` | One per `Router.route()` | `gen_ai.system="contextweaver"`, `gen_ai.operation.name="execute_tool"`, `gen_ai.tool.name` (the rank-1 candidate), plus `contextweaver.routing.candidate_count` and `contextweaver.routing.candidate_ids` (full ranked list). |
| `contextweaver.context.firewall` | One per firewall interception | `contextweaver.firewall.reason`, `contextweaver.item.kind`. |
| `contextweaver.context.exclude` | One per exclusion batch | `contextweaver.exclude.reason`, `contextweaver.exclude.count`. |

The two `gen_ai.*` spans are the load-bearing ones for downstream
platforms; the `contextweaver.*` spans are engine-specific audit detail
that is not yet covered by upstream SemConv.

## Metric shapes

| Metric name | Type | Attributes |
|---|---|---|
| `gen_ai.client.token.usage` | histogram | `gen_ai.operation.name`, `gen_ai.token.type="input"`, `gen_ai.system="contextweaver"` |
| `contextweaver.firewall.interceptions` | counter | `contextweaver.firewall.reason` |
| `contextweaver.items.excluded` | counter | `contextweaver.exclude.reason` |
| `contextweaver.budget.exceeded` | counter | `contextweaver.budget.requested`, `contextweaver.budget.limit` |
| `contextweaver.routing.candidates` | histogram | `gen_ai.operation.name`, `gen_ai.system` |

## Worked example — Phoenix

[Phoenix] consumes OTLP and renders `invoke_agent` / `execute_tool` spans
as collapsible agent traces. A minimal wiring:

```python
from phoenix.otel import register
from contextweaver.context.manager import ContextManager
from contextweaver.extras.otel import OTelEventHook
from contextweaver.types import ContextItem, ItemKind, Phase

register(project_name="my-agent", endpoint="http://localhost:6006/v1/traces")
hook = OTelEventHook(service_name="my-agent")
mgr = ContextManager(hook=hook)

mgr.ingest(
    ContextItem(id="u1", kind=ItemKind.user_turn, text="find open invoices")
)
pack = mgr.build_sync(phase=Phase.answer, query="open invoices")
# Phoenix UI now shows a single `invoke_agent` span with prompt_tokens
# under `gen_ai.usage.input_tokens` and `contextweaver.phase=answer`.
```

## Worked example — Laminar

[Laminar] auto-imports `gen_ai.*` spans and renders them in its agent
timeline. Wire OTLP the same way:

```python
import os
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint="https://api.lmnr.ai/v1/traces",
            headers={"Authorization": f"Bearer {os.environ['LMNR_PROJECT_API_KEY']}"},
        )
    )
)
trace.set_tracer_provider(provider)

# Now any contextweaver build / route call emits via the configured
# exporter under the `contextweaver` service name.
```

## Privacy guidance

The default emission does **not** include raw query strings, full tool
descriptions, or any `args_schema` content — those can carry sensitive
payloads in some tool catalogs. Reference: [OpenTelemetry GenAI Tracing
AI Agents Without Leaking PII][maketocreate-pii].

To opt into experimental attributes (e.g. raw-prompt content under
`gen_ai.prompt`), pass `otel_emit_experimental=True` at construction
time. Only enable it when the observability backend is trusted to
handle PII appropriately.

```python
# Off by default — PII-safe:
hook = OTelEventHook(service_name="my-agent")

# Opt-in when running on a trusted backend with redaction in place:
hook = OTelEventHook(service_name="my-agent", otel_emit_experimental=True)
```

## SemConv version note

The GenAI Semantic Conventions are still flagged **Development** status
upstream. contextweaver imports them via the
`opentelemetry.semconv._incubating.attributes.gen_ai_attributes` path,
which is the OTel project's convention for unstable surfaces. When
upstream graduates the conventions, the import path will change but the
emitted attribute *names* are spec-stable (e.g. `gen_ai.system`,
`gen_ai.operation.name`) — your dashboards will keep working.

[i224]: https://github.com/dgenio/contextweaver/issues/224
[gen-ai-spans]: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
[Laminar]: https://laminar.sh/
[Phoenix]: https://phoenix.arize.com/
[Langfuse]: https://langfuse.com/
[LangSmith]: https://www.langchain.com/langsmith
[otel-source]: https://github.com/dgenio/contextweaver/blob/main/src/contextweaver/extras/otel.py
[maketocreate-pii]: https://maketocreate.com/opentelemetry-genai-tracing-ai-agents-without-leaking-pii/
