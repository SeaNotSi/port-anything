"""Read-only dataset queries against the generated SQLite index."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..util.paths import DATASETS_DIR
from .index import DB_NAME


class IndexMissingError(RuntimeError):
    pass


def _connect(index_path: Path) -> sqlite3.Connection:
    if not index_path.is_file():
        raise IndexMissingError(
            f"index not found at {index_path}; run: pa dataset index"
        )
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "category": row["category"],
        "name": row["name"],
        "version": row["version"],
        "description": row["description"],
        "android": row["compat_android"],
        "arm64": row["compat_arm64"],
        "status": row["compat_status"],
        "verification": row["verification"],
        "last_verified": row["last_verified"],
    }


def _fts_query(text: str) -> str:
    tokens = [token for token in "".join(c if c.isalnum() or c in "-_." else " " for c in text).split() if token]
    return " AND ".join(f'"{token}"*' for token in tokens)


def search(
    query: str,
    *,
    category: str | None = None,
    limit: int = 20,
    index_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    target = Path(index_path) if index_path else DATASETS_DIR / "index" / DB_NAME
    conn = _connect(target)
    try:
        rows: list[sqlite3.Row] = []
        if query.strip():
            fts = conn.execute("SELECT value FROM meta WHERE key='fts'").fetchone()
            if fts and fts["value"] == "1":
                try:
                    sql = (
                        "SELECT r.* FROM records_fts f JOIN records r ON r.id = f.id "
                        "WHERE records_fts MATCH ?"
                    )
                    params: list[Any] = [_fts_query(query)]
                    if category:
                        sql += " AND r.category = ?"
                        params.append(category)
                    sql += " ORDER BY rank LIMIT ?"
                    params.append(limit)
                    rows = conn.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                sql = (
                    "SELECT * FROM records WHERE (name LIKE ? OR description LIKE ? OR id LIKE ?)"
                )
                like = f"%{query}%"
                params = [like, like, like]
                if category:
                    sql += " AND category = ?"
                    params.append(category)
                sql += " ORDER BY name LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
        else:
            sql = "SELECT * FROM records"
            params = []
            if category:
                sql += " WHERE category = ?"
                params.append(category)
            sql += " ORDER BY category, name LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_summary(row) for row in rows]
    finally:
        conn.close()


def get(record_id: str, *, index_path: str | Path | None = None) -> dict[str, Any] | None:
    target = Path(index_path) if index_path else DATASETS_DIR / "index" / DB_NAME
    conn = _connect(target)
    try:
        row = conn.execute("SELECT json FROM records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            return None
        record = json.loads(row["json"])
        record["tags"] = [
            r["tag"] for r in conn.execute("SELECT tag FROM tags WHERE record_id=? ORDER BY tag", (record_id,))
        ]
        record["dependents"] = [
            r["record_id"] for r in conn.execute("SELECT record_id FROM deps WHERE dep_id=? ORDER BY record_id", (record_id,))
        ]
        return record
    finally:
        conn.close()


def stats(*, index_path: str | Path | None = None) -> dict[str, Any]:
    target = Path(index_path) if index_path else DATASETS_DIR / "index" / DB_NAME
    conn = _connect(target)
    try:
        meta = {key: value for key, value in conn.execute("SELECT key, value FROM meta")}
        categories = {
            row["category"]: row["count"]
            for row in conn.execute("SELECT category, COUNT(*) AS count FROM records GROUP BY category ORDER BY category")
        }
        verification = {
            row["verification"]: row["count"]
            for row in conn.execute(
                "SELECT verification, COUNT(*) AS count FROM records GROUP BY verification ORDER BY verification"
            )
        }
        return {
            "meta": meta,
            "categories": categories,
            "verification": verification,
            "total": sum(categories.values()),
        }
    finally:
        conn.close()


def by_compat(
    capability: str,
    value: str = "supported",
    *,
    index_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    column = {
        "android": "compat_android",
        "arm64": "compat_arm64",
        "status": "compat_status",
    }.get(capability)
    if column is None:
        raise ValueError("capability must be one of: android, arm64, status")
    target = Path(index_path) if index_path else DATASETS_DIR / "index" / DB_NAME
    conn = _connect(target)
    try:
        rows = conn.execute(
            f"SELECT * FROM records WHERE {column} = ? ORDER BY category, name", (value,)
        ).fetchall()
        return [_row_to_summary(row) for row in rows]
    finally:
        conn.close()
