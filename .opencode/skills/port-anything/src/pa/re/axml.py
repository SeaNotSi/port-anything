"""Android binary XML (AXML) parser.

Enough of the format to read AndroidManifest.xml: string pool, resource map,
start elements and typed attribute values. Bounds-checked throughout.
"""

from __future__ import annotations

import struct
from typing import Any

RES_STRING_POOL = 0x0001
RES_XML = 0x0003
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_RESOURCE_MAP = 0x0180

TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12


class AxmlError(ValueError):
    pass


def _read_string_pool(data: bytes, offset: int) -> list[str]:
    header_size, chunk_size = struct.unpack_from("<HH", data, offset + 2)
    string_count, style_count, flags, strings_start, styles_start = struct.unpack_from(
        "<IIIII", data, offset + 8
    )
    utf8 = bool(flags & 0x100)
    offsets = []
    cursor = offset + header_size
    for index in range(min(string_count, 65536)):
        if cursor + 4 > len(data):
            break
        offsets.append(struct.unpack_from("<I", data, cursor)[0])
        cursor += 4
    base = offset + strings_start
    strings: list[str] = []
    for relative in offsets:
        position = base + relative
        if position >= len(data):
            strings.append("")
            continue
        if utf8:
            # Two lengths: UTF-16 length then UTF-8 byte length, each 1 or 2 bytes.
            position += 1 if data[position] & 0x80 == 0 else 2
            if position >= len(data):
                strings.append("")
                continue
            byte_length = data[position]
            if byte_length & 0x80:
                byte_length = ((byte_length & 0x7F) << 8) | data[position + 1]
                position += 2
            else:
                position += 1
            raw = data[position : position + byte_length]
            strings.append(raw.decode("utf-8", errors="replace"))
        else:
            length = struct.unpack_from("<H", data, position)[0]
            position += 2
            if length & 0x8000:
                low = length & 0x7FFF
                high = struct.unpack_from("<H", data, position)[0]
                length = (high << 15) | low
                position += 2
            raw = data[position : position + length * 2]
            strings.append(raw.decode("utf-16-le", errors="replace"))
    return strings


def _string(strings: list[str], index: int) -> str:
    if 0 <= index < len(strings):
        return strings[index]
    return ""


def parse_axml(data: bytes) -> dict[str, Any]:
    if len(data) < 8:
        raise AxmlError("file too small for a binary XML header")
    chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, 0)
    if chunk_type != RES_XML:
        raise AxmlError(f"not binary XML (chunk type 0x{chunk_type:04x})")

    strings: list[str] = []
    resource_ids: list[int] = []
    elements: list[dict[str, Any]] = []

    cursor = header_size
    limit = min(len(data), chunk_size if chunk_size else len(data))
    while cursor + 8 <= limit:
        ctype, cheader, csize = struct.unpack_from("<HHI", data, cursor)
        if csize < 8 or cursor + csize > limit:
            break
        if ctype == RES_STRING_POOL:
            strings = _read_string_pool(data, cursor)
        elif ctype == RES_XML_RESOURCE_MAP:
            count = (csize - cheader) // 4
            for index in range(min(count, 65536)):
                resource_ids.append(struct.unpack_from("<I", data, cursor + cheader + index * 4)[0])
        elif ctype == RES_XML_START_ELEMENT:
            line_number, _comment, ns, name = struct.unpack_from("<IIII", data, cursor + 8)
            attribute_start, attribute_size, attribute_count = struct.unpack_from(
                "<HHH", data, cursor + 24
            )
            attributes: dict[str, Any] = {}
            # attributeStart is relative to the ResXMLTree_attrExt struct,
            # which begins 16 bytes into the chunk (8 header + line + comment).
            attr_cursor = cursor + 16 + attribute_start
            for index in range(min(attribute_count, 256)):
                base = attr_cursor + index * attribute_size
                if base + 20 > cursor + csize:
                    break
                a_ns, a_name, a_raw = struct.unpack_from("<III", data, base)
                value_size, _res0, value_type, value_data = struct.unpack_from("<HBBI", data, base + 12)
                key = _string(strings, a_name)
                if value_type == TYPE_STRING:
                    value: Any = _string(strings, value_data)
                elif value_type == TYPE_INT_BOOLEAN:
                    value = bool(value_data)
                elif value_type in (TYPE_INT_DEC, TYPE_INT_HEX):
                    value = value_data
                else:
                    value = _string(strings, a_raw) if a_raw else value_data
                attributes[key] = value
                resource_id = resource_ids[a_name] if 0 <= a_name < len(resource_ids) else None
                if resource_id is not None:
                    attributes.setdefault(f"@0x{resource_id:08x}", value)
            elements.append(
                {
                    "line": line_number,
                    "namespace": _string(strings, ns),
                    "name": _string(strings, name),
                    "attributes": attributes,
                }
            )
        cursor += csize

    return {
        "strings": strings[:512],
        "resource_ids": resource_ids[:256],
        "elements": elements,
        "element_count": len(elements),
    }
