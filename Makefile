.PHONY: fmt lint type test example demo ci docs docs-serve benchmark llms llms-check

fmt:
	ruff format src/ tests/ examples/

lint:
	ruff check src/ tests/ examples/

type:
	mypy src/

test:
	pytest -q

example:
	python examples/minimal_loop.py
	python examples/full_agent_loop.py
	python examples/tool_wrapping.py
	python examples/routing_demo.py
	python examples/before_after.py
	python examples/hydrate_call_demo.py
	python examples/mcp_adapter_demo.py
	python examples/a2a_adapter_demo.py
	python examples/langchain_memory_demo.py

demo:
	python -m contextweaver demo

docs:
	mkdocs build --clean

docs-serve:
	mkdocs serve

benchmark:
	python benchmarks/benchmark.py

ci: fmt lint type test example demo

llms:
	python scripts/gen_llms.py

llms-check:
	python scripts/gen_llms.py --check
