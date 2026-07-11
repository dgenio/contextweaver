"""Private check implementations for :mod:`contextweaver.adapters.gateway_doctor`.

Extracted to keep ``gateway_doctor.py`` ≤300 lines; ``DoctorFinding`` /
``CONFIG_KEYS`` live here and are re-exported there. Not public API."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import tempfile
from collections import Counter
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from contextweaver.adapters.artifact_policy import ArtifactPolicy
from contextweaver.adapters.gateway_presets import GatewayPreset
from contextweaver.adapters.startup_policy import StartupPolicy
from contextweaver.exceptions import ConfigError
from contextweaver.routing.cards import make_choice_cards
from contextweaver.routing.catalog import Catalog, load_catalog_dicts, validate_references
from contextweaver.routing.normalizer import CatalogNormalizer
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.json_file_artifacts import JsonFileArtifactStore

if TYPE_CHECKING:
    from contextweaver.types import SelectableItem

#: Accepted ``mcp serve`` top-level config keys — mirrors ``_CONFIG_KEYS`` in
#: :mod:`contextweaver._mcp_cli` (not imported: that module pulls in typer).
CONFIG_KEYS: frozenset[str] = frozenset({
    "catalog", "mode", "top_k", "beam_width", "cache_stable", "name", "version", "diagnostics",
    "quiet", "state_dir", "transport", "host", "port", "retry", "rate_limits", "cache", "redact",
    "policy", "policy_preset", "upstreams", "startup", "artifacts",
})  # fmt: skip

#: Keys that must never appear inside a rendered ChoiceCard dict — the
#: schema-hiding invariant (docs/gateway_spec.md §2).
_SCHEMA_KEYS: frozenset[str] = frozenset({"args_schema", "properties", "inputSchema"})

#: ``(importable module, extras name)`` pairs for the optional-extras check.
_OPTIONAL_EXTRAS: tuple[tuple[str, str], ...] = (("fastmcp", "fastmcp"), ("opentelemetry", "otel"),
                                                 ("sentence_transformers", "embeddings"),
                                                 ("sklearn", "ranker"))  # fmt: skip

_MAX_CARD_PROBE_ITEMS = 10
_LIVE_TIMEOUT_SECONDS = 5.0
_WEAK_DESCRIPTION_MIN_CHARS = 8

Level = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class DoctorFinding:
    """One preflight check outcome (re-exported by ``gateway_doctor``).

    Attributes:
        check: Stable dotted check id (e.g. ``"catalog.references"``).
        level: ``"ok"``, ``"warn"``, or ``"fail"``.
        message: Human-readable one-line outcome.
        hint: Optional one-line remediation pointer.
    """

    check: str
    level: Level
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return asdict(self)


def _add(
    out: list[DoctorFinding], check: str, level: Level, message: str, hint: str | None = None
) -> None:
    """Append one finding, whitespace-normalised so it renders on one line."""
    out.append(DoctorFinding(check, level, " ".join(message.split()), hint))


def find_schema_keys(obj: Any, path: str = "") -> list[str]:  # noqa: ANN401 — JSON payload
    """Return sorted paths of every leaked schema key inside a card payload."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for key in sorted(obj):
            where = f"{path}.{key}" if path else str(key)
            if key in _SCHEMA_KEYS:
                hits.append(where)
            hits.extend(find_schema_keys(obj[key], where))
    elif isinstance(obj, list):
        for index, entry in enumerate(obj):
            hits.extend(find_schema_keys(entry, f"{path}[{index}]"))
    return sorted(hits)


def _parse_file(path: Path) -> Any:  # noqa: ANN401 — JSON/YAML payload
    """Parse a JSON/YAML file (JSON by ``.json`` suffix, YAML otherwise)."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)


def _resolve(config_dir: Path, value: str) -> Path:
    """Resolve *value* relative to the config file's directory (CLI-compatible)."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_dir / path


def parse_config(path: Path, out: list[DoctorFinding]) -> dict[str, Any] | None:
    """Parse + shape-check the serve config; return it, or ``None`` when unusable."""
    try:
        data = _parse_file(path)
    except Exception as exc:
        _add(out, "config.parse", "fail", f"cannot parse {path.name}: {exc}")
        return None
    if not isinstance(data, dict):
        _add(out, "config.parse", "fail", "config must be a JSON/YAML mapping")
        return None
    _add(out, "config.parse", "ok", f"parsed {path.name} ({len(data)} keys)")
    unknown = sorted(set(data) - CONFIG_KEYS)
    if unknown:
        msg = f"unknown top-level key(s): {', '.join(unknown)}"
        _add(out, "config.keys", "warn", msg, hint="see 'mcp serve --config' accepted keys")
    if ("catalog" in data) == ("upstreams" in data):
        which = "both" if "catalog" in data else "neither"
        msg = f"config must set exactly one of 'catalog' or 'upstreams' (got {which})"
        _add(out, "config.source", "fail", msg, hint="docs/gateway_spec.md §4.7")
    else:
        _add(out, "config.source", "ok", "catalog" if "catalog" in data else "upstreams")
    return data


def check_blocks(cfg: dict[str, Any], out: list[DoctorFinding]) -> None:
    """Parse the ``startup`` / ``artifacts`` / ``policy_preset`` value blocks."""
    parsers: list[tuple[str, Any]] = [
        ("startup", StartupPolicy.from_dict),
        ("artifacts", ArtifactPolicy.from_dict),
        ("policy_preset", lambda name: GatewayPreset.from_preset(str(name))),
    ]
    for key, parser in parsers:
        if key not in cfg:
            continue
        try:
            parser(cfg[key])
            _add(out, f"config.{key}", "ok", f"'{key}' parses")
        except Exception as exc:
            _add(out, f"config.{key}", "fail", str(exc))


def _native_dicts(raw: Any) -> list[dict[str, Any]]:  # noqa: ANN401 — JSON/YAML payload
    """Coerce a native / MCP-snapshot catalog payload to native item dicts."""
    if isinstance(raw, dict) and isinstance(raw.get("tools"), list):
        raw = raw["tools"]
    if not isinstance(raw, list):
        raise ConfigError("catalog must be a list of tool entries (or a snapshot with 'tools')")
    out: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, dict) and "id" not in entry and "name" in entry:
            # Raw MCP ``tools/list`` shape — promote to the native shape.
            entry = {"id": str(entry["name"]), "kind": "tool", "name": str(entry["name"]),
                     "description": str(entry.get("description", "")),
                     "args_schema": entry.get("inputSchema") or {}}  # fmt: skip
        out.append(dict(entry) if isinstance(entry, dict) else entry)
    return out


def load_items(
    cfg: dict[str, Any], config_dir: Path, out: list[DoctorFinding]
) -> list[SelectableItem]:
    """Load the static catalog; report duplicate-id / reference findings."""
    path = _resolve(config_dir, str(cfg["catalog"]))
    try:
        dicts = _native_dicts(_parse_file(path))
        dupes = sorted(i for i, n in Counter(str(d.get("id")) for d in dicts).items() if n > 1)
        items = load_catalog_dicts(dicts, on_invalid="ignore")
    except Exception as exc:
        _add(out, "catalog.load", "fail", str(exc))
        return []
    if dupes:
        _add(out, "catalog.load", "fail", f"duplicate item id(s): {', '.join(dupes)}")
        return []
    if not items:
        _add(out, "catalog.load", "fail", "catalog contains no tools")
        return []
    _add(out, "catalog.load", "ok", f"{len(items)} tools from {path.name}")
    report = validate_references(items)
    if report.ok:
        _add(out, "catalog.references", "ok", "all references resolve")
    else:
        msg = f"{len(report.findings)} broken reference(s): {'; '.join(report.messages())}"
        _add(out, "catalog.references", "warn", msg)
    return items


def check_catalog_quality(items: list[SelectableItem], out: list[DoctorFinding]) -> None:
    """Weak-metadata warnings, ChoiceCard schema-hiding, and hydration probes."""
    # Weak = originally empty (name-filled by the normalizer) or still near-empty.
    normalized, _report = CatalogNormalizer().normalize(items)
    weak = sum(
        1
        for o, n in zip(items, normalized, strict=False)
        if not o.description.strip() or len(n.description) < _WEAK_DESCRIPTION_MIN_CHARS
    )
    if weak:
        msg = f"{weak} item(s) with empty/near-empty descriptions"
        _add(out, "catalog.metadata", "warn", msg, hint="routing quality depends on metadata")
    else:
        _add(out, "catalog.metadata", "ok", "descriptions look healthy")
    try:
        cards = make_choice_cards(items[:_MAX_CARD_PROBE_ITEMS])
        leaks = sorted({hit for card in cards for hit in find_schema_keys(card.to_dict())})
        if leaks:
            _add(out, "cards.schema_hiding", "fail", f"schema key(s) leaked: {', '.join(leaks)}")
        else:
            _add(out, "cards.schema_hiding", "ok", f"{len(cards)} cards hide schemas")
    except Exception as exc:
        _add(out, "cards.schema_hiding", "fail", str(exc))
    try:
        catalog = Catalog()
        for item in items:
            catalog.register(item)
        _add(out, "catalog.hydration", "ok", f"hydrated {catalog.hydrate(items[0].id).item.id!r}")
    except Exception as exc:
        _add(out, "catalog.hydration", "fail", str(exc))


def check_artifact_store(cfg: dict[str, Any], config_dir: Path, out: list[DoctorFinding]) -> None:
    """Probe artifact-store writability in the configured state dir (or a tempdir)."""
    state_dir, tmp_root = cfg.get("state_dir"), None
    if state_dir is not None:
        base = _resolve(config_dir, str(state_dir)) / "artifacts"
        where = str(base)
    else:
        tmp_root = tempfile.mkdtemp(prefix="cw-doctor-")
        base, where = Path(tmp_root) / "artifacts", "tempdir probe (no state_dir configured)"
    try:
        store = JsonFileArtifactStore(base)
        store.put("doctor-probe", b"ok", "text/plain", label="doctor writability probe")
        store.get("doctor-probe")
        store.delete("doctor-probe")
        _add(out, "artifacts.writable", "ok", f"writable: {where}")
    except Exception as exc:
        hint = "check state_dir permissions/disk space"
        _add(out, "artifacts.writable", "fail", f"{where}: {exc}", hint=hint)
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def check_extras(out: list[DoctorFinding]) -> None:
    """Report optional-extras availability (informational — never a failure)."""
    for module, extra in _OPTIONAL_EXTRAS:
        if importlib.util.find_spec(module) is not None:
            _add(out, f"extras.{module}", "ok", "installed")
        else:
            hint = f"pip install 'contextweaver[{extra}]'"
            _add(out, f"extras.{module}", "ok", "not installed (optional)", hint=hint)


def check_live(cfg: dict[str, Any], out: list[DoctorFinding]) -> None:
    """Launch the configured upstreams briefly and report the startup outcome."""
    from contextweaver.adapters.upstream_config import parse_upstreams_config
    from contextweaver.adapters.upstream_launch import launch_upstreams

    try:
        specs = parse_upstreams_config(cfg["upstreams"])
        policy = StartupPolicy.from_dict(cfg.get("startup") or {})
        timeout = min(policy.upstream_timeout_seconds, _LIVE_TIMEOUT_SECONDS)
        policy = replace(policy, upstream_timeout_seconds=timeout)

        async def _probe() -> Any:  # noqa: ANN401 — StartupReport, import kept lazy
            async with AsyncExitStack() as stack:
                _, report = await launch_upstreams(specs, policy, stack)
                return report

        report = asyncio.run(_probe())
        msg = f"healthy={report.healthy_count}/{len(specs)} tools={report.total_tools}"
        _add(out, "upstreams.live", "ok", msg)
    except Exception as exc:
        _add(out, "upstreams.live", "fail", str(exc))


def check_smoke(items: list[SelectableItem], queries: list[str], out: list[DoctorFinding]) -> None:
    """Route each smoke query through a default Router; warn on zero candidates."""
    try:
        router = Router(TreeBuilder().build(items), items)
    except Exception as exc:
        _add(out, "smoke.router", "fail", str(exc))
        return
    for query in queries:
        try:
            hits = len(router.route(query).candidate_ids)
            _add(out, "smoke.query", "ok" if hits else "warn", f"{query!r}: {hits} candidate(s)")
        except Exception as exc:
            _add(out, "smoke.query", "fail", f"{query!r}: {exc}")
