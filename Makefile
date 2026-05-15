.PHONY: fmt lint type test example demo ci docs docs-serve benchmark scorecard scorecard-check architectures llms llms-check weaver-conformance

fmt:
	ruff format src/ tests/ examples/ scripts/

lint:
	ruff check src/ tests/ examples/ scripts/

type:
	mypy src/

test:
	pytest --cov=contextweaver --cov-report=term-missing -q

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
	python examples/langchain_memory_demo.py
	python examples/cookbook/byot_recipe.py
	python examples/cookbook/firewall_drilldown_recipe.py
	$(MAKE) architectures

architectures:
	python examples/architectures/slack_ops_bot/main.py

demo:
	python -m contextweaver demo

docs:
	mkdocs build --clean

docs-serve:
	mkdocs serve

benchmark:
	python benchmarks/benchmark.py

scorecard:
	python scripts/render_scorecard.py

scorecard-check:
	python scripts/render_scorecard.py --check

ci: fmt lint type test example demo

llms:
	python scripts/gen_llms.py

llms-check:
	python scripts/gen_llms.py --check

weaver-conformance:
	@mkdir -p .weaver-schemas
	@for s in routing_decision choice_card selectable_item frame; do \
		curl -fsSL "https://raw.githubusercontent.com/dgenio/weaver-spec/main/contracts/json/$$s.schema.json" \
			-o ".weaver-schemas/$$s.schema.json"; \
	done
	python scripts/weaver_spec_conformance.py --schemas-dir .weaver-schemas
