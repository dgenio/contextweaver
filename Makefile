.PHONY: fmt lint type test example demo ci docs docs-serve benchmark benchmark-matrix benchmark-gateway smoke-eval e2e-quality scorecard scorecard-check sweep-scoring architectures llms llms-check weaver-conformance schemas schemas-check context-rot context-rot-check readme-version-check

fmt:
	ruff format src/ tests/ examples/ scripts/

lint:
	ruff check src/ tests/ examples/ scripts/

type:
	mypy src/

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
	python examples/mcp_proxy_demo.py
	python examples/a2a_adapter_demo.py
	python examples/crewai_adapter_demo.py
	python examples/pydantic_ai_adapter_demo.py
	python examples/smolagents_adapter_demo.py
	python examples/agno_adapter_demo.py
	python examples/fastmcp_discovery_demo.py
	python examples/langchain_memory_demo.py
	python examples/cookbook/byot_recipe.py
	python examples/cookbook/firewall_drilldown_recipe.py
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

benchmark-matrix:
	python benchmarks/benchmark.py --matrix

benchmark-gateway:
	python benchmarks/gateway_benchmark.py

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

ci: fmt lint type test schemas-check example demo

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
