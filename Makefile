.PHONY: fmt lint type test example demo ci ci-full floor-deps tool-smoke docs docs-serve benchmark benchmark-matrix benchmark-routing-scale benchmark-gateway benchmark-primitives benchmark-large-catalog benchmark-large-catalog-check benchmark-scenario benchmark-scenario-check trend trend-check sidecar-smoke token-calibration smoke-eval e2e-quality scorecard scorecard-check sweep-scoring architectures llms llms-check weaver-conformance schemas schemas-check context-rot context-rot-check readme-version-check security-policy-check drift drift-check api api-check module-size-check module-size-update doc-snippets-check

# Interpreter and pip front-end (issue #712). Default to `python3`, which is what
# many modern environments ship (some have no bare `python` on PATH at all).
# Override per-invocation, e.g. `make test PYTHON=python3.11`.
PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

PYTHON ?= python3

fmt:
	ruff format src/ tests/ examples/ scripts/

lint:
	ruff check src/ tests/ examples/ scripts/

type:
	mypy src/ examples/ scripts/

test:
	$(PYTHON) -m pytest --cov=contextweaver --cov-report=term-missing -q

example:
	$(PYTHON) examples/minimal_loop.py
	$(PYTHON) examples/full_agent_loop.py
	$(PYTHON) examples/tool_wrapping.py
	$(PYTHON) examples/routing_demo.py
	$(PYTHON) examples/before_after.py
	$(PYTHON) examples/hydrate_call_demo.py
	$(PYTHON) examples/mcp_adapter_demo.py
	$(PYTHON) examples/mcp_gateway_demo.py
	$(PYTHON) examples/mcp_primitives_demo.py
	$(PYTHON) examples/mcp_proxy_demo.py
	$(PYTHON) examples/a2a_adapter_demo.py
	$(PYTHON) examples/crewai_adapter_demo.py
	$(PYTHON) examples/pydantic_ai_adapter_demo.py
	$(PYTHON) examples/smolagents_adapter_demo.py
	$(PYTHON) examples/agno_adapter_demo.py
	$(PYTHON) examples/agent_framework_adapter_demo.py
	$(PYTHON) examples/openapi_routing_demo.py
	$(PYTHON) examples/skills_routing_demo.py
	$(PYTHON) examples/fastmcp_discovery_demo.py
	$(PYTHON) examples/langchain_memory_demo.py
	$(PYTHON) examples/cookbook/byot_recipe.py
	$(PYTHON) examples/cookbook/firewall_drilldown_recipe.py
	$(PYTHON) examples/sidecar_demo.py
	$(MAKE) architectures

architectures:
	$(PYTHON) examples/architectures/catalog_showcase/main.py
	$(PYTHON) examples/architectures/mcp_context_gateway/main.py
	$(PYTHON) examples/architectures/mcp_context_gateway/main_live.py
	$(PYTHON) examples/architectures/mcp_context_gateway/main_multi.py
	$(PYTHON) examples/architectures/mcp_context_gateway/main_real.py
	$(PYTHON) examples/architectures/slack_ops_bot/main.py
	$(PYTHON) examples/architectures/code_review_bot/main.py
	$(PYTHON) examples/architectures/voice_agent/main.py
	$(PYTHON) examples/architectures/langgraph_agent_loop/main.py
	$(PYTHON) examples/architectures/eval_artifact_profile/main.py
	$(PYTHON) examples/architectures/contextweaver_to_chainweaver/main.py

demo:
	$(PYTHON) -m contextweaver demo

docs:
	mkdocs build --clean

docs-serve:
	mkdocs serve

benchmark:
	$(PYTHON) benchmarks/benchmark.py

token-calibration:
	$(PYTHON) benchmarks/token_calibration.py

benchmark-matrix:
	$(PYTHON) benchmarks/benchmark.py --matrix

# Routing-scale profiler + bottleneck report (issue #684; non-gating). Profiles
# the routing path up to 10k tools and the persistent fitted-index cache
# (#543/#624/#685); writes benchmarks/results/routing_scale.json + the report
# at docs/benchmarks/routing-scale.md.
benchmark-routing-scale:
	$(PYTHON) benchmarks/routing_scale.py

# Large-catalog routing benchmark (issue #369; non-gating). 300+ tools across 8
# namespaces with near-duplicate distractors and destructive tools; writes
# benchmarks/large_catalog_scorecard.md + benchmarks/results/large_catalog.json.
# `-check` verifies the committed scorecard is in sync (deterministic accuracy).
benchmark-large-catalog:
	$(PYTHON) benchmarks/large_catalog.py

benchmark-large-catalog-check:
	$(PYTHON) benchmarks/large_catalog.py --check --strict

# Scenario benchmark (issue #418; non-gating): naive all-tools prompt vs bounded
# ChoiceCard routing. Writes benchmarks/scenario_routing.md; `-check` gates drift.
benchmark-scenario:
	$(PYTHON) benchmarks/scenario_routing.py

benchmark-scenario-check:
	$(PYTHON) benchmarks/scenario_routing.py --check

# Release-over-release benchmark trend (issue #554). `trend` re-renders
# benchmarks/trend.md from benchmarks/results/history/*.json; `trend-check` gates
# drift. Capture a release snapshot with:
#   python scripts/render_trend.py --snapshot <version> --from benchmarks/results/latest.json
trend:
	$(PYTHON) scripts/render_trend.py

trend-check:
	$(PYTHON) scripts/render_trend.py --check

benchmark-gateway:
	$(PYTHON) benchmarks/gateway_benchmark.py

benchmark-primitives:
	$(PYTHON) benchmarks/primitive_gateway_benchmark.py

sidecar-smoke:
	$(PYTHON) examples/sidecar_demo.py

smoke-eval:
	$(PYTHON) benchmarks/smoke_eval.py

e2e-quality:
	$(PYTHON) benchmarks/e2e_quality.py

scorecard:
	$(PYTHON) scripts/render_scorecard.py

scorecard-check:
	$(PYTHON) scripts/render_scorecard.py --check

gateway-scorecard:
	$(PYTHON) scripts/render_gateway_scorecard.py

gateway-scorecard-check:
	$(PYTHON) scripts/render_gateway_scorecard.py --check

record-demos:
	$(PYTHON) scripts/record_demo.py

record-demos-check:
	$(PYTHON) scripts/record_demo.py --check

sweep-scoring:
	$(PYTHON) scripts/sweep_scoring.py

context-rot:
	$(PYTHON) scripts/context_rot_demo.py

context-rot-check:
	$(PYTHON) scripts/context_rot_demo.py --check

readme-version-check:
	$(PYTHON) scripts/check_readme_version.py

# Fails when SECURITY.md drifts from pyproject.toml's version or links a
# missing doc (issue #691). Stdlib-only; no install required.
security-policy-check:
	$(PYTHON) scripts/check_security_policy.py

# The local pass bar. Mirrors the gating CI checks a contributor can run
# offline (issue #474): the consolidated generated-artifact drift gate
# (issue #522) plus the module-size (#456), doc-snippet (#526), and README
# version gates. Weaver-spec conformance and the benchmarks stay CI-only —
# they fetch remote schemas / are heavy — and are documented as such.
ci: fmt lint type test drift-check module-size-check doc-snippets-check readme-version-check security-policy-check example demo

# Local equivalents of the two gating CI *jobs* `make ci` cannot mirror cheaply
# (issue #710, follow-up to #474). Kept out of `ci` because both build isolated
# environments and are slow; run them on demand or via `ci-full`.

# Floor-deps proof (mirrors the `floor-deps` CI job). Resolves each *direct*
# dependency to its declared lower bound in a throwaway uv venv and runs the
# suite, proving the `>=X` floors in pyproject.toml are truthful. The venv is
# pinned to the CI job's floor interpreter (Python 3.10) so the target
# reproduces the gating surface regardless of the local default; override with
# `make floor-deps FLOOR_PYTHON=3.11`. Requires `uv`.
FLOOR_VENV ?= .venv-floor
FLOOR_PYTHON ?= 3.10
floor-deps:
	uv venv --python $(FLOOR_PYTHON) $(FLOOR_VENV)
	uv pip install --python $(FLOOR_VENV) --resolution lowest-direct -e ".[dev,langchain]"
	$(FLOOR_VENV)/bin/python -m pytest -q

# Zero-install distribution smoke (mirrors the Linux cell of the `tool-run-smoke`
# CI job; the macOS cell stays CI-only). Builds the wheel and runs the console
# entry point from isolated uvx / pipx environments to catch packaging or
# script-entry regressions an editable install hides. Requires `uv`; the `pipx`
# legs are skipped with a notice when `pipx` is not on PATH (the uvx legs already
# exercise the same entry points). Only stale wheels are cleared from `dist/`
# (sdists and other artifacts are left in place), and a single fresh wheel is
# rebuilt so the smoke always targets the just-built artifact.
tool-smoke:
	rm -f dist/*.whl
	uv build --wheel
	@WHEEL=$$(find dist -name '*.whl' -print -quit); \
	echo "smoke-testing $$WHEEL"; \
	uvx --isolated --no-config --from "$$WHEEL" contextweaver demo --scenario killer; \
	uvx --isolated --no-config --from "$$WHEEL" contextweaver mcp serve \
		--catalog examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json \
		--dry-run; \
	if command -v pipx >/dev/null 2>&1; then \
		pipx run --no-cache --spec "$$WHEEL" contextweaver demo --scenario killer; \
		pipx run --no-cache --spec "$$WHEEL" contextweaver mcp serve \
			--catalog examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json \
			--dry-run; \
	else \
		echo "pipx not found on PATH — skipping pipx smoke legs (uvx legs already ran)"; \
	fi

# Everything in `ci` plus the two isolated-environment jobs above. The macOS
# tool-run-smoke cell remains the one gating surface with no local equivalent.
ci-full: ci floor-deps tool-smoke

# Unified generated-artifact drift gate (issue #522). `drift` regenerates every
# registered artifact; `drift-check` is the gate. Both compose the per-artifact
# generators (schemas, scorecards, recorded demos, llms.txt, context-rot SVG,
# public-API manifest) so adding the next artifact is one registry entry.
drift:
	$(PYTHON) scripts/drift_check.py

drift-check:
	$(PYTHON) scripts/drift_check.py --check

# Public-API manifest (issue #518): committed signature-level snapshot of the
# public surface; `api-check` fails when the surface changes without a regen.
api:
	$(PYTHON) scripts/gen_api_manifest.py

api-check:
	$(PYTHON) scripts/gen_api_manifest.py --check

# Module-size convention gate (issue #456): enforces ≤300 lines for new modules
# and freezes grandfathered violators at their current size. `module-size-update`
# re-snapshots the frozen baseline (run only when intentionally decomposing).
module-size-check:
	$(PYTHON) scripts/check_module_size.py

module-size-update:
	$(PYTHON) scripts/check_module_size.py --update

# Doc-snippet execution gate (issue #526): runs the Python blocks in README and
# the curated docs allowlist so the first code an adopter copies is guaranteed
# to run against the current API.
doc-snippets-check:
	$(PYTHON) scripts/check_doc_snippets.py

llms:
	$(PYTHON) scripts/gen_llms.py

llms-check:
	$(PYTHON) scripts/gen_llms.py --check

schemas:
	$(PYTHON) scripts/gen_schemas.py

schemas-check:
	$(PYTHON) scripts/gen_schemas.py --check

weaver-conformance:
	@mkdir -p .weaver-schemas
	@for s in routing_decision choice_card selectable_item frame; do \
		curl -fsSL "https://raw.githubusercontent.com/dgenio/weaver-spec/main/contracts/json/$$s.schema.json" \
			-o ".weaver-schemas/$$s.schema.json"; \
	done
	$(PYTHON) scripts/weaver_spec_conformance.py --schemas-dir .weaver-schemas
