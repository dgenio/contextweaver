.PHONY: fmt lint type test example demo ci

fmt:
	ruff format src/ tests/ examples/

lint:
	ruff check src/ tests/ examples/

type:
	mypy src/

test:
	python -m pytest -q

example:
	python examples/minimal_loop.py
	python examples/tool_wrapping.py
	python examples/mcp_adapter_demo.py
	python examples/a2a_adapter_demo.py
	python examples/before_after.py
	python examples/routing_demo.py

demo:
	python -m contextweaver demo

ci: fmt lint type test example demo
