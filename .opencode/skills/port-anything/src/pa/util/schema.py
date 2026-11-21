"""A dependency-free JSON Schema validator (subset).

Implements the keywords used by the PAKB schemas: ``$ref`` (local and
file-relative), ``type``, ``required``, ``properties``,
``additionalProperties``, ``enum``, ``const``, ``pattern``, string and array
lengths, ``minimum``/``maximum``, ``items``, ``uniqueItems``, ``allOf``,
``anyOf``, ``oneOf`` and ``not``.

Unsupported keywords are ignored deliberately rather than silently treated as
passing validation — :meth:`Validator.supported_keywords` lists what is
enforced, and the test suite asserts the PAKB schemas stay inside that set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SUPPORTED = {
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "title",
    "description",
    "type",
    "required",
    "properties",
    "additionalProperties",
    "enum",
    "const",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "items",
    "minItems",
    "maxItems",
    "uniqueItems",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "default",
    "examples",
}


class SchemaLoadError(ValueError):
    pass


def _pointer(doc: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return doc
    if not pointer.startswith("/"):
        raise SchemaLoadError(f"unsupported $ref pointer: {pointer}")
    node = doc
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise SchemaLoadError(f"cannot walk $ref pointer at {token!r}")
    return node


def _is_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


class Validator:
    def __init__(self, schema_dir: str | Path):
        self.schema_dir = Path(schema_dir).resolve()
        self._docs: dict[Path, Any] = {}

    def supported_keywords(self) -> set[str]:
        return set(SUPPORTED)

    def load(self, name: str | Path) -> tuple[Path, Any]:
        path = Path(name)
        if not path.is_absolute():
            path = self.schema_dir / path
        path = path.resolve()
        if path not in self._docs:
            try:
                self._docs[path] = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise SchemaLoadError(f"schema not found: {path}") from exc
            except json.JSONDecodeError as exc:
                raise SchemaLoadError(f"schema is not valid JSON: {path}: {exc}") from exc
        return path, self._docs[path]

    def validate(self, instance: Any, schema_name: str | Path) -> list[str]:
        doc_path, doc = self.load(schema_name)
        errors: list[str] = []
        self._validate(instance, doc, "$", doc, doc_path, errors)
        return errors

    def _validate(
        self,
        value: Any,
        schema: Any,
        path: str,
        root: Any,
        root_path: Path,
        errors: list[str],
    ) -> None:
        if not isinstance(schema, dict):
            return

        if "$ref" in schema:
            ref = schema["$ref"]
            file_part, _, pointer = ref.partition("#")
            if file_part:
                target_path, target_doc = self.load((root_path.parent / file_part).resolve())
                self._validate(value, target_doc, path, target_doc, target_path, errors)
            else:
                self._validate(value, _pointer(root, pointer), path, root, root_path, errors)
            # A $ref may sit next to other keywords in draft 2020-12; validate them too.
            rest = {k: v for k, v in schema.items() if k != "$ref"}
            if rest:
                self._validate(value, rest, path, root, root_path, errors)

        if "type" in schema:
            types = schema["type"]
            types = [types] if isinstance(types, str) else list(types)
            if not any(_is_type(value, t) for t in types):
                errors.append(f"{path}: expected type {'/'.join(types)}, got {type(value).__name__}")
                return

        if "const" in schema and value != schema["const"]:
            errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")

        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} is not one of {schema['enum']}")

        if isinstance(value, str):
            pattern = schema.get("pattern")
            if pattern and re.search(pattern, value) is None:
                errors.append(f"{path}: {value!r} does not match pattern {pattern!r}")
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(f"{path}: string shorter than {schema['minLength']}")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"{path}: string longer than {schema['maxLength']}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{path}: {value} below minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: {value} above maximum {schema['maximum']}")
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                errors.append(f"{path}: {value} must be > {schema['exclusiveMinimum']}")
            if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
                errors.append(f"{path}: {value} must be < {schema['exclusiveMaximum']}")

        if isinstance(value, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in value:
                    errors.append(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            for key, subschema in properties.items():
                if key in value:
                    self._validate(value[key], subschema, f"{path}.{key}", root, root_path, errors)
            additional = schema.get("additionalProperties", True)
            if additional is False:
                unknown = sorted(set(value) - set(properties) - set(schema.get("$defs", {})))
                for key in unknown:
                    errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                for key in sorted(set(value) - set(properties)):
                    self._validate(value[key], additional, f"{path}.{key}", root, root_path, errors)

        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append(f"{path}: fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(f"{path}: more than {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                seen: list[str] = []
                for item in value:
                    marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                    if marker in seen:
                        errors.append(f"{path}: duplicate array item {item!r}")
                        break
                    seen.append(marker)
            items = schema.get("items")
            if isinstance(items, dict):
                for index, item in enumerate(value):
                    self._validate(item, items, f"{path}[{index}]", root, root_path, errors)

        for index, subschema in enumerate(schema.get("allOf", [])):
            self._validate(value, subschema, path, root, root_path, errors)

        if "anyOf" in schema:
            matches = 0
            for subschema in schema["anyOf"]:
                probe: list[str] = []
                self._validate(value, subschema, path, root, root_path, probe)
                if not probe:
                    matches += 1
            if matches == 0:
                errors.append(f"{path}: does not match any anyOf branch")

        if "oneOf" in schema:
            matches = 0
            for subschema in schema["oneOf"]:
                probe = []
                self._validate(value, subschema, path, root, root_path, probe)
                if not probe:
                    matches += 1
            if matches != 1:
                errors.append(f"{path}: matches {matches} oneOf branches, expected exactly 1")

        if "not" in schema:
            probe = []
            self._validate(value, schema["not"], path, root, root_path, probe)
            if not probe:
                errors.append(f"{path}: matched a forbidden 'not' schema")
