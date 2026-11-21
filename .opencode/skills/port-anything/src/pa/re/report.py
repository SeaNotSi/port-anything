"""Text and Markdown rendering for analysis and compatibility reports."""

from __future__ import annotations

from typing import Any


def _format_line(bundle: dict[str, Any]) -> str:
    fmt = bundle.get("format", {})
    return f"{fmt.get('format')} ({fmt.get('family')}, {fmt.get('confidence')}) — {fmt.get('detail')}"


def _analyzer_status(entry: dict[str, Any]) -> str:
    if "error" in entry:
        return f"error: {entry['error']}"
    result = entry.get("result")
    if isinstance(result, dict):
        return f"ok ({len(result)} fields)"
    return "ok"


def render_text(bundle: dict[str, Any]) -> str:
    fp = bundle.get("fingerprint", {})
    lines: list[str] = []
    lines.append(f"path      : {bundle.get('path')}")
    lines.append(f"format    : {_format_line(bundle)}")

    engines = fp.get("engines", [])
    if engines:
        top = engines[0]
        lines.append(f"engine    : {top['name']} ({top['id']}) — {top['confidence']} score {top['score']}")
        for evidence in top["evidence"]:
            lines.append(f"            {evidence['source']:<12} {evidence['confidence']:<9} {evidence['detail']}")
        for other in engines[1:4]:
            lines.append(f"also      : {other['name']} — {other['confidence']} score {other['score']}")
    else:
        lines.append("engine    : unknown")

    if fp.get("runtime"):
        lines.append(f"runtime   : {', '.join(fp['runtime'])}")
    if fp.get("middleware"):
        lines.append(f"middleware: {', '.join(fp['middleware'])}")
    if fp.get("graphics"):
        lines.append(f"graphics  : {', '.join(fp['graphics'])}")
    platforms = ", ".join(fp.get("platforms", [])) or "unknown"
    archs = ", ".join(fp.get("architectures", [])) or "unknown"
    lines.append(f"platform  : {platforms} [{archs}]")
    lines.append(f"source    : {'yes' if fp.get('source_available') else 'no'}")
    if fp.get("build_systems"):
        lines.append(f"build     : {', '.join(fp['build_systems'])}")
    if fp.get("assets"):
        lines.append(f"assets    : {', '.join(fp['assets'])}")

    lines.append("analyzers :")
    for entry in bundle.get("analyzers", []):
        lines.append(f"  {entry.get('analyzer'):<16} {_analyzer_status(entry)}")

    compat = bundle.get("compatibility")
    if compat:
        lines.append("")
        lines.append(f"target    : {compat['target']['platform']} {compat['target']['abi']}")
        recommended = compat.get("recommended")
        if recommended:
            strategy = next(item for item in compat["strategies"] if item["id"] == recommended)
            lines.append(
                f"route     : {strategy['name']} ({strategy['confidence']}, score {strategy['score']})"
            )
            for reason in strategy["rationale"]:
                lines.append(f"            - {reason}")
            for blocker in strategy["blockers"]:
                lines.append(f"            blocker: {blocker}")
        else:
            lines.append("route     : none matched")
        if compat.get("blockers"):
            lines.append("blockers  :")
            for blocker in compat["blockers"]:
                lines.append(f"  - {blocker}")
        lines.append("stages    :")
        if recommended:
            strategy = next(item for item in compat["strategies"] if item["id"] == recommended)
            for stage in strategy["stages"]:
                lines.append(f"  {stage['name']:<10} {stage['status']:<14} {stage['detail']}")
        claims = compat["claims"]
        lines.append(
            f"claims    : apk_present={str(claims['apk_present']).lower()}"
            f" port_verified={str(claims['port_verified']).lower()}"
        )
        if compat.get("next_actions"):
            lines.append("next      :")
            for action in compat["next_actions"]:
                lines.append(f"  - {action}")

    for note in fp.get("notes", []):
        lines.append(f"note      : {note}")
    return "\n".join(lines)


def render_markdown(bundle: dict[str, Any]) -> str:
    fp = bundle.get("fingerprint", {})
    lines: list[str] = ["# Port Anything analysis", ""]
    lines.append(f"`{bundle.get('path')}`")
    lines.append("")
    lines.append(f"- **Format:** {_format_line(bundle)}")
    if fp.get("top_engine"):
        top = fp["engines"][0]
        lines.append(f"- **Engine:** {top['name']} (`{top['dataset_id']}`), {top['confidence']}, score {top['score']}")
    else:
        lines.append("- **Engine:** unknown")
    if fp.get("runtime"):
        lines.append(f"- **Runtime:** {', '.join(fp['runtime'])}")
    if fp.get("graphics"):
        lines.append(f"- **Graphics:** {', '.join(fp['graphics'])}")
    lines.append(f"- **Platforms:** {', '.join(fp.get('platforms', [])) or 'unknown'}")
    lines.append(f"- **Architectures:** {', '.join(fp.get('architectures', [])) or 'unknown'}")
    lines.append(f"- **Source available:** {'yes' if fp.get('source_available') else 'no'}")
    lines.append("")

    engines = fp.get("engines", [])
    if engines:
        lines.extend(["## Engine evidence", "", "| Engine | Confidence | Score | Evidence |", "|---|---|---|---|"])
        for engine in engines:
            detail = "; ".join(item["detail"] for item in engine["evidence"])
            lines.append(
                f"| {engine['name']} (`{engine['dataset_id']}`) | {engine['confidence']} | {engine['score']} | {detail} |"
            )
        lines.append("")

    lines.extend(["## Analyzers", "", "| Analyzer | Status |", "|---|---|"])
    for entry in bundle.get("analyzers", []):
        lines.append(f"| `{entry.get('analyzer')}` | {_analyzer_status(entry)} |")
    lines.append("")

    compat = bundle.get("compatibility")
    if compat:
        lines.extend(["## Port routes", "", f"Target: `{compat['target']['platform']}` / `{compat['target']['abi']}`", ""])
        lines.extend(["| Route | Confidence | Score | Blockers |", "|---|---|---|---|"])
        for strategy in compat["strategies"]:
            blockers = "; ".join(strategy["blockers"]) or "none"
            lines.append(f"| {strategy['name']} (`{strategy['id']}`) | {strategy['confidence']} | {strategy['score']} | {blockers} |")
        lines.append("")

        recommended = compat.get("recommended")
        if recommended:
            strategy = next(item for item in compat["strategies"] if item["id"] == recommended)
            lines.extend([f"### Recommended: {strategy['name']}", ""])
            for reason in strategy["rationale"]:
                lines.append(f"- {reason}")
            lines.append("")
            lines.extend(["| Stage | Status | Detail |", "|---|---|---|"])
            for stage in strategy["stages"]:
                lines.append(f"| {stage['name']} | {stage['status']} | {stage['detail']} |")
            lines.append("")

        claims = compat["claims"]
        lines.append(
            f"**Claim status:** apk_present={str(claims['apk_present']).lower()}, "
            f"port_verified={str(claims['port_verified']).lower()}. {claims['statement']}."
        )
        lines.append("")
        if compat.get("next_actions"):
            lines.append("### Next actions")
            lines.append("")
            for action in compat["next_actions"]:
                lines.append(f"1. {action}")
            lines.append("")

    if fp.get("notes"):
        lines.append("## Notes")
        lines.append("")
        for note in fp["notes"]:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
