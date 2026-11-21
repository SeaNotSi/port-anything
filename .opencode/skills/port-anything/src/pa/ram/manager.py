"""Adaptive job runner.

Jobs are external processes — a linker, a compiler, a packager. The runner
samples memory between dispatches and decides how many may be in flight. It
only ever waits for processes it started; it never signals, reaps, or inspects
anything it did not spawn.

Three properties are deliberate:

* **Downgrade, never kill.** A low sample stops new dispatches. In-flight work
  finishes. Nothing is cancelled, so no half-written object file is left behind.
* **Measured, not guessed.** A job is only dispatched when the declared weight
  fits the remaining budget after the reserve. The last resort for a single
  oversized job is to run it alone and record that the gate was relaxed.
* **Honest reporting.** Every level change and every relaxed gate is an event in
  the report. The report never claims a peak it did not sample.
"""

from __future__ import annotations

import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..util import proc
from . import config as ram_config
from . import policy
from .monitor import Monitor


@dataclass(frozen=True)
class Job:
    id: str
    argv: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] | None = None
    weight_bytes: int = policy.DEFAULT_JOB_WEIGHT

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError(f"job {self.id!r} has an empty argv")
        if isinstance(self.argv, (str, bytes)):
            raise TypeError(f"job {self.id!r} argv must be a sequence, not a string")
        if self.weight_bytes <= 0:
            raise ValueError(f"job {self.id!r} weight must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        return cls(
            id=str(data["id"]),
            argv=tuple(str(part) for part in data["argv"]),
            cwd=data.get("cwd"),
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()} or None,
            weight_bytes=int(data.get("weight_bytes") or policy.DEFAULT_JOB_WEIGHT),
        )


@dataclass
class Report:
    jobs: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    started_level: str | None = None
    final_level: str | None = None
    max_inflight: int = 0
    duration_seconds: float = 0.0
    relaxed_gates: int = 0

    @property
    def ok(self) -> bool:
        return all(job["ok"] for job in self.jobs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "jobs": self.jobs,
            "events": self.events,
            "transitions": self.transitions,
            "started_level": self.started_level,
            "final_level": self.final_level,
            "max_inflight": self.max_inflight,
            "relaxed_gates": self.relaxed_gates,
            "duration_seconds": round(self.duration_seconds, 3),
        }


def _empty_report() -> Report:
    return Report()


def run_jobs(
    jobs: Sequence[Job],
    *,
    cfg: dict[str, Any] | None = None,
    override: str | int | None = None,
    timeout: int = proc.DEFAULT_TIMEOUT,
    monitor: Monitor | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Report:
    cfg = cfg or ram_config.load()
    monitor = monitor or Monitor(history_samples=int(cfg["history_samples"]))
    interval = max(0.05, float(cfg["sample_interval_seconds"]))
    if not jobs:
        return _empty_report()

    report = Report()
    tracker = policy.Tracker()
    results: dict[str, dict[str, Any]] = {}
    order = [job.id for job in jobs]
    seen: set[str] = set()
    for job in jobs:
        if job.id in seen:
            raise ValueError(f"duplicate job id {job.id!r}")
        seen.add(job.id)

    def emit(kind: str, **fields: Any) -> None:
        event = {"ts": round(time.time(), 3), "event": kind, **fields}
        report.events.append(event)
        if on_event is not None:
            on_event(event)

    def work(job: Job) -> dict[str, Any]:
        started = time.time()
        result = proc.run(
            job.argv,
            cwd=job.cwd,
            timeout=timeout,
            env=job.env,
        )
        return {
            "id": job.id,
            "argv": list(job.argv),
            "ok": result.ok,
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_seconds": round(time.time() - started, 3),
        }

    workers = max(1, min(int(cfg["max_concurrency"]), len(jobs)))
    deadline_started = time.time()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pa-ram") as pool:
        pending: deque[Job] = deque(jobs)
        futures: dict[Any, Job] = {}
        weights: dict[Any, int] = {}
        available_before: dict[str, int | None] = {}
        last_decision: policy.Decision | None = None

        while pending or futures:
            snapshot = monitor.sample()
            remaining = len(pending) + len(futures)
            decision = policy.evaluate(snapshot, cfg, override, pending=remaining)
            if report.started_level is None:
                report.started_level = decision.level
            report.final_level = decision.level
            tracker.record(decision, snapshot["ts"])

            if last_decision is None or (
                decision.level != last_decision.level
                or decision.concurrency != last_decision.concurrency
            ):
                emit(
                    "policy",
                    level=decision.level,
                    concurrency=decision.concurrency,
                    available=snapshot.get("available"),
                    budget_bytes=decision.budget_bytes,
                    reasons=list(decision.reasons),
                )
                last_decision = decision

            target = max(1, decision.concurrency)
            while pending and len(futures) < target:
                job = pending[0]
                used = sum(weights.values())
                fits = used + job.weight_bytes <= decision.budget_bytes
                if not fits and futures:
                    emit(
                        "gate",
                        job=job.id,
                        blocked=True,
                        inflight_bytes=used,
                        budget_bytes=decision.budget_bytes,
                    )
                    break
                if not fits and not futures:
                    report.relaxed_gates += 1
                    emit(
                        "gate",
                        job=job.id,
                        blocked=False,
                        relaxed=True,
                        inflight_bytes=used,
                        budget_bytes=decision.budget_bytes,
                        note="single job exceeds budget; running alone",
                    )
                pending.popleft()
                available_before[job.id] = snapshot.get("available")
                future = pool.submit(work, job)
                futures[future] = job
                weights[future] = job.weight_bytes
                report.max_inflight = max(report.max_inflight, len(futures))
                emit(
                    "start",
                    job=job.id,
                    argv=list(job.argv),
                    available=snapshot.get("available"),
                )

            if not futures:
                # Defensive: the loop above always dispatches one job when idle.
                break

            done, _ = wait(set(futures), timeout=interval, return_when=FIRST_COMPLETED)
            for future in done:
                job = futures.pop(future)
                weights.pop(future, None)
                try:
                    record = future.result()
                except Exception as exc:  # a fault in work(), not in the job
                    record = {
                        "id": job.id,
                        "argv": list(job.argv),
                        "ok": False,
                        "returncode": -1,
                        "timed_out": False,
                        "stdout": "",
                        "stderr": f"internal runner error: {exc}",
                        "duration_seconds": 0.0,
                    }
                record["available_before"] = available_before.get(job.id)
                record["available_after"] = (
                    monitor.history[-1].get("available") if monitor.history else None
                )
                results[job.id] = record
                emit(
                    "finish",
                    job=job.id,
                    ok=record["ok"],
                    returncode=record["returncode"],
                    duration_seconds=record["duration_seconds"],
                )

    report.jobs = [results[job_id] for job_id in order if job_id in results]
    report.transitions = tracker.transitions
    report.duration_seconds = time.time() - deadline_started
    return report


def run_job(
    job: Job,
    *,
    cfg: dict[str, Any] | None = None,
    override: str | int | None = None,
    timeout: int = proc.DEFAULT_TIMEOUT,
) -> Report:
    return run_jobs([job], cfg=cfg, override=override, timeout=timeout)


def probe(
    samples: int = 5,
    interval: float = 1.0,
    *,
    cfg: dict[str, Any] | None = None,
    override: str | int | None = None,
) -> dict[str, Any]:
    """Sample memory *samples* times and report the series with its verdict."""
    cfg = cfg or ram_config.load()
    monitor = Monitor(history_samples=max(1, samples))
    series: list[dict[str, Any]] = []
    for index in range(max(1, samples)):
        series.append(monitor.sample())
        if index + 1 < samples:
            time.sleep(max(0.0, interval))
    decision = policy.evaluate(series[-1], cfg, override)
    return {
        "series": series,
        "stats": monitor.stats(),
        "decision": decision.as_dict(),
    }


def jobs_from_manifest(data: Any) -> list[Job]:
    """Parse ``{"jobs": [...]}`` or a bare list of job objects."""
    items: Iterable[Any]
    if isinstance(data, dict):
        items = data.get("jobs") or []
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("job manifest must be a JSON object or array")
    jobs: list[Job] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each job must be a JSON object")
        jobs.append(Job.from_dict(item))
    return jobs