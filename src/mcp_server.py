"""
MCP-сервер для работы с `.pdsprj` файлами Proteus 8.

Workflow: user расставляет компоненты в Proteus и сохраняет .pdsprj.
Claude через MCP читает схему, объясняет её, пишет ASM-код для AT89C51,
и записывает прошивку обратно в архив. Generator-of-schematics нет —
бинарный формат Proteus закрытый, генерация с нуля упирается в
library-section и pointer-семантику (см. HANDOFF.md).

Tools:
    parse_schematic    — структурированное JSON описание схемы
    explain_schematic  — человекочитаемая текстовая сводка
    get_asm / get_hex  — прочитать прошивку
    set_firmware       — записать новый ASM (и опц. HEX) в .pdsprj
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastmcp import FastMCP

from src import asm_tools, explain
from src.parser import parse_schematic as _parse_schematic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

mcp = FastMCP("pdsprj-mcp")


@mcp.tool
def parse_schematic(pdsprj_path: str) -> dict:
    """
    Парсит `.pdsprj` → JSON-схема:
        {components: [{refdes, device, x, y, package, pins, properties, ...}],
         wires: [{vertices: [[x,y], ...]}]}
    """
    sch = _parse_schematic(Path(pdsprj_path))
    return sch.model_dump(mode="json")


@mcp.tool
def explain_schematic(pdsprj_path: str) -> str:
    """
    Возвращает короткий markdown-summary схемы: компоненты с группировкой
    по device-type, координаты, провода. Используй как starting point
    для разговора с пользователем или для grounding'а ASM-генерации.
    """
    return explain.explain(Path(pdsprj_path))


@mcp.tool
def get_asm(pdsprj_path: str) -> str:
    """Возвращает содержимое `FIRMWARE/<MCU>/main.asm` из `.pdsprj`."""
    return asm_tools.get_asm(Path(pdsprj_path))


@mcp.tool
def get_hex(pdsprj_path: str) -> str:
    """Возвращает содержимое `Debug.HEX` (Intel HEX) из `.pdsprj`."""
    return asm_tools.get_hex(Path(pdsprj_path))


@mcp.tool
def set_firmware(
    pdsprj_path: str,
    asm_code: str,
    output_path: str | None = None,
    hex_code: str | None = None,
) -> str:
    """
    Записывает новый ASM (и опционально HEX) в `.pdsprj`. Остальные
    файлы архива не трогаются. Если `output_path` не задан — модифицируется
    исходный файл. Возвращает путь к результату.

    HEX можно не передавать — Proteus сам пересоберёт при первой
    симуляции, либо используй внешний ассемблер (ASEM-51).
    """
    out = asm_tools.set_asm(
        Path(pdsprj_path),
        asm_code=asm_code,
        output_path=Path(output_path) if output_path else None,
        hex_code=hex_code,
    )
    return str(out)


if __name__ == "__main__":
    mcp.run()
