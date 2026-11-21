"""Analyzer registry.

Each analyzer declares what it supports, what it needs, what it cannot do and
how well it has been verified. Unknown formats produce an explicit result —
nothing is silently classified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class AnalyzerSpec:
    id: str
    name: str
    formats: tuple[str, ...]
    inputs: tuple[str, ...]
    output_schema: str
    dependencies: tuple[str, ...]
    limitations: tuple[str, ...]
    verification: str = "verified"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "formats": list(self.formats),
            "inputs": list(self.inputs),
            "output_schema": self.output_schema,
            "dependencies": list(self.dependencies),
            "limitations": list(self.limitations),
            "verification": self.verification,
        }


@dataclass
class AnalysisContext:
    path: Path
    sniffed: Any
    max_bytes: int = 64 * 1024 * 1024
    notes: list[str] = field(default_factory=list)


class Analyzer:
    spec: AnalyzerSpec

    def detect(self, context: AnalysisContext) -> bool:
        raise NotImplementedError

    def analyze(self, context: AnalysisContext) -> dict[str, Any]:
        raise NotImplementedError


_REGISTRY: dict[str, Analyzer] = {}


def register(analyzer: Analyzer) -> Analyzer:
    _REGISTRY[analyzer.spec.id] = analyzer
    return analyzer


def all_analyzers() -> list[Analyzer]:
    return sorted(_REGISTRY.values(), key=lambda a: a.spec.id)


def get(analyzer_id: str) -> Analyzer | None:
    return _REGISTRY.get(analyzer_id)


def specs() -> list[dict[str, Any]]:
    return [analyzer.spec.as_dict() for analyzer in all_analyzers()]


def run(path: str | Path, sniffed: Any) -> dict[str, Any]:
    """Run every analyzer that claims the sniffed format, in registry order."""
    resolved = Path(path).expanduser().resolve()
    context = AnalysisContext(path=resolved, sniffed=sniffed)
    results: list[dict[str, Any]] = []
    for analyzer in all_analyzers():
        try:
            if not analyzer.detect(context):
                continue
            payload = analyzer.analyze(context)
            results.append(
                {
                    "analyzer": analyzer.spec.id,
                    "name": analyzer.spec.name,
                    "verification": analyzer.spec.verification,
                    "limitations": list(analyzer.spec.limitations),
                    "result": payload,
                }
            )
        except Exception as exc:  # an analyzer must never take down the run
            results.append(
                {
                    "analyzer": analyzer.spec.id,
                    "name": analyzer.spec.name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"path": str(resolved), "format": sniffed.as_dict(), "analyzers": results}
