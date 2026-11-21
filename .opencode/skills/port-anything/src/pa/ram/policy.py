"""Concurrency policy derived from measured memory.

The policy answers one question: how many build processes may run right now
without the device starting to kill other applications. It never terminates a
process, never touches a process it did not start, and never reports a level it
could not measure — an unavailable sample degrades to ``low`` rather than
guessing high.

Thresholds are ratios with absolute floors, so the same policy fits a 2 GiB
phone and a 96 GiB workstation. Ratio alone is wrong on small devices (8% of
2 GiB is 160 MiB, not enough for a linker) and the floor alone is wrong on
large ones (512 MiB free on 96 GiB means the machine is in trouble).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import config as ram_config

LEVELS = ram_config.LEVELS

# Real cost of one linker or one rustc under -j1. Only used when a caller does
# not declare a weight for its job; it is a planning number, not a measurement.
DEFAULT_JOB_WEIGHT = 192 * 1024 * 1024


@dataclass(frozen=True)
class Decision:
    level: str
    concurrency: int
    reasons: tuple[str, ...] = ()
    override: str | None = None
    budget_bytes: int = 0
    cpu_cap: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "concurrency": self.concurrency,
            "reasons": list(self.reasons),
            "override": self.override,
            "budget_bytes": self.budget_bytes,
            "cpu_cap": self.cpu_cap,
        }


def _gb(value: int | None) -> str:
    if value is None:
        return "unknown"
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{value} B"


def classify(snapshot: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, list[str]]:
    """Return ``(level, reasons)`` for a single memory snapshot."""
    available = snapshot.get("available")
    total = snapshot.get("total") or 0
    if not available or not total:
        return "low", ["memory sample unavailable; running sequentially"]

    reasons: list[str] = []
    floor = int(cfg["min_free_bytes"])
    if available <= floor:
        reasons.append(f"available {_gb(available)} at or below reserve {_gb(floor)}")
        return "low", reasons

    ratio = available / total
    low_bytes = int(cfg["low_available_bytes"])
    if ratio < cfg["low_available_ratio"]:
        reasons.append(
            f"available {ratio:.1%} below low band {cfg['low_available_ratio']:.0%}"
        )
        return "low", reasons
    if available < low_bytes:
        reasons.append(f"available {_gb(available)} below low floor {_gb(low_bytes)}")
        return "low", reasons

    if ratio < cfg["medium_available_ratio"]:
        reasons.append(
            f"available {ratio:.1%} below medium band {cfg['medium_available_ratio']:.0%}"
        )
        return "medium", reasons

    reasons.append(f"available {ratio:.1%} above medium band")
    return "high", reasons


def _downgrade(level: str, reason: str) -> tuple[str, str]:
    order = list(LEVELS)
    index = max(0, order.index(level) - 1)
    return order[index], reason


def cpu_cap(cfg: dict[str, Any], cpus: int | None) -> int:
    """Cores this module is willing to occupy, leaving the rest to the device."""
    count = max(1, int(cpus or 1))
    spare = max(1, int(round(count * (1.0 - float(cfg["reserve_cpu_factor"])))))
    return max(1, min(int(cfg["max_concurrency"]), spare, count))


def evaluate(
    snapshot: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    override: str | int | None = None,
    pending: int | None = None,
) -> Decision:
    """Classify the snapshot and turn it into a concurrency decision."""
    cfg = cfg or ram_config.load()
    reasons: list[str] = []
    level, level_reasons = classify(snapshot, cfg)
    reasons.extend(level_reasons)

    load1 = snapshot.get("load1")
    cpus = snapshot.get("cpus") or 1
    if load1 is not None and level != "low" and load1 > cpus * 0.9:
        level, note = _downgrade(level, f"load1 {load1:.2f} above {cpus * 0.9:.2f}")
        reasons.append(note)

    cap = cpu_cap(cfg, cpus)
    concurrency = int(cfg["concurrency"][level])
    override_label: str | None = None

    if isinstance(override, str) and override.strip().isdigit():
        override = int(override.strip())

    if isinstance(override, str) and override in LEVELS:
        override_label = override
        level = override
        concurrency = int(cfg["concurrency"][override])
        reasons.append(f"user override forces {override}")
    elif isinstance(override, int) and not isinstance(override, bool):
        if override >= 1:
            override_label = str(override)
            concurrency = override
            reasons.append(f"user override forces concurrency {override}")
        else:
            reasons.append(f"user override {override} ignored; concurrency is at least 1")

    if concurrency > cap:
        reasons.append(f"capped at {cap} by cpu reserve and max_concurrency")
        concurrency = cap
    concurrency = max(1, concurrency)

    if pending is not None:
        if pending <= 0:
            concurrency = 0
        elif concurrency > pending:
            concurrency = pending

    budget = max(0, int(snapshot.get("available") or 0) - int(cfg["min_free_bytes"]))
    return Decision(
        level=level,
        concurrency=concurrency,
        reasons=tuple(reasons),
        override=override_label,
        budget_bytes=budget,
        cpu_cap=cap,
    )


@dataclass
class Tracker:
    """History of decisions, used to report how the level moved during a run."""

    transitions: list[dict[str, Any]] = field(default_factory=list)
    last_level: str | None = None

    def record(self, decision: Decision, ts: float) -> None:
        if decision.level != self.last_level:
            self.transitions.append(
                {
                    "ts": round(ts, 3),
                    "from": self.last_level,
                    "to": decision.level,
                    "concurrency": decision.concurrency,
                    "reason": decision.reasons[0] if decision.reasons else "",
                }
            )
            self.last_level = decision.level