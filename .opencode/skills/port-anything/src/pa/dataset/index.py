"""Build the SQLite search index from canonical JSONL records.

The JSONL files are canonical. The database is a generated artefact and is
rebuilt from content hashes, so a stale index can always be detected.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..util.paths import DATASETS_DIR, INDEX_DIR
from .model import Dataset, load_dataset

DB_NAME = "pakb.sqlite"


def _schema_hash(datasets_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((datasets_dir / "schema").glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _dataset_version(datasets_dir: Path) -> str:
    manifest = datasets_dir / "manifest.json"
    if manifest.is_file():
        try:
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "0"))
        except (json.JSONDecodeError, OSError):
            return "0"
    return "0"


def _create_schema(conn: sqlite3.Connection) -> bool:
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE records (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            version TEXT,
            description TEXT NOT NULL,
            compat_android TEXT,
            compat_arm64 TEXT,
            compat_status TEXT,
            verification TEXT NOT NULL,
            last_verified TEXT NOT NULL,
            source_url TEXT NOT NULL,
            license_spdx TEXT NOT NULL,
            json TEXT NOT NULL
        );
        CREATE TABLE tags (
            record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            PRIMARY KEY (record_id, tag)
        );
        CREATE TABLE deps (
            record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            dep_id TEXT NOT NULL,
            PRIMARY KEY (record_id, dep_id)
        );
        CREATE TABLE files (
            path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            records INTEGER NOT NULL
        );
        CREATE INDEX idx_records_category ON records(category);
        CREATE INDEX idx_records_verification ON records(verification);
        CREATE INDEX idx_deps_dep ON deps(dep_id);
        """
    )
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE records_fts USING fts5(id UNINDEXED, name, description, tags)"
        )
        return True
    except sqlite3.OperationalError:
        return False


def build_index(
    datasets_dir: str | Path = DATASETS_DIR,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(datasets_dir)
    dataset: Dataset = load_dataset(root)
    target = Path(out_path) if out_path else root / "index" / DB_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    for stale in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        if stale.exists():
            stale.unlink()

    conn = sqlite3.connect(tmp)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA foreign_keys=ON")
        fts = _create_schema(conn)

        file_counts: dict[str, int] = {}
        for record in dataset.records:
            file_counts[record.get("__file", "?")] = file_counts.get(record.get("__file", "?"), 0) + 1

        for record in dataset.records:
            public = {k: v for k, v in record.items() if not k.startswith("__")}
            compat = public.get("compatibility", {})
            conn.execute(
                "INSERT INTO records (id, category, name, version, description, compat_android,"
                " compat_arm64, compat_status, verification, last_verified, source_url,"
                " license_spdx, json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    public["id"],
                    public["category"],
                    public["name"],
                    public.get("version", ""),
                    public["description"],
                    compat.get("android", ""),
                    compat.get("arm64", ""),
                    compat.get("status", ""),
                    public["verification"],
                    public["last_verified"],
                    public.get("source", {}).get("url", ""),
                    public.get("license", {}).get("spdx", ""),
                    json.dumps(public, ensure_ascii=False, sort_keys=True),
                ),
            )
            for tag in public.get("tags", []):
                conn.execute("INSERT OR IGNORE INTO tags (record_id, tag) VALUES (?,?)", (public["id"], tag))
            for dep in public.get("dependencies", []):
                conn.execute("INSERT OR IGNORE INTO deps (record_id, dep_id) VALUES (?,?)", (public["id"], dep))
            if fts:
                conn.execute(
                    "INSERT INTO records_fts (id, name, description, tags) VALUES (?,?,?,?)",
                    (public["id"], public["name"], public["description"], " ".join(public.get("tags", []))),
                )

        for path in dataset.files:
            rel = str(path.relative_to(root))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            conn.execute(
                "INSERT INTO files (path, sha256, records) VALUES (?,?,?)",
                (rel, digest, file_counts.get(rel, 0)),
            )

        meta = {
            "tool_version": __version__,
            "dataset_version": _dataset_version(root),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "records_hash": dataset.content_hash(),
            "schema_hash": _schema_hash(root),
            "record_count": str(len(dataset.records)),
            "fts": "1" if fts else "0",
        }
        conn.executemany("INSERT INTO meta (key, value) VALUES (?,?)", meta.items())
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp, target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(target) + suffix)
        if side.exists():
            side.unlink()
    return {
        "path": str(target),
        "records": len(dataset.records),
        "files": len(dataset.files),
        "fts": fts,
        "bytes": target.stat().st_size,
    }


def index_meta(index_path: str | Path) -> dict[str, str]:
    path = Path(index_path)
    if not path.is_file():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {key: value for key, value in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()


def needs_rebuild(
    datasets_dir: str | Path = DATASETS_DIR,
    index_path: str | Path | None = None,
) -> tuple[bool, str]:
    root = Path(datasets_dir)
    target = Path(index_path) if index_path else root / "index" / DB_NAME
    if not target.is_file():
        return True, "index does not exist"
    meta = index_meta(target)
    if not meta:
        return True, "index has no metadata"
    dataset = load_dataset(root)
    if meta.get("records_hash") != dataset.content_hash():
        return True, "record content changed"
    if meta.get("schema_hash") != _schema_hash(root):
        return True, "schema changed"
    return False, "index is current"
