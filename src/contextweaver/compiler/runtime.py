"""Phase-aware runtime facade for compiled bundles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.compiler.bundle import CompiledBundle, load_bundle
from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.compiler.trust import RuntimeTrustAssessment
from contextweaver.envelope import HydrationResult
from contextweaver.exceptions import ValidationError
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.history import RouteHistory
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tree import TreeBuilder


@dataclass
class CompiledHydrationResult:
    """Hydrated capability plus declared resources and runtime trust."""

    hydration: HydrationResult
    resources: list[ResourceDescriptor] = field(default_factory=list)
    trust: RuntimeTrustAssessment | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "hydration": self.hydration.to_dict(),
            "resources": [resource.to_dict() for resource in self.resources],
            "trust": self.trust.to_dict() if self.trust else None,
        }


class CompiledAgent:
    """Load, route, and hydrate a compiled-agent bundle without execution."""

    def __init__(self, bundle: CompiledBundle) -> None:
        if not bundle.capabilities:
            raise ValidationError("compiled agent requires at least one capability")
        self.bundle = bundle
        self.catalog = Catalog()
        for item in bundle.capabilities:
            self.catalog.register(item)
        graph = TreeBuilder().build(self.catalog.all())
        self._router = Router(graph, self.catalog.all())

    @classmethod
    def load(cls, path: str | Path, *, verify: bool = True) -> CompiledAgent:
        """Load a compiled bundle from *path* and construct a runtime facade."""
        return cls(load_bundle(path, verify=verify))

    def route(
        self,
        query: str,
        *,
        debug: bool = False,
        exclude_ids: set[str] | None = None,
        exclude_tags: set[str] | None = None,
        allowed_namespaces: set[str] | None = None,
        allowed_tags: set[str] | None = None,
        context_hints: list[str] | None = None,
        history: RouteHistory | None = None,
        pin_ids: set[str] | None = None,
        namespace_quota: int | None = None,
    ) -> RouteResult:
        """Route *query* against the compiled capability catalog."""
        return self._router.route(
            query,
            debug=debug,
            exclude_ids=exclude_ids,
            exclude_tags=exclude_tags,
            allowed_namespaces=allowed_namespaces,
            allowed_tags=allowed_tags,
            context_hints=context_hints,
            history=history,
            pin_ids=pin_ids,
            namespace_quota=namespace_quota,
        )

    def hydrate(self, capability_id: str) -> CompiledHydrationResult:
        """Hydrate a selected capability and attach declared resources."""
        hydration = self.catalog.hydrate(capability_id)
        return CompiledHydrationResult(
            hydration=hydration,
            resources=self.resources_for(capability_id),
            trust=self.assess_runtime(),
        )

    def resources_for(self, capability_id: str) -> list[ResourceDescriptor]:
        """Return resources declared for *capability_id*."""
        item = self.catalog.get(capability_id)
        metadata_ids = item.metadata.get("resource_ids", [])
        declared = {str(value) for value in metadata_ids if isinstance(value, str)}
        resources = [
            resource
            for resource in self.bundle.resources
            if capability_id in resource.capability_ids or resource.resource_id in declared
        ]
        return sorted(resources, key=lambda resource: resource.resource_id)

    def assess_runtime(self, *, checked_at: str = "") -> RuntimeTrustAssessment:
        """Return a runtime trust assessment without mutating the bundle."""
        digest = self.bundle.bundle_digest()
        status = self.bundle.trust.status if self.bundle.trust else "unverified"
        blocked: list[str] = []
        for item in self.catalog.all():
            resources = self.resources_for(item.id)
            if status == "invalid" or any(
                resource.requirement == "required" and not resource.digest for resource in resources
            ):
                blocked.append(item.id)
        blocked_set = set(blocked)
        allowed = [item.id for item in self.catalog.all() if item.id not in blocked_set]
        findings = list(self.bundle.trust.findings) if self.bundle.trust else []
        return RuntimeTrustAssessment(
            bundle_digest=digest,
            status=status,
            checked_at=checked_at,
            allowed_capability_ids=allowed,
            blocked_capability_ids=blocked,
            findings=findings,
        )
