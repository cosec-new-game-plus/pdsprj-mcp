"""
Human-readable summary of `.pdsprj` schematic.

Generates a short text description that helps Claude (or a person)
understand what's already on the board: what MCU, what passive
components, which pins are wired together, what's left unconnected.

Use it as a starting point for: explaining the schema to the user,
suggesting missing components for a task, or grounding ASM-generation
in actual pin assignments.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from src.parser import Component, Schematic, Wire, parse_schematic


def explain(pdsprj_path: Path) -> str:
    """Return a multi-line text summary of the schematic."""
    sch = parse_schematic(pdsprj_path)
    lines: list[str] = []
    lines.append(f"# {Path(pdsprj_path).name}")
    lines.append("")
    lines.append(_summarize_components(sch))
    lines.append("")
    lines.append(_summarize_wires(sch))
    nets = _infer_nets(sch)
    if nets:
        lines.append("")
        lines.append(_summarize_nets(nets))
    return "\n".join(lines)


def _summarize_components(sch: Schematic) -> str:
    by_device: dict[str, list[Component]] = defaultdict(list)
    for c in sch.components:
        by_device[c.device or "?"].append(c)
    lines = [f"## Components ({len(sch.components)})"]
    for device, comps in sorted(by_device.items()):
        refdeses = [c.refdes or "(unnamed)" for c in comps]
        lines.append(f"- **{device}** ×{len(comps)}: {', '.join(refdeses)}")
        for c in comps:
            details = []
            if c.package:
                details.append(f"pkg={c.package}")
            if c.value and c.value != device:
                details.append(f"value={c.value}")
            if c.pins:
                details.append(f"{len(c.pins)} pins")
            tail = f" — {', '.join(details)}" if details else ""
            lines.append(f"  - {c.refdes or '(unnamed)'} @ ({c.x}, {c.y}){tail}")
    return "\n".join(lines)


def _summarize_wires(sch: Schematic) -> str:
    lines = [f"## Wires ({len(sch.wires)})"]
    if not sch.wires:
        lines.append("- (none)")
        return "\n".join(lines)
    polylines = sum(1 for w in sch.wires if w.is_polyline)
    lines.append(
        f"- {len(sch.wires) - polylines} simple, {polylines} polyline (≥3 vertices)"
    )
    return "\n".join(lines)


def _infer_nets(sch: Schematic) -> list[list[tuple[str, str]]]:
    """
    Group pins that share a vertex with any wire.

    Returns a list of nets; each net is a list of `(refdes, pin_name)` pairs.
    Components without explicit pin offsets are skipped (parser only fills
    pin name/number, not coords). For now this is a placeholder — real
    pin-coord lookup needs library section reverse, see HANDOFF.md.
    """
    return []


def _summarize_nets(nets: list[list[tuple[str, str]]]) -> str:
    lines = [f"## Inferred nets ({len(nets)})"]
    for i, net in enumerate(nets, 1):
        members = ", ".join(f"{r}.{p}" for r, p in net)
        lines.append(f"- net{i}: {members}")
    return "\n".join(lines)
