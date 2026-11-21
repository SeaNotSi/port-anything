"""Manifest generation and integrity checks for the dataset bundle."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..util.paths import DATASETS_DIR, MANIFEST_FILE
from .model import load_dataset


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    datasets_dir: str | Path = DATASETS_DIR,
    *,
    version: str | None = None,
) -> dict[str, Any]:
    root = Path(datasets_dir)
    dataset = load_dataset(root)

    existing: dict[str, Any] = {}
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    per_file: dict[str, int] = {}
    for record in dataset.records:
        name = record.get("__file", "")
        per_file[name] = per_file.get(name, 0) + 1

    records = []
    for path in dataset.files:
        rel = str(path.relative_to(root))
        records.append({"file": path.name, "path": rel, "records": per_file.get(rel, 0), "sha256": _sha256(path)})

    schemas = [
        {"file": path.name, "sha256": _sha256(path)}
        for path in sorted((root / "schema").glob("*.json"))
    ]

    return {
        "name": "Port Anything Knowledge Base",
        "short_name": "PAKB",
        "version": version or existing.get("version", "1.0.0"),
        "schema_version": "1.0.0",
        "description": (
            "Structured records for game engines, platforms, graphics APIs, Android, Rust, C++, "
            "toolchains, file formats, architectures and build systems used by Port Anything."
        ),
        "license": "CC-BY-4.0",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_records": len(dataset.records),
        "categories": dataset.categories(),
        "content_hash": dataset.content_hash(),
        "records": records,
        "schemas": schemas,
        "sources": "sources/source-registry.json",
        "index": "index/pakb.sqlite",
        "notes": [
            "JSONL records are canonical. The SQLite database is generated.",
            "Every record carries a source URL and a verification status.",
            "Records are reviewed, not scraped. Unknown facts are marked unverified.",
        ],
    }


def write_manifest(
    datasets_dir: str | Path = DATASETS_DIR,
    *,
    version: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(datasets_dir)
    manifest = build_manifest(root, version=version)
    if not dry_run:
        target = root / "manifest.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(target)
    return manifest


def verify_manifest(datasets_dir: str | Path = DATASETS_DIR) -> list[str]:
    """Compare declared hashes with the files on disk."""
    root = Path(datasets_dir)
    problems: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"manifest.json is invalid JSON: {exc}"]

    for entry in manifest.get("records", []):
        path = root / entry.get("path", f"records/{entry.get('file', '')}")
        if not path.is_file():
            problems.append(f"declared record file is missing: {path}")
            continue
        if _sha256(path) != entry.get("sha256"):
            problems.append(f"hash mismatch: {path}")
    for entry in manifest.get("schemas", []):
        path = root / "schema" / entry["file"]
        if not path.is_file():
            problems.append(f"declared schema is missing: {path}")
        elif _sha256(path) != entry.get("sha256"):
            problems.append(f"hash mismatch: {path}")

    dataset = load_dataset(root)
    if manifest.get("total_records") != len(dataset.records):
        problems.append(
            f"manifest total_records={manifest.get('total_records')} != {len(dataset.records)}"
        )
    if manifest.get("content_hash") != dataset.content_hash():
        problems.append("manifest content_hash does not match the records")
    return problems
