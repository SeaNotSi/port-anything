"""Strategy to build-plan mapping.

The seven port routes are not interchangeable, and neither are their build
pipelines. An emulator APK is a launcher; a compatibility-layer APK carries a
translation runtime; a native port rebuilds the code. Calling all of them "the
APK" is how a project ends up claiming a port it never made.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..re.compat import STRATEGY_IDS, STRATEGY_NAMES


@dataclass(frozen=True)
class BuildPlan:
    strategy: str
    name: str
    needs_source: bool
    native_build: str
    steps: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "name": self.name,
            "needs_source": self.needs_source,
            "native_build": self.native_build,
            "steps": list(self.steps),
            "notes": list(self.notes),
        }


_COMMON_TAIL = ("align", "sign", "verify", "install", "launch", "runtime")

PLANS: dict[str, BuildPlan] = {
    "already_android": BuildPlan(
        strategy="already_android",
        name=STRATEGY_NAMES["already_android"],
        needs_source=False,
        native_build="none",
        steps=("extract", "validate", *(_COMMON_TAIL[1:])),
        notes=(
            "an existing APK is the starting point, not the result",
            "adaptation work is measured against the original behaviour",
        ),
    ),
    "native_port": BuildPlan(
        strategy="native_port",
        name=STRATEGY_NAMES["native_port"],
        needs_source=True,
        native_build="rust-or-cpp",
        steps=("toolchain", "compile", "link", "dex", "embed", *_COMMON_TAIL),
        notes=(
            "no engine translation layer: the source is rebuilt for Android",
            "the framework android.jar is required to link a manifest with resources",
        ),
    ),
    "engine_recompile": BuildPlan(
        strategy="engine_recompile",
        name=STRATEGY_NAMES["engine_recompile"],
        needs_source=True,
        native_build="engine-export",
        steps=("engine_export", "assets", "link", "dex", "embed", *_COMMON_TAIL),
        notes=(
            "re-export from the engine's Android target before touching code",
            "a re-export that does not run is a packaging result, not a port",
        ),
    ),
    "adaptation": BuildPlan(
        strategy="adaptation",
        name=STRATEGY_NAMES["adaptation"],
        needs_source=False,
        native_build="patched-binary",
        steps=("extract", "patch", "repackage", "embed", *_COMMON_TAIL),
        notes=(
            "patched binaries must be re-signed with a key you control",
            "record the exact patch set; an unrecorded patch cannot be reproduced",
        ),
    ),
    "asset_migration": BuildPlan(
        strategy="asset_migration",
        name=STRATEGY_NAMES["asset_migration"],
        needs_source=False,
        native_build="new-runtime",
        steps=("extract_assets", "runtime", "assets", "link", "dex", "embed", *_COMMON_TAIL),
        notes=(
            "the new runtime is a rewrite of the game loop, not a wrapper",
            "asset conversion fidelity is a testable claim, not a visual opinion",
        ),
    ),
    "compatibility_layer": BuildPlan(
        strategy="compatibility_layer",
        name=STRATEGY_NAMES["compatibility_layer"],
        needs_source=False,
        native_build="translation-layer",
        steps=("layer", "extract", "bundle", "embed", *_COMMON_TAIL),
        notes=(
            "CPU and OS translation layers have their own performance cost to measure",
            "the layer's license governs redistribution of the result",
        ),
    ),
    "emulator": BuildPlan(
        strategy="emulator",
        name=STRATEGY_NAMES["emulator"],
        needs_source=False,
        native_build="emulator-core",
        steps=("core", "rom", "frontend", "bundle", *_COMMON_TAIL),
        notes=(
            "the APK is a launcher around a core; the game is the image",
            "shipping a core you did not build has a license and a size cost",
        ),
    ),
}

assert set(PLANS) == set(STRATEGY_IDS), "every strategy needs a build plan"


def plan(strategy_id: str) -> BuildPlan:
    try:
        return PLANS[strategy_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown strategy {strategy_id!r}; expected one of {', '.join(STRATEGY_IDS)}"
        ) from exc


def from_compat(compat: dict[str, Any]) -> BuildPlan:
    """Pick the plan matching the recommended strategy in a compatibility bundle."""
    recommended = compat.get("recommended") or {}
    strategy_id = recommended.get("id") or compat.get("strategy") or "already_android"
    return plan(str(strategy_id))


def render(plan_out: BuildPlan) -> str:
    lines = [
        f"strategy : {plan_out.strategy} ({plan_out.name})",
        f"source   : {'rebuild required' if plan_out.needs_source else 'no rebuild'}",
        f"native   : {plan_out.native_build}",
        "steps    :",
    ]
    lines.extend(f"  {index}. {step}" for index, step in enumerate(plan_out.steps, start=1))
    if plan_out.notes:
        lines.append("notes    :")
        lines.extend(f"  - {note}" for note in plan_out.notes)
    return "\n".join(lines)