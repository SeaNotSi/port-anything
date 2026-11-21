"""Dataset validation: schema, uniqueness, dependency integrity, provenance."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..util import jsonl
from ..util.paths import DATASETS_DIR, SCHEMA_DIR
from ..util.schema import SchemaLoadError, Validator
from .model import CATEGORY_SCHEMA, Dataset, find_cycles, load_dataset, strip_internal


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }


def _check_record(
    record: dict[str, Any],
    validator: Validator,
    today: date,
    report: Report,
    where: str,
) -> None:
    category = record.get("category", "")
    schema_name = CATEGORY_SCHEMA.get(category, "record.schema.json")
    try:
        errors = validator.validate(strip_internal(record), schema_name)
    except SchemaLoadError as exc:
        report.errors.append(f"{where}: {exc}")
        return
    for message in errors:
        report.errors.append(f"{where}: {message}")

    verified = record.get("last_verified")
    if isinstance(verified, str):
        try:
            verified_date = datetime.strptime(verified, "%Y-%m-%d").date()
        except ValueError:
            report.errors.append(f"{where}: last_verified is not a real date: {verified!r}")
        else:
            if verified_date > today:
                report.errors.append(f"{where}: last_verified is in the future: {verified}")
            elif (today - verified_date).days > 365:
                report.warnings.append(
                    f"{where}: last_verified is more than a year old ({verified})"
                )

    if not record.get("limitations"):
        report.warnings.append(f"{where}: limitations list is empty; state the real ones")
    if not record.get("requirements"):
        report.warnings.append(f"{where}: requirements list is empty")

    license_block = record.get("license") or {}
    spdx = license_block.get("spdx", "")
    if spdx == "NOASSERTION" and record.get("verification") == "verified":
        report.warnings.append(f"{where}: verified record has NOASSERTION license")

    compat = record.get("compatibility") or {}
    if compat.get("android") == "supported" and compat.get("status") == "unsupported":
        report.errors.append(f"{where}: android supported but overall status unsupported")


def validate_dataset(datasets_dir: str | Path = DATASETS_DIR) -> Report:
    root = Path(datasets_dir)
    schema_dir = root / "schema" if (root / "schema").is_dir() else SCHEMA_DIR
    validator = Validator(schema_dir)
    report = Report()
    today = date.today()

    try:
        dataset = load_dataset(root)
    except jsonl.JsonlError as exc:
        report.errors.append(str(exc))
        return report

    seen: dict[str, str] = {}
    for record in dataset.records:
        where = f"{record.get('__file', '?')}:{record.get('__line', '?')} [{record.get('id', '?')}]"
        _check_record(record, validator, today, report, where)
        record_id = record.get("id")
        if isinstance(record_id, str):
            if record_id in seen:
                report.errors.append(
                    f"{where}: duplicate id {record_id!r}, first seen at {seen[record_id]}"
                )
            else:
                seen[record_id] = where

    by_id = dataset.by_id
    for record in dataset.records:
        for dep in record.get("dependencies", []):
            if dep not in by_id:
                report.errors.append(
                    f"[{record.get('id')}]: dependency {dep!r} does not exist in the dataset"
                )

    graph = {record["id"]: [d for d in record.get("dependencies", []) if d in by_id] for record in dataset.records}
    for cycle in find_cycles(graph):
        report.errors.append(f"dependency cycle: {' -> '.join(cycle + [cycle[0]])}")

    duplicates: dict[str, list[str]] = {}
    for record in dataset.records:
        key = f"{record.get('category')}|{record.get('name', '').strip().lower()}"
        duplicates.setdefault(key, []).append(record.get("id", "?"))
    for key, ids in duplicates.items():
        if len(ids) > 1:
            report.warnings.append(f"same category+name for {key}: {ids}")

    manifest = root / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            declared = data.get("total_records")
            if isinstance(declared, int) and declared != len(dataset.records):
                report.errors.append(
                    f"manifest total_records={declared} but dataset has {len(dataset.records)}"
                )
        except json.JSONDecodeError as exc:
            report.errors.append(f"manifest.json is not valid JSON: {exc}")

    report.stats = {
        "files": len(dataset.files),
        "records": len(dataset.records),
        "categories": dataset.categories(),
        "content_hash": dataset.content_hash(),
        "schemas_checked": sorted(
            {CATEGORY_SCHEMA.get(r.get("category", ""), "record.schema.json") for r in dataset.records}
        ),
    }
    return report
