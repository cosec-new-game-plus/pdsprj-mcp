"""
MCP-сервер для работы с `.pdsprj` файлами Proteus 8.

Tools:
    parse_schematic         — прочитать компоненты/провода из .pdsprj
    get_asm / get_hex       — прочитать прошивку
    set_asm                 — записать новый ASM (и опц. HEX)
    list_components         — список device types в библиотеке
    generate_pdsprj         — собрать новый .pdsprj из спецификации
    learn_component         — добавить новый компонент в библиотеку
                               из sample-файла
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastmcp import FastMCP

from src import asm_tools, component_lib
from src.generator import (
    ComponentSpec,
    SchematicSpec,
    WireSpec,
    generate_pdsprj as _generate_pdsprj,
)
from src.parser import parse_schematic as _parse_schematic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

mcp = FastMCP("pdsprj-mcp")


@mcp.tool
def parse_schematic(pdsprj_path: str) -> dict:
    """
    Парсит `.pdsprj` → возвращает JSON-схему:
        {components: [{refdes, device, x, y, package, pins, properties}], wires: [{vertices}]}
    """
    sch = _parse_schematic(Path(pdsprj_path))
    return sch.model_dump(mode="json")


@mcp.tool
def get_asm(pdsprj_path: str) -> str:
    """Возвращает содержимое `main.asm` из `.pdsprj`."""
    return asm_tools.get_asm(Path(pdsprj_path))


@mcp.tool
def get_hex(pdsprj_path: str) -> str:
    """Возвращает содержимое `Debug.HEX` из `.pdsprj`."""
    return asm_tools.get_hex(Path(pdsprj_path))


@mcp.tool
def set_asm(
    pdsprj_path: str,
    asm_code: str,
    output_path: str | None = None,
    hex_code: str | None = None,
) -> str:
    """Записывает ASM (+ опц. HEX) в `.pdsprj`. Возвращает путь к результату."""
    out = asm_tools.set_asm(
        Path(pdsprj_path),
        asm_code=asm_code,
        output_path=Path(output_path) if output_path else None,
        hex_code=hex_code,
    )
    return str(out)


@mcp.tool
def list_components() -> list[str]:
    """Список device types, доступных для генерации."""
    return component_lib.list_devices()


@mcp.tool
def generate_pdsprj(
    output_path: str,
    components: list[dict],
    wires: list[dict] | None = None,
    chassis_path: str | None = None,
    asm_code: str | None = None,
) -> str:
    """
    Генерирует новый `.pdsprj`.

    components: [{"device": str, "refdes": str, "x": int, "y": int}, ...]
    wires:      [{"vertices": [[x,y], [x,y], ...]}, ...]

    Координаты в Proteus-units (0.0001"). Шаг сетки обычно 0x10000=65536.

    chassis_path: путь к базовому .pdsprj (по умолчанию —
    встроенный task-2-1 с AT89C51 + осциллограф).
    """
    from src.generator import DEFAULT_CHASSIS

    spec = SchematicSpec(
        components=[ComponentSpec(**c) for c in components],
        wires=[WireSpec(**w) for w in (wires or [])],
    )
    chassis = Path(chassis_path) if chassis_path else DEFAULT_CHASSIS
    out = _generate_pdsprj(spec, Path(output_path), chassis=chassis, asm_code=asm_code)
    return str(out)


@mcp.tool
def learn_component(sample_pdsprj: str, refdes: str, device: str) -> str:
    """
    Добавляет компонент в библиотеку из sample-файла.

    Нужно когда device'а нет в `list_components()`. Пользователь один
    раз рисует его в Proteus, сохраняет как `.pdsprj`, и вызывает этот
    tool чтобы извлечь шаблон.
    """
    tpl = component_lib.extract_template(Path(sample_pdsprj), refdes, device=device)
    path = component_lib.save(tpl)
    return f"learned {device} → {path}"


if __name__ == "__main__":
    mcp.run()
