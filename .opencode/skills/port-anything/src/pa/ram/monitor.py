"""Measured memory, CPU and cgroup sampling.

Nothing here assumes a device size. Values come from ``/proc/meminfo``,
``/proc/loadavg``, ``/proc/self/status`` and the cgroup memory limit when the
host exposes one. When a source is unavailable the field is ``None`` and the
policy degrades to sequential execution.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util import proc


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    values[key.strip()] = int(parts[0]) * 1024
    except OSError:
        pass
    return values


def _read_int(path: str) -> int | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def cgroup_limit() -> int | None:
    limit = _read_int("/sys/fs/cgroup/memory.max")
    if limit is None:
        limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if limit is None or limit > (1 << 62):
        return None
    return limit


def cgroup_usage() -> int | None:
    usage = _read_int("/sys/fs/cgroup/memory.current")
    if usage is None:
        usage = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    return usage


def self_rss() -> int | None:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1]) * 1024
    except OSError:
        return None
    return None


def load_average() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except (OSError, AttributeError):
        return None


def sample() -> dict[str, Any]:
    mem = meminfo()
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable")
    if available is None:
        available = (
            mem.get("MemFree", 0)
            + mem.get("Buffers", 0)
            + mem.get("Cached", 0)
        )
    cgroup_max = cgroup_limit()
    cgroup_used = cgroup_usage()
    if cgroup_max is not None and cgroup_used is not None:
        cgroup_available = max(0, cgroup_max - cgroup_used)
        if total == 0:
            total = cgroup_max
        available = min(available, cgroup_available) if available else cgroup_available
    loads = load_average()
    return {
        "ts": round(time.time(), 3),
        "total": total,
        "available": available,
        "free": mem.get("MemFree", 0),
        "swap_total": mem.get("SwapTotal", 0),
        "swap_free": mem.get("SwapFree", 0),
        "used": max(0, total - available),
        "percent_used": round((total - available) / total, 4) if total else 0.0,
        "cgroup_limit": cgroup_max,
        "cgroup_used": cgroup_used,
        "self_rss": self_rss(),
        "cpus": os.cpu_count() or 1,
        "load1": loads[0] if loads else None,
        "load5": loads[1] if loads else None,
        "load15": loads[2] if loads else None,
    }


@dataclass
class Monitor:
    history_samples: int = 120
    history: deque[dict[str, Any]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.history = deque(maxlen=max(1, self.history_samples))

    def sample(self) -> dict[str, Any]:
        snapshot = sample()
        self.history.append(snapshot)
        return snapshot

    def stats(self) -> dict[str, Any]:
        if not self.history:
            return {"samples": 0}
        available = [item["available"] for item in self.history if item.get("available")]
        used = [item["percent_used"] for item in self.history]
        return {
            "samples": len(self.history),
            "available_min": min(available) if available else None,
            "available_max": max(available) if available else None,
            "available_avg": int(sum(available) / len(available)) if available else None,
            "percent_used_avg": round(sum(used) / len(used), 4) if used else None,
            "last": self.history[-1],
        }


def process_count() -> int:
    result = proc.run(["ps", "-e"], timeout=5)
    if not result.ok:
        return 0
    return max(0, len(result.stdout.splitlines()) - 1)