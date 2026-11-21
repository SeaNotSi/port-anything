"""Analyzer implementations.

Importing this package registers every analyzer with :mod:`pa.re.registry`.
Order matters only for readability; the registry sorts by id.
"""

from __future__ import annotations

from . import apk, elf, gameformats, macho, pe, source, strings_scan  # noqa: F401

__all__ = ["apk", "elf", "gameformats", "macho", "pe", "source", "strings_scan"]
