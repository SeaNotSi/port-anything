"""Incremental dataset updates: import bundles, deduplicate, reindex.

Conflicts are never resolved by silently overwriting. A record with the same id
but different content is reported and skipped; the operator decides.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..util import jsonl
from ..util.paths import DATASETS_DIR, SCHEMA_DIR
from ..util.schema import Validator
from .index import build_index
from .manifest import write_manifest
from .model import CATEGORY_SCHEMA, load_dataset, strip_internal


def import_bundle(
    source: str | Path,
    *,
    dry_run: bool = False,
    datasets_dir: str | Path = DATASETS_DIR,
) -> dict[str, Any]:
    root = Path(datasets_dir)
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        return {"ok": False, "error": f"bundle not found: {source_path}"}

    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"bundle is not valid JSON: {exc}"}

    incoming = payload.get("records")
    if not isinstance(incoming, list):
        return {"ok": False, "error": "bundle has no records array"}

    validator = Validator(root / "schema" if (root / "schema").is_dir() else SCHEMA_DIR)
    existing = load_dataset(root)
    existing_by_id = existing.by_id

    new_records: list[dict[str, Any]] = []
    identical: list[str] = []
    conflicts: list[str] = []
    invalid: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, record in enumerate(incoming):
        if not isinstance(record, dict):
            invalid.append({"index": index, "errors": ["record is not an object"]})
            continue
        public = {k: v for k, v in record.items() if not k.startswith("__")}
        record_id = public.get("id")
        if not isinstance(record_id, str):
            invalid.append({"index": index, "errors": ["record has no string id"]})
            continue
        if record_id in seen:
            conflicts.append(f"{record_id}: duplicated inside the bundle")
            continue
        seen.add(record_id)

        schema_name = CATEGORY_SCHEMA.get(public.get("category", ""), "record.schema.json")
        errors = validator.validate(public, schema_name)
        if errors:
            invalid.append({"index": index, "id": record_id, "errors": errors})
            continue

        if record_id in existing_by_id:
            if jsonl.canonical(strip_internal(existing_by_id[record_id])) == jsonl.canonical(public):
                identical.append(record_id)
            else:
                conflicts.append(f"{record_id}: exists with different content; not overwritten")
            continue
        new_records.append(public)

    result: dict[str, Any] = {
        "ok": not invalid,
        "dry_run": dry_run,
        "incoming": len(incoming),
        "new": len(new_records),
        "identical": len(identical),
        "conflicts": conflicts,
        "invalid": invalid,
    }

    if dry_run or not new_records:
        return result

    target = root / "records" / "imported.jsonl"
    merged = jsonl.read_records(target) if target.is_file() else []
    known = {record["id"] for record in merged}
    merged.extend(record for record in new_records if record["id"] not in known)
    jsonl.write_records(target, merged)

    write_manifest(root)
    result["index"] = build_index(root)
    result["written"] = str(target)
    return result
