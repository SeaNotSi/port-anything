"""Command line interface for Port Anything.

The CLI is deliberately dependency-free. Commands that need an unavailable
module report it rather than crashing the process.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .dataset import query
from .dataset.index import build_index, needs_rebuild
from .dataset.manifest import verify_manifest, write_manifest
from .dataset.validate import validate_dataset
from .util.paths import DATASETS_DIR, human_bytes


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False))


def _fail(message: str, code: int = 1) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


# --------------------------------------------------------------------------- #
# dataset
# --------------------------------------------------------------------------- #


def cmd_dataset_validate(args: argparse.Namespace) -> int:
    report = validate_dataset(DATASETS_DIR)
    if args.json:
        _print_json(report.as_dict())
    else:
        stats = report.stats
        print(f"records : {stats.get('records', 0)}")
        print(f"files   : {stats.get('files', 0)}")
        print(f"hash    : {stats.get('content_hash', '')[:16]}")
        for category, count in stats.get("categories", {}).items():
            print(f"  {category:<14} {count}")
        for warning in report.warnings:
            print(f"warn: {warning}")
        for error in report.errors:
            print(f"error: {error}")
        print("OK" if report.ok else "FAILED")
    return 0 if report.ok else 1


def cmd_dataset_manifest(args: argparse.Namespace) -> int:
    if args.write:
        manifest = write_manifest(DATASETS_DIR, version=args.version)
        if args.json:
            _print_json(manifest)
        else:
            print(f"manifest written: {manifest['total_records']} records, version {manifest['version']}")
        return 0
    problems = verify_manifest(DATASETS_DIR)
    if args.json:
        _print_json({"ok": not problems, "problems": problems})
    else:
        for problem in problems:
            print(f"error: {problem}")
        print("OK" if not problems else "FAILED")
    return 0 if not problems else 1


def cmd_dataset_index(args: argparse.Namespace) -> int:
    stale, reason = needs_rebuild(DATASETS_DIR)
    if not stale and not args.force:
        if args.json:
            _print_json({"rebuilt": False, "reason": reason})
        else:
            print(f"index is current ({reason}); use --force to rebuild")
        return 0
    result = build_index(DATASETS_DIR)
    result["rebuilt"] = True
    result["reason"] = reason
    if args.json:
        _print_json(result)
    else:
        print(f"indexed {result['records']} records into {result['path']}")
        print(f"fts5: {result['fts']}  size: {human_bytes(result['bytes'])}")
    return 0


def cmd_dataset_search(args: argparse.Namespace) -> int:
    try:
        rows = query.search(args.query, category=args.category, limit=args.limit)
    except query.IndexMissingError as exc:
        return _fail(str(exc))
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print("no matches")
        return 0
    for row in rows:
        print(f"{row['id']:<36} {row['name']} {row['version']}")
        print(f"  android={row['android']} arm64={row['arm64']} status={row['status']} ({row['verification']})")
    return 0


def cmd_dataset_show(args: argparse.Namespace) -> int:
    try:
        record = query.get(args.id)
    except query.IndexMissingError as exc:
        return _fail(str(exc))
    if record is None:
        return _fail(f"record not found: {args.id}")
    if args.json:
        _print_json(record)
        return 0
    print(f"{record['id']} — {record['name']} {record.get('version', '')}".rstrip())
    print(record["description"])
    print(f"source     : {record['source']['url']} ({record['source']['type']}, {record['source']['retrieved']})")
    print(f"license    : {record['license']['name']} [{record['license']['spdx']}]")
    print(f"verified   : {record['verification']} on {record['last_verified']}")
    print(f"android    : {record['compatibility'].get('android')}  arm64: {record['compatibility'].get('arm64')}")
    if record.get("requirements"):
        print("requirements:")
        for item in record["requirements"]:
            print(f"  - {item}")
    if record.get("limitations"):
        print("limitations:")
        for item in record["limitations"]:
            print(f"  - {item}")
    if record.get("dependencies"):
        print(f"dependencies: {', '.join(record['dependencies'])}")
    if record.get("dependents"):
        print(f"used by     : {', '.join(record['dependents'])}")
    return 0


def cmd_dataset_stats(args: argparse.Namespace) -> int:
    try:
        stats = query.stats()
    except query.IndexMissingError as exc:
        return _fail(str(exc))
    if args.json:
        _print_json(stats)
    else:
        print(f"total records: {stats['total']}")
        for category, count in stats["categories"].items():
            print(f"  {category:<14} {count}")
        print("verification:")
        for state, count in stats["verification"].items():
            print(f"  {state:<20} {count}")
    return 0


def cmd_dataset_compat(args: argparse.Namespace) -> int:
    try:
        rows = query.by_compat(args.capability, args.value)
    except (query.IndexMissingError, ValueError) as exc:
        return _fail(str(exc))
    if args.json:
        _print_json(rows)
    else:
        for row in rows:
            print(f"{row['id']:<36} {row['name']}")
    return 0


def cmd_dataset_export(args: argparse.Namespace) -> int:
    from .dataset.model import load_dataset, strip_internal

    dataset = load_dataset(DATASETS_DIR)
    payload = {
        "manifest": json.loads((DATASETS_DIR / "manifest.json").read_text(encoding="utf-8")),
        "records": [strip_internal(record) for record in dataset.records],
    }
    target = Path(args.out)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"exported {len(payload['records'])} records to {target}")
    return 0


def cmd_dataset_import(args: argparse.Namespace) -> int:
    from .dataset.update import import_bundle

    result = import_bundle(Path(args.source), dry_run=args.dry_run)
    if args.json:
        _print_json(result)
    else:
        print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


# --------------------------------------------------------------------------- #
# reverse engineering
# --------------------------------------------------------------------------- #


def _write_markdown(bundle: dict[str, Any], target: str) -> None:
    from .re import render_markdown

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(bundle), encoding="utf-8")
    print(f"report written: {path}", file=sys.stderr)


def cmd_analyze(args: argparse.Namespace) -> int:
    from .re import analyze, render_text

    bundle = analyze(args.path)
    if args.markdown:
        _write_markdown(bundle, args.markdown)
    if args.json:
        _print_json(bundle)
    else:
        print(render_text(bundle))
    return 0


def cmd_compat(args: argparse.Namespace) -> int:
    from .doctor import detect
    from .re import analyze, assess, render_text

    bundle = analyze(args.path)
    capabilities = None if args.no_probe else detect()["capabilities"]
    bundle["compatibility"] = assess(
        bundle["fingerprint"],
        target_platform=args.target,
        target_abi=args.abi,
        capabilities=capabilities,
    )
    if args.markdown:
        _write_markdown(bundle, args.markdown)
    if args.json:
        _print_json(bundle)
    else:
        print(render_text(bundle))
    return 0


def cmd_analyzers(args: argparse.Namespace) -> int:
    from .re import specs

    entries = specs()
    if args.json:
        _print_json(entries)
        return 0
    for entry in entries:
        formats = ", ".join(entry["formats"])
        print(f"{entry['id']:<16} {entry['name']}")
        print(f"  formats : {formats}")
        print(f"  verified: {entry['verification']}")
        for limitation in entry["limitations"]:
            print(f"  limit   : {limitation}")
    return 0


def _collect_text_files(paths: list[str]) -> tuple[list[Path], list[dict[str, str]]]:
    from .tokens import sources

    return sources.expand_paths(paths)


# --------------------------------------------------------------------------- #
# token efficiency
# --------------------------------------------------------------------------- #


def cmd_tokens_estimate(args: argparse.Namespace) -> int:
    from .tokens import estimate, sources

    files, skipped = sources.expand_paths(args.paths)
    if not files:
        return _fail("no readable files found")
    result = estimate.estimate_paths(files)
    result["skipped"] = skipped
    if args.json:
        _print_json(result)
        return 0
    for item in result["files"]:
        flag = " binary" if item["binary"] else ""
        note = "" if item["complete"] else " PARTIAL"
        print(f"{item['tokens']:>8}  {human_bytes(item['bytes']):>10}{flag}{note}  {item['path']}")
    print(f"{result['total_tokens']:>8}  total tokens ({result['method']})")
    if result["incomplete"]:
        print(f"partial: {len(result['incomplete'])} file(s) exceeded the read limit")
    return 0


def cmd_tokens_index(args: argparse.Namespace) -> int:
    from .tokens import sources

    index = sources.index(args.paths, root=args.root)
    if args.out:
        path = sources.save_manifest(index, args.out)
        print(f"manifest written: {path}", file=sys.stderr)
    if args.json:
        _print_json(index.to_dict())
        return 0
    totals = index.to_dict()["totals"]
    print(f"root   : {index.root}")
    print(f"files  : {totals['files']}  lines: {totals['lines']}  bytes: {human_bytes(totals['bytes'])}")
    print(f"tokens : {totals['tokens']}")
    print(f"hash   : {index.manifest_hash()[:16]}")
    if index.skipped:
        print(f"skipped: {len(index.skipped)}")
    return 0


def cmd_tokens_plan(args: argparse.Namespace) -> int:
    from .tokens import context, guard, sources

    index = sources.index(args.paths, root=args.root)
    if not index.files:
        return _fail("no text sources found")
    plan = context.plan(
        index,
        budget=args.budget,
        query=args.query,
        must_keep=args.must_keep or (),
        dedupe=not args.no_dedupe,
    )
    if not args.no_check:
        report = guard.check(plan, index)
        if not report["ok"]:
            for violation in report["violations"]:
                print(f"violation: {violation}", file=sys.stderr)
            return 1
        for warning in report["warnings"]:
            print(f"warn: {warning}", file=sys.stderr)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"plan written: {target}", file=sys.stderr)
    if args.json:
        _print_json(plan)
    else:
        print(context.render_plan(plan))
    return 0


def cmd_tokens_check(args: argparse.Namespace) -> int:
    from .tokens import guard, sources

    index = sources.load_manifest(args.manifest)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    report = guard.check(plan, index, budget=args.budget)
    if args.json:
        _print_json(report)
    else:
        for violation in report["violations"]:
            print(f"violation: {violation}")
        for warning in report["warnings"]:
            print(f"warn: {warning}")
        print(
            f"{'OK' if report['ok'] else 'FAILED'} — {report['recomputed_tokens']}/{report['budget']} tokens, "
            f"{report['sources']} sources"
        )
    return 0 if report["ok"] else 1


def cmd_tokens_slice(args: argparse.Namespace) -> int:
    from .tokens import sources

    index = sources.load_manifest(args.manifest)
    try:
        text = index.read_slice(args.source, args.start, args.end)
    except (KeyError, ValueError) as exc:
        return _fail(str(exc))
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cmd_tokens_dedupe(args: argparse.Namespace) -> int:
    from .tokens import duplicate

    result = duplicate.find_duplicates(args.paths, root=args.root)
    if args.json:
        _print_json(result)
        return 0
    if not result["exact"] and not result["near"]:
        print("no duplicates found")
        return 0
    for group in result["exact"]:
        print(f"exact {group['sha256'][:12]} saves {group['tokens_saved']} tokens:")
        for source in group["sources"]:
            print(f"  {source['id']} {source['path']}")
    for pair in result["near"]:
        print(f"near {pair['similarity']:.2f}: {pair['path_a']} <-> {pair['path_b']}")
    print(f"savings: {result['savings_tokens']} tokens")
    return 0


def cmd_tokens_cache(args: argparse.Namespace) -> int:
    from .tokens import cache

    if args.clear:
        removed = cache.clear()
        print(f"cleared {removed} entries")
        return 0
    stats = cache.stats()
    if args.json:
        _print_json(stats)
    else:
        print(f"cache  : {stats['dir']}")
        print(f"entries: {stats['entries']}  size: {human_bytes(stats['bytes'])}")
    return 0


# --------------------------------------------------------------------------- #
# ram efficiency
# --------------------------------------------------------------------------- #


def _ram_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    from .ram import config as ram_config
    from .ram import monitor

    return monitor.sample(), ram_config.load()


def cmd_ram_status(args: argparse.Namespace) -> int:
    from .ram import policy

    snapshot, cfg = _ram_snapshot()
    decision = policy.evaluate(snapshot, cfg, args.override)
    if args.json:
        _print_json({"snapshot": snapshot, "decision": decision.as_dict(), "config": cfg})
        return 0
    total = snapshot.get("total") or 0
    available = snapshot.get("available") or 0
    percent = (available / total * 100) if total else 0.0
    print(f"memory   : {human_bytes(available)} available of {human_bytes(total)} ({percent:.1f}%)")
    if snapshot.get("cgroup_limit"):
        print(
            f"cgroup   : {human_bytes(snapshot['cgroup_limit'])} limit, "
            f"{human_bytes(snapshot.get('cgroup_used') or 0)} used"
        )
    print(
        f"swap     : {human_bytes(snapshot.get('swap_free') or 0)} free of "
        f"{human_bytes(snapshot.get('swap_total') or 0)}"
    )
    loads = [
        snapshot.get("load1"),
        snapshot.get("load5"),
        snapshot.get("load15"),
    ]
    if any(value is not None for value in loads):
        rendered = " ".join(f"{value:.2f}" for value in loads if value is not None)
        print(f"load     : {rendered} on {snapshot.get('cpus')} cpus")
    print(f"level    : {decision.level} -> concurrency {decision.concurrency}")
    print(f"budget   : {human_bytes(decision.budget_bytes)} above the reserve")
    for reason in decision.reasons:
        print(f"reason   : {reason}")
    return 0


def cmd_ram_policy(args: argparse.Namespace) -> int:
    from .ram import policy

    snapshot, cfg = _ram_snapshot()
    decision = policy.evaluate(snapshot, cfg, args.override)
    if args.json:
        _print_json({"config": cfg, "decision": decision.as_dict()})
        return 0
    print("thresholds (ascending bands):")
    print(f"  reserve      : {human_bytes(cfg['min_free_bytes'])}")
    print(f"  low band     : available < {cfg['low_available_ratio']:.0%} or < {human_bytes(cfg['low_available_bytes'])}")
    print(f"  medium band  : available < {cfg['medium_available_ratio']:.0%}")
    print("concurrency:")
    for level in ("low", "medium", "high"):
        print(f"  {level:<7} {cfg['concurrency'][level]}")
    print(f"  max          {cfg['max_concurrency']}")
    print(f"  cpu cap      {decision.cpu_cap} (reserves {cfg['reserve_cpu_factor']:.0%} of cores)")
    print(f"  sample       every {cfg['sample_interval_seconds']}s, {cfg['history_samples']} kept")
    print(f"now          : {decision.level} -> {decision.concurrency}")
    return 0


def cmd_ram_run(args: argparse.Namespace) -> int:
    from .ram import manager

    if args.manifest:
        try:
            data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            jobs = manager.jobs_from_manifest(data)
        except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
            return _fail(f"invalid job manifest: {exc}")
    else:
        argv = list(args.argv or [])
        if argv[:1] == ["--"]:
            argv = argv[1:]
        if not argv:
            return _fail("no command given; pass `-- <cmd...>` or --manifest FILE")
        jobs = [manager.Job(id="job-1", argv=tuple(argv))]

    report = manager.run_jobs(
        jobs,
        override=args.override,
        timeout=args.timeout,
    )
    if args.json:
        _print_json(report.as_dict())
        return 0 if report.ok else 1

    print(f"level    : {report.started_level} -> {report.final_level}  max inflight {report.max_inflight}")
    if report.relaxed_gates:
        print(f"gates    : {report.relaxed_gates} relaxed (job larger than budget)")
    for job in report.jobs:
        state = "ok" if job["ok"] else f"failed ({job['returncode']})"
        print(
            f"{job['id']:<12} {state:<14} {job['duration_seconds']:>6.2f}s "
            f"mem {human_bytes(job.get('available_before') or 0)} -> "
            f"{human_bytes(job.get('available_after') or 0)} available"
        )
        if not job["ok"] and job["stderr"].strip():
            print(f"  {job['stderr'].strip().splitlines()[0][:160]}")
    print(f"total    : {report.duration_seconds:.2f}s — {'OK' if report.ok else 'FAILED'}")
    return 0 if report.ok else 1


def cmd_ram_cache(args: argparse.Namespace) -> int:
    from .ram import cache

    root = args.root
    if args.clear:
        removed = cache.clear(root)
        if args.json:
            _print_json({"cleared": removed})
        else:
            print(f"cleared {removed} files")
        return 0
    if args.trim is not None:
        result = cache.trim(args.trim, root=root)
        if args.json:
            _print_json(result)
        else:
            print(
                f"evicted {result['evicted']} entries, freed {human_bytes(result['freed_bytes'])}, "
                f"{human_bytes(result['remaining_bytes'])} remaining"
            )
        return 0
    if args.evict_for is not None:
        result = cache.evict_for(args.evict_for, root=root)
        if args.json:
            _print_json(result)
        else:
            print(f"ok       : {result['ok']}")
            print(f"required : {human_bytes(result['required_bytes'])}")
            print(f"free     : {human_bytes(result['free_before'])} -> {human_bytes(result['free_after'])}")
            if result.get("note"):
                print(f"note     : {result['note']}")
        return 0 if result["ok"] else 1
    stats = cache.stats(root)
    if args.json:
        _print_json(stats)
    else:
        print(f"cache  : {stats['path']}")
        print(f"entries: {stats['entries']}  size: {human_bytes(stats['bytes'])}")
        print(f"free   : {human_bytes(cache.free_space(stats['path']))}")
    return 0


def cmd_ram_probe(args: argparse.Namespace) -> int:
    from .ram import manager

    result = manager.probe(args.samples, args.interval, override=args.override)
    if args.json:
        _print_json(result)
        return 0
    for point in result["series"]:
        total = point.get("total") or 0
        available = point.get("available") or 0
        percent = (available / total * 100) if total else 0.0
        print(
            f"{point['ts']:.3f}  {human_bytes(available):>10} free  "
            f"{percent:5.1f}%  load {point.get('load1') or 0:.2f}"
        )
    decision = result["decision"]
    print(f"level    : {decision['level']} -> concurrency {decision['concurrency']}")
    for reason in decision["reasons"]:
        print(f"reason   : {reason}")
    return 0


# --------------------------------------------------------------------------- #
# multi-agent
# --------------------------------------------------------------------------- #


def _load_contracts(path: str) -> list[Any]:
    from .agents import contract as contract_module

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return contract_module.contracts_from_manifest(data)


def cmd_agents_list(args: argparse.Namespace) -> int:
    from .agents import registry
    from .util.paths import AGENTS_DIR

    root = Path(args.dir) if args.dir else AGENTS_DIR
    rows = []
    for name, mode in registry.EXPECTED_AGENTS:
        path = root / f"{name}.md"
        meta: dict[str, str] = {}
        if path.is_file():
            meta = registry._frontmatter(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "name": name,
                "mode": mode,
                "present": path.is_file(),
                "description": meta.get("description", ""),
            }
        )
    if args.json:
        _print_json(rows)
        return 0
    for row in rows:
        state = "ok " if row["present"] else "MISSING"
        print(f"{state} {row['name']:<20} {row['mode']:<9} {row['description'][:88]}")
    return 0 if all(row["present"] for row in rows) else 1


def cmd_agents_validate(args: argparse.Namespace) -> int:
    from .agents import registry
    from .util.paths import AGENTS_DIR

    result = registry.check(Path(args.dir) if args.dir else AGENTS_DIR)
    if args.json:
        _print_json(result)
    else:
        print(registry.render_check(result))
    return 0 if result["ok"] else 1


def cmd_agents_graph(args: argparse.Namespace) -> int:
    from .agents import conflict
    from .agents.graph import TaskGraph, owners_of

    try:
        contracts = _load_contracts(args.manifest)
        graph = TaskGraph.from_contracts(contracts)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _fail(str(exc))
    clashes = conflict.find_conflicts(owners_of(graph), related=graph.related)
    if args.json:
        _print_json(
            {
                "tasks": len(graph),
                "layers": graph.layers(),
                "order": graph.topo_order(),
                "roots": graph.roots(),
                "leaves": graph.leaves(),
                "conflicts": [item.as_dict() for item in clashes],
            }
        )
        return 0
    print(f"tasks : {len(graph)}")
    for index, layer in enumerate(graph.layers(), start=1):
        print(f"layer {index}: {', '.join(layer)}")
    hard = [item for item in clashes if not item.sequential]
    for item in clashes:
        kind = "handoff" if item.sequential else "CONFLICT"
        print(
            f"{kind:<9} {item.task_a}:{item.path_a} <-> {item.task_b}:{item.path_b}"
        )
    print(f"parallel-safe: {'yes' if not hard else 'no'}")
    return 0 if not hard else 1


def _open_store(graph: Any, path: str) -> Any:
    from .agents.store import RunStore

    return RunStore.create(graph, path, resume=True)


def cmd_agents_plan(args: argparse.Namespace) -> int:
    from .agents import scheduler
    from .agents.graph import TaskGraph

    try:
        contracts = _load_contracts(args.manifest)
        graph = TaskGraph.from_contracts(contracts)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _fail(str(exc))

    if args.dry_run:
        try:
            batches = scheduler.simulate(graph, concurrency=args.concurrency)
        except ValueError as exc:
            return _fail(str(exc))
        if args.json:
            _print_json({"batches": batches})
            return 0
        for index, batch in enumerate(batches, start=1):
            print(f"batch {index}: {', '.join(batch)}")
        print(f"waves: {len(batches)}")
        return 0

    if not args.store:
        return _fail("--store is required unless --dry-run is used")
    try:
        store = _open_store(graph, args.store)
        plan_out = scheduler.plan(graph, store, concurrency=args.concurrency)
        if args.apply:
            scheduler.mark_dispatched(store, plan_out.batch)
            store.save()
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    if args.json:
        _print_json({**plan_out.as_dict(), "applied": bool(args.apply)})
        return 0
    print(scheduler.describe(plan_out))
    if args.apply:
        print(f"applied: {len(plan_out.batch)} task(s) marked running in {store.path}")
    return 0


def cmd_agents_status(args: argparse.Namespace) -> int:
    from .agents.store import RunStore

    try:
        store = RunStore.load(args.store)
    except ValueError as exc:
        return _fail(str(exc))
    if args.json:
        _print_json({**store.summary(), "tasks": store.data["tasks"]})
        return 0
    summary = store.summary()
    print(f"run      : {summary['run']}")
    print(f"state    : {summary['path']}")
    counts = ", ".join(f"{status}={count}" for status, count in summary["counts"].items() if count)
    print(f"tasks    : {summary['tasks']} ({counts or 'empty'})")
    print(f"claims   : {summary['claims']}  finished: {summary['finished']}")
    for task_id, entry in store.data["tasks"].items():
        note = entry.get("error") or ""
        print(f"  {task_id:<28} {entry['status']:<8} {note[:80]}")
    return 0


def cmd_agents_prompt(args: argparse.Namespace) -> int:
    try:
        contracts = _load_contracts(args.manifest)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _fail(str(exc))
    target = next((contract for contract in contracts if contract.id == args.id), None)
    if target is None:
        return _fail(f"no task with id {args.id!r}")
    print(target.render_prompt(run_id=args.run or ""))
    return 0


# --------------------------------------------------------------------------- #
# apk pipeline
# --------------------------------------------------------------------------- #


def cmd_apk_env(args: argparse.Namespace) -> int:
    from .apk import env

    report = env.detect()
    if args.json:
        _print_json(report)
        return 0
    print("tools:")
    for name, tool in report["tools"].items():
        state = tool["state"]
        version = tool["version"] or tool["error"]
        print(f"  {name:<10} {state:<8} {version[:80]}")
    android = report["android"]
    print("android:")
    print(f"  sdk root     : {android['sdk_root'] or 'MISSING'}")
    print(f"  framework jar: {android['framework_jar'] or 'MISSING'}")
    print(f"  ndk root     : {android['ndk_root'] or 'MISSING'}")
    print(f"  ndk toolchain: {android['ndk_toolchain_file'] or 'MISSING'}")
    print(f"  adb          : {android['adb']['state']} {android['adb_reason']}")
    print("capabilities:")
    for name, state in report["capabilities"].items():
        print(f"  {name:<24} {state}")
    return 0


def cmd_apk_plan(args: argparse.Namespace) -> int:
    from .apk import strategy

    try:
        if args.target:
            from .doctor import detect
            from .re import analyze, assess

            bundle = analyze(args.target)
            bundle["compatibility"] = assess(
                bundle["fingerprint"], capabilities=detect()["capabilities"]
            )
            plan = strategy.from_compat(bundle["compatibility"])
        else:
            plan = strategy.plan(args.strategy)
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    if args.json:
        _print_json(plan.as_dict())
    else:
        print(strategy.render(plan))
    return 0


def cmd_apk_selftest(args: argparse.Namespace) -> int:
    from .apk import package

    result = package.selftest(args.workdir)
    if args.json:
        _print_json(result)
    else:
        for stage in result["stages"]:
            print(f"{stage['state'].upper():<12} {stage['id']:<12} {stage['detail'][:90]}")
        if result.get("missing"):
            print(f"missing  : {', '.join(result['missing'])}")
        print(f"artifact : {result.get('artifact') or 'none'}")
        print("OK" if result["ok"] else "FAILED")
    if result.get("artifact") and not args.keep:
        # Only the temp artifacts are removed; the report above is already printed.
        from pathlib import Path as _Path

        for name in ("base.apk", "selftest-signed.apk", "compiled-resources.zip"):
            try:
                (_Path(result["workdir"]) / name).unlink()
            except OSError:
                continue
    return 0 if result["ok"] else 1


def cmd_apk_validate(args: argparse.Namespace) -> int:
    from .apk import validate

    report = validate.inspect(args.apk)
    if args.package:
        report["package_check"] = validate.check_package_name(args.apk, args.package)
    if args.json:
        _print_json(report)
    else:
        print(validate.render(report))
        if args.package:
            check = report["package_check"]
            print(f"package  : {'OK' if check['ok'] else 'MISMATCH'} — {check['detail']}")
    return 0 if report["ok"] else 1


def cmd_apk_smoke(args: argparse.Namespace) -> int:
    from .apk import smoke

    report = {"device": smoke.state()}
    report["devices"] = smoke.devices().as_dict()
    if not args.device_only:
        report["install"] = smoke.install(args.apk).as_dict()
        if args.package:
            report["launch"] = smoke.launch(args.package, args.activity or "").as_dict()
            report["runtime"] = smoke.logcat(package=args.package).as_dict()
    if args.json:
        _print_json(report)
    else:
        print(f"device   : {report['device']['state']} {report['device'].get('error', '')}")
        for key in ("devices", "install", "launch", "runtime"):
            if key in report:
                print(f"{report[key]['state'].upper():<12} {key:<8} {report[key]['detail'][:90]}")
    stages_list = [report[key] for key in ("devices", "install", "launch", "runtime") if key in report]
    return 0 if all(item["state"] != "failed" for item in stages_list) else 1


def cmd_apk_build(args: argparse.Namespace) -> int:
    from .apk import pipeline

    try:
        report = pipeline.build_from_spec_file(
            args.spec, strategy_id=args.strategy, check_device=not args.no_device
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _fail(str(exc))
    if args.json:
        _print_json(report.as_dict())
    else:
        print(report.render())
    return 0 if report.ok else 1


def cmd_apk_native(args: argparse.Namespace) -> int:
    from .apk import pipeline

    try:
        result = pipeline.build_native(
            args.directory,
            abi=args.abi,
            kind=args.kind,
            release=not args.debug,
            offline=args.offline,
            build_dir=args.build_dir,
        )
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    if args.json:
        _print_json(result)
        return 0
    if "stage" in result:
        stage = result["stage"]
        print(f"{stage['state'].upper():<12} {stage['id']:<12} {stage['detail'][:90]}")
        if stage["output"] and stage["state"] != "done":
            print(stage["output"][:1200])
    for stage in result.get("stages", []):
        print(f"{stage['state'].upper():<12} {stage['id']:<12} {stage['detail'][:90]}")
    for abi, paths in (result.get("libraries") or {}).items():
        for path in paths:
            print(f"library      {abi:<12} {path}")
    states = [result["stage"]["state"]] if "stage" in result else [item["state"] for item in result.get("stages", [])]
    return 0 if all(state != "failed" for state in states) else 1


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #


def cmd_install(args: argparse.Namespace) -> int:
    from . import install as installer

    scope = "global" if args.global_scope else "project"
    try:
        if args.uninstall:
            report = installer.uninstall(scope, args.path, dry_run=args.dry_run)
        elif args.check:
            report = installer.check(scope, args.path)
        else:
            report = installer.install(scope, args.path, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    if args.json:
        _print_json(report.as_dict())
    else:
        print(report.render())
    return 0 if report.ok() else 1


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import detect

    report = detect()
    if args.json:
        _print_json(report)
        return 0
    plat = report["platform"]
    print(f"port-anything {__version__}")
    print(f"platform : {plat['system']} {plat['release']} {plat['machine']}")
    print(f"termux   : {plat['termux']}  api={plat['android_api']} abi={plat['android_abi']}")
    mem = report["memory"]
    print(
        f"memory   : {human_bytes(mem['available'])} free of {human_bytes(mem['total'])}"
        f"  swap {human_bytes(mem['swap_free'])} free"
    )
    home = report["storage"]["home"]
    if home:
        print(f"storage  : {human_bytes(home['free'])} free")
    print("tools:")
    for name, version in report["tools"].items():
        print(f"  {name:<10} {version or 'MISSING'}")
    print("capabilities:")
    for name, state in report["capabilities"].items():
        print(f"  {name:<22} {state}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pa",
        description="Port Anything — analyze authorized game projects and plan Android ports.",
    )
    parser.add_argument("--version", action="version", version=f"port-anything {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="detect environment and build capabilities")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    analyze = sub.add_parser("analyze", help="analyze a file, APK or project directory")
    analyze.add_argument("path")
    analyze.add_argument("--json", action="store_true")
    analyze.add_argument("--markdown", metavar="PATH", help="write a Markdown report")
    analyze.set_defaults(func=cmd_analyze)

    compat = sub.add_parser("compat", help="rank port strategies for an artifact")
    compat.add_argument("path")
    compat.add_argument("--target", default="android")
    compat.add_argument("--abi", default="arm64-v8a")
    compat.add_argument("--no-probe", action="store_true", help="skip tool probing; stages report unknown")
    compat.add_argument("--json", action="store_true")
    compat.add_argument("--markdown", metavar="PATH", help="write a Markdown report")
    compat.set_defaults(func=cmd_compat)

    analyzers = sub.add_parser("analyzers", help="list registered analyzers and their limits")
    analyzers.add_argument("--json", action="store_true")
    analyzers.set_defaults(func=cmd_analyzers)

    tokens = sub.add_parser("tokens", help="token estimation and lossless context planning")
    tsub = tokens.add_subparsers(dest="tokens_command", required=True)

    estimate_cmd = tsub.add_parser("estimate", help="estimate tokens for files or directories")
    estimate_cmd.add_argument("paths", nargs="+")
    estimate_cmd.add_argument("--json", action="store_true")
    estimate_cmd.set_defaults(func=cmd_tokens_estimate)

    tokens_index = tsub.add_parser("index", help="index sources with hashes and line counts")
    tokens_index.add_argument("paths", nargs="+")
    tokens_index.add_argument("--root")
    tokens_index.add_argument("--out", metavar="FILE", help="write the manifest JSON")
    tokens_index.add_argument("--json", action="store_true")
    tokens_index.set_defaults(func=cmd_tokens_index)

    tokens_plan = tsub.add_parser("plan", help="build a budgeted, lossless context plan")
    tokens_plan.add_argument("paths", nargs="+")
    tokens_plan.add_argument("--root")
    tokens_plan.add_argument("--budget", type=int)
    tokens_plan.add_argument("--query", default="")
    tokens_plan.add_argument("--must-keep", action="append", metavar="ID_OR_PATH")
    tokens_plan.add_argument("--out", metavar="FILE", help="write the plan JSON")
    tokens_plan.add_argument("--json", action="store_true")
    tokens_plan.add_argument("--no-dedupe", action="store_true")
    tokens_plan.add_argument("--no-check", action="store_true")
    tokens_plan.set_defaults(func=cmd_tokens_plan)

    tokens_check = tsub.add_parser("check", help="verify a plan keeps every source line accounted for")
    tokens_check.add_argument("plan")
    tokens_check.add_argument("--manifest", required=True)
    tokens_check.add_argument("--budget", type=int)
    tokens_check.add_argument("--json", action="store_true")
    tokens_check.set_defaults(func=cmd_tokens_check)

    tokens_slice = tsub.add_parser("slice", help="retrieve an exact line range from an indexed source")
    tokens_slice.add_argument("manifest")
    tokens_slice.add_argument("source")
    tokens_slice.add_argument("start", type=int)
    tokens_slice.add_argument("end", type=int)
    tokens_slice.set_defaults(func=cmd_tokens_slice)

    tokens_dedupe = tsub.add_parser("dedupe", help="find exact and near-duplicate sources")
    tokens_dedupe.add_argument("paths", nargs="+")
    tokens_dedupe.add_argument("--root")
    tokens_dedupe.add_argument("--json", action="store_true")
    tokens_dedupe.set_defaults(func=cmd_tokens_dedupe)

    tokens_cache = tsub.add_parser("cache", help="inspect or clear the estimate cache")
    tokens_cache.add_argument("--clear", action="store_true")
    tokens_cache.add_argument("--json", action="store_true")
    tokens_cache.set_defaults(func=cmd_tokens_cache)

    ram = sub.add_parser("ram", help="measured memory policy and adaptive job runner")
    rsub = ram.add_subparsers(dest="ram_command", required=True)

    ram_status = rsub.add_parser("status", help="current memory sample and concurrency decision")
    ram_status.add_argument("--override", help="force a level (low, medium, high) or an integer")
    ram_status.add_argument("--json", action="store_true")
    ram_status.set_defaults(func=cmd_ram_status)

    ram_policy = rsub.add_parser("policy", help="show thresholds, concurrency map and live verdict")
    ram_policy.add_argument("--override")
    ram_policy.add_argument("--json", action="store_true")
    ram_policy.set_defaults(func=cmd_ram_policy)

    ram_run = rsub.add_parser("run", help="run jobs with adaptive concurrency")
    ram_run.add_argument("argv", nargs=argparse.REMAINDER, help="command to run (after --)")
    ram_run.add_argument("--manifest", metavar="FILE", help="JSON job manifest instead of a command")
    ram_run.add_argument("--override")
    ram_run.add_argument("--timeout", type=int, default=120)
    ram_run.add_argument("--json", action="store_true")
    ram_run.set_defaults(func=cmd_ram_run)

    ram_cache = rsub.add_parser("cache", help="inspect, trim or clear the artifact cache")
    ram_cache.add_argument("--root", help="cache directory (default: $PA_RAM_CACHE or user cache)")
    ram_cache.add_argument("--trim", type=int, metavar="BYTES", help="evict oldest entries above this size")
    ram_cache.add_argument("--evict-for", type=int, metavar="BYTES", help="free space for a new artifact")
    ram_cache.add_argument("--clear", action="store_true")
    ram_cache.add_argument("--json", action="store_true")
    ram_cache.set_defaults(func=cmd_ram_cache)

    ram_probe = rsub.add_parser("probe", help="sample memory over time and report the verdict")
    ram_probe.add_argument("--samples", type=int, default=5)
    ram_probe.add_argument("--interval", type=float, default=1.0)
    ram_probe.add_argument("--override")
    ram_probe.add_argument("--json", action="store_true")
    ram_probe.set_defaults(func=cmd_ram_probe)

    agents = sub.add_parser("agents", help="multi-agent contracts, scheduling and run state")
    asub = agents.add_subparsers(dest="agents_command", required=True)

    agents_list = asub.add_parser("list", help="list the nine expected agent files")
    agents_list.add_argument("--dir")
    agents_list.add_argument("--json", action="store_true")
    agents_list.set_defaults(func=cmd_agents_list)

    agents_validate = asub.add_parser("validate", help="check agent files against the contract")
    agents_validate.add_argument("--dir")
    agents_validate.add_argument("--json", action="store_true")
    agents_validate.set_defaults(func=cmd_agents_validate)

    agents_graph = asub.add_parser("graph", help="validate a task manifest and show its DAG")
    agents_graph.add_argument("--manifest", required=True)
    agents_graph.add_argument("--json", action="store_true")
    agents_graph.set_defaults(func=cmd_agents_graph)

    agents_plan = asub.add_parser("plan", help="compute the next dispatch batch")
    agents_plan.add_argument("--manifest", required=True)
    agents_plan.add_argument("--store", help="run state file (created or resumed)")
    agents_plan.add_argument("--concurrency", type=int, default=2)
    agents_plan.add_argument("--apply", action="store_true", help="mark the batch running")
    agents_plan.add_argument("--dry-run", action="store_true", help="simulate the whole schedule")
    agents_plan.add_argument("--json", action="store_true")
    agents_plan.set_defaults(func=cmd_agents_plan)

    agents_status = asub.add_parser("status", help="show a run store")
    agents_status.add_argument("--store", required=True)
    agents_status.add_argument("--json", action="store_true")
    agents_status.set_defaults(func=cmd_agents_status)

    agents_prompt = asub.add_parser("prompt", help="render one task as a sub-agent prompt")
    agents_prompt.add_argument("--manifest", required=True)
    agents_prompt.add_argument("--id", required=True)
    agents_prompt.add_argument("--run")
    agents_prompt.set_defaults(func=cmd_agents_prompt)

    apk = sub.add_parser("apk", help="Android toolchain, packaging, validation and device tests")
    asub2 = apk.add_subparsers(dest="apk_command", required=True)

    apk_env = asub2.add_parser("env", help="discover the Android toolchain and its limits")
    apk_env.add_argument("--json", action="store_true")
    apk_env.set_defaults(func=cmd_apk_env)

    apk_plan = asub2.add_parser("plan", help="build plan for a strategy or a target")
    apk_plan.add_argument("--strategy", default="native_port")
    apk_plan.add_argument("--target", help="analyze this path and use its recommended strategy")
    apk_plan.add_argument("--json", action="store_true")
    apk_plan.set_defaults(func=cmd_apk_plan)

    apk_selftest = asub2.add_parser("selftest", help="prove the packaging path on this host")
    apk_selftest.add_argument("--workdir")
    apk_selftest.add_argument("--keep", action="store_true")
    apk_selftest.add_argument("--json", action="store_true")
    apk_selftest.set_defaults(func=cmd_apk_selftest)

    apk_validate = asub2.add_parser("validate", help="inspect a packaged APK")
    apk_validate.add_argument("apk")
    apk_validate.add_argument("--package", help="expected package name")
    apk_validate.add_argument("--json", action="store_true")
    apk_validate.set_defaults(func=cmd_apk_validate)

    apk_smoke = asub2.add_parser("smoke", help="install and run on a device through adb")
    apk_smoke.add_argument("apk")
    apk_smoke.add_argument("--package")
    apk_smoke.add_argument("--activity")
    apk_smoke.add_argument("--device-only", action="store_true")
    apk_smoke.add_argument("--json", action="store_true")
    apk_smoke.set_defaults(func=cmd_apk_smoke)

    apk_build = asub2.add_parser("build", help="run the packaging pipeline from a spec file")
    apk_build.add_argument("--spec", required=True)
    apk_build.add_argument("--strategy", default="native_port")
    apk_build.add_argument("--no-device", action="store_true")
    apk_build.add_argument("--json", action="store_true")
    apk_build.set_defaults(func=cmd_apk_build)

    apk_native = asub2.add_parser("native", help="build native code for an ABI")
    apk_native.add_argument("--dir", dest="directory", required=True)
    apk_native.add_argument("--kind", choices=["rust", "cpp"], default="rust")
    apk_native.add_argument("--abi", default="arm64-v8a")
    apk_native.add_argument("--debug", action="store_true")
    apk_native.add_argument("--offline", action="store_true")
    apk_native.add_argument("--build-dir")
    apk_native.add_argument("--json", action="store_true")
    apk_native.set_defaults(func=cmd_apk_native)

    dataset = sub.add_parser("dataset", help="PAKB dataset operations")
    dsub = dataset.add_subparsers(dest="dataset_command", required=True)

    validate = dsub.add_parser("validate", help="schema and integrity validation")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_dataset_validate)

    manifest = dsub.add_parser("manifest", help="verify or regenerate manifest.json")
    manifest.add_argument("--write", action="store_true")
    manifest.add_argument("--version")
    manifest.add_argument("--json", action="store_true")
    manifest.set_defaults(func=cmd_dataset_manifest)

    index = dsub.add_parser("index", help="build the SQLite search index")
    index.add_argument("--force", action="store_true")
    index.add_argument("--json", action="store_true")
    index.set_defaults(func=cmd_dataset_index)

    search = dsub.add_parser("search", help="search records")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--category")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_dataset_search)

    show = dsub.add_parser("show", help="show a full record")
    show.add_argument("id")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_dataset_show)

    stats = dsub.add_parser("stats", help="index statistics")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(func=cmd_dataset_stats)

    compat = dsub.add_parser("compat", help="filter by compatibility capability")
    compat.add_argument("--capability", choices=["android", "arm64", "status"], default="android")
    compat.add_argument("--value", default="supported")
    compat.add_argument("--json", action="store_true")
    compat.set_defaults(func=cmd_dataset_compat)

    export = dsub.add_parser("export", help="export a JSON bundle")
    export.add_argument("--out", required=True)
    export.set_defaults(func=cmd_dataset_export)

    imp = dsub.add_parser("import", help="import a JSON bundle")
    imp.add_argument("source")
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--json", action="store_true")
    imp.set_defaults(func=cmd_dataset_import)

    install = sub.add_parser("install", help="install or verify the skill in a project or globally")
    scope = install.add_mutually_exclusive_group()
    scope.add_argument("--project", dest="global_scope", action="store_false", help="install into <project>/.opencode (default)")
    scope.add_argument("--global", dest="global_scope", action="store_true", help="install into ~/.config/opencode")
    install.set_defaults(global_scope=False)
    install.add_argument("--path", help="project directory for a project install (default: discover from cwd)")
    install.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    install.add_argument("--uninstall", action="store_true", help="remove files recorded in the manifest")
    install.add_argument("--check", action="store_true", help="verify an existing installation is current")
    install.add_argument("--json", action="store_true")
    install.set_defaults(func=cmd_install)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
