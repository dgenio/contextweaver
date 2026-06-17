.PHONY: fmt lint type test example demo ci docs docs-serve benchmark benchmark-matrix benchmark-routing-scale benchmark-gateway benchmark-primitives sidecar-smoke token-calibration smoke-eval e2e-quality scorecard scorecard-check sweep-scoring architectures llms llms-check weaver-conformance schemas schemas-check context-rot context-rot-check readme-version-check drift drift-check api api-check module-size-check module-size-update doc-snippets-check

fmt:
	ruff format src/ tests/ examples/ scripts/

lint:
	ruff check src/ tests/ examples/ scripts/

type:
	mypy src/ examples/ scripts/

test:
	python -m pytest --cov=contextweaver --cov-report=term-missing -q

example:
	python examples/minimal_loop.py
	python examples/full_agent_loop.py
	python examples/tool_wrapping.py
	python examples/routing_demo.py
	python examples/before_after.py
	python examples/hydrate_call_demo.py
	python examples/mcp_adapter_demo.py
	python examples/mcp_gateway_demo.py
	python examples/mcp_primitives_demo.py
	python examples/mcp_proxy_demo.py
	python examples/a2a_adapter_demo.py
	python examples/crewai_adapter_demo.py
	python examples/pydantic_ai_adapter_demo.py
	python examples/smolagents_adapter_demo.py
	python examples/agno_adapter_demo.py
	python examples/agent_framework_adapter_demo.py
	python examples/openapi_routing_demo.py
	python examples/skills_routing_demo.py
	python examples/fastmcp_discovery_demo.py
	python examples/langchain_memory_demo.py
	python examples/cookbook/byot_recipe.py
	python examples/cookbook/firewall_drilldown_recipe.py
	python examples/sidecar_demo.py
	$(MAKE) architectures

architectures:
	python examples/architectures/catalog_showcase/main.py
	python examples/architectures/mcp_context_gateway/main.py
	python examples/architectures/mcp_context_gateway/main_live.py
	python examples/architectures/mcp_context_gateway/main_multi.py
	python examples/architectures/mcp_context_gateway/main_real.py
	python examples/architectures/slack_ops_bot/main.py
	python examples/architectures/code_review_bot/main.py
	python examples/architectures/voice_agent/main.py
	python examples/architectures/langgraph_agent_loop/main.py
	python examples/architectures/eval_artifact_profile/main.py
	python examples/architectures/contextweaver_to_chainweaver/main.py

demo:
	python -m contextweaver demo

docs:
	mkdocs build --clean

docs-serve:
	mkdocs serve

benchmark:
	python benchmarks/benchmark.py

token-calibration:
	python benchmarks/token_calibration.py

benchmark-matrix:
	python benchmarks/benchmark.py --matrix

# Routing-scale profiler + bottleneck report (issue #684; non-gating). Profiles
# the routing path up to 10k tools and the persistent fitted-index cache
# (#543/#624/#685); writes benchmarks/results/routing_scale.json + the report
# at docs/benchmarks/routing-scale.md.
benchmark-routing-scale:
	python benchmarks/routing_scale.py

benchmark-gateway:
	python benchmarks/gateway_benchmark.py

benchmark-primitives:
	python benchmarks/primitive_gateway_benchmark.py

sidecar-smoke:
	python examples/sidecar_demo.py

smoke-eval:
	python benchmarks/smoke_eval.py

e2e-quality:
	python benchmarks/e2e_quality.py

scorecard:
	python scripts/render_scorecard.py

scorecard-check:
	python scripts/render_scorecard.py --check

gateway-scorecard:
	python scripts/render_gateway_scorecard.py

gateway-scorecard-check:
	python scripts/render_gateway_scorecard.py --check

record-demos:
	python scripts/record_demo.py

record-demos-check:
	python scripts/record_demo.py --check

sweep-scoring:
	python scripts/sweep_scoring.py

context-rot:
	python scripts/context_rot_demo.py

context-rot-check:
	python scripts/context_rot_demo.py --check

readme-version-check:
	python scripts/check_readme_version.py

# The local pass bar. Mirrors the gating CI checks a contributor can run
# offline (issue #474): the consolidated generated-artifact drift gate
# (issue #522) plus the module-size (#456), doc-snippet (#526), and README
# version gates. Weaver-spec conformance and the benchmarks stay CI-only —
# they fetch remote schemas / are heavy — and are documented as such.
ci: fmt lint type test drift-check module-size-check doc-snippets-check readme-version-check example demo

# Unified generated-artifact drift gate (issue #522). `drift` regenerates every
# registered artifact; `drift-check` is the gate. Both compose the per-artifact
# generators (schemas, scorecards, recorded demos, llms.txt, context-rot SVG,
# public-API manifest) so adding the next artifact is one registry entry.
drift:
	python scripts/drift_check.py

drift-check:
	python scripts/drift_check.py --check

# Public-API manifest (issue #518): committed signature-level snapshot of the
# public surface; `api-check` fails when the surface changes without a regen.
api:
	python scripts/gen_api_manifest.py

api-check:
	python scripts/gen_api_manifest.py --check

# Module-size convention gate (issue #456): enforces ≤300 lines for new modules
# and freezes grandfathered violators at their current size. `module-size-update`
# re-snapshots the frozen baseline (run only when intentionally decomposing).
module-size-check:
	python scripts/check_module_size.py

module-size-update:
	python scripts/check_module_size.py --update

# Doc-snippet execution gate (issue #526): runs the Python blocks in README and
# the curated docs allowlist so the first code an adopter copies is guaranteed
# to run against the current API.
doc-snippets-check:
	python scripts/check_doc_snippets.py

llms:
	python scripts/gen_llms.py

llms-check:
	python scripts/gen_llms.py --check

schemas:
	python scripts/gen_schemas.py

schemas-check:
	python scripts/gen_schemas.py --check

weaver-conformance:
	@mkdir -p .weaver-schemas
	@for s in routing_decision choice_card selectable_item frame; do \
		curl -fsSL "https://raw.githubusercontent.com/dgenio/weaver-spec/main/contracts/json/$$s.schema.json" \
			-o ".weaver-schemas/$$s.schema.json"; \
	done
	python scripts/weaver_spec_conformance.py --schemas-dir .weaver-schemas
