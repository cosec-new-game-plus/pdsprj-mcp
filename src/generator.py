"""
Генератор `.pdsprj` с нуля.

Стратегия splice: берём chassis-файл (шаблон всего архива), оставляем
неизменными библиотечную секцию ROOT.DSN, заголовок CIRCUIT секции и
SPICE-footer. Заменяем только список компонентов и проводов.

Требования к chassis:
- существующий .pdsprj любой сложности
- его используем как источник библиотечных ресурсов (шрифты, pin
  definitions, SPICE параметры)

Компоненты инстанцируем из `src/component_lib` (templates извлечены
из sample-файлов).
"""

from __future__ import annotations

import logging
import struct
import zipfile
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel, Field

from src import component_lib
from src.cdb_writer import (
    CDBComponentEntry,
    build_per_component_block,
    find_per_component_block,
    replace_per_component_block,
)
from src.parser import _find_circuit, _parse_components, _parse_wires, parse_schematic

log = logging.getLogger(__name__)

_WIRE_ANCHOR = b"\x02\x7fWIRE\x00"

DEFAULT_CHASSIS = (
    Path(__file__).parent.parent
    / "samples"
    / "hse-microcontrollers"
    / "Labs"
    / " Seminar-02"
    / "src"
    / "task-2-1.pdsprj"
)


class ComponentSpec(BaseModel):
    """Что и куда ставить на схему."""

    device: str
    refdes: str
    x: int
    y: int


class WireSpec(BaseModel):
    """Полилиния провода. Минимум 2 вершины."""

    vertices: list[tuple[int, int]] = Field(min_length=2, max_length=64)


class TerminalSpec(BaseModel):
    """VCC/GND терминал для питания и земли на схеме."""

    kind: str = Field(description="$TERPOWER (VCC) или $TERGROUND (GND)")
    x: int
    y: int


class SchematicSpec(BaseModel):
    components: list[ComponentSpec] = Field(default_factory=list)
    wires: list[WireSpec] = Field(default_factory=list)
    terminals: list[TerminalSpec] = Field(default_factory=list)


def generate_pdsprj(
    spec: SchematicSpec,
    output_path: Path,
    chassis: Path = DEFAULT_CHASSIS,
    asm_code: str | None = None,
    hex_code: str | None = None,
) -> Path:
    """Соберёт `.pdsprj` на `output_path` из `spec` + chassis."""
    output_path = Path(output_path)
    chassis = Path(chassis)

    with zipfile.ZipFile(chassis) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}

    dsn = members["ROOT.DSN"]
    new_dsn = _rebuild_dsn(dsn, spec)
    members["ROOT.DSN"] = new_dsn

    if "ROOT.CDB" in members and spec.components:
        members["ROOT.CDB"] = _rebuild_cdb(
            members["ROOT.CDB"], dsn, spec
        )

    if asm_code is not None or hex_code is not None:
        _patch_firmware(members, asm_code, hex_code)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    log.info("wrote %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


def _rebuild_dsn(dsn: bytes, spec: SchematicSpec) -> bytes:
    """
    Passthrough-режим: chassis DSN сохраняется целиком, новые компоненты
    и провода вставляются в конец CIRCUIT-секции (перед SPICE).

    При вставке байт **патчит back-references** в SPICE-секции
    (указатели на cs/ce), иначе Proteus падает т.к. читает мусор по
    сдвинувшемуся offset'у.
    """
    cs, ce = _find_circuit(dsn)
    insertion_point = _find_circuit_payload_end(dsn, cs, ce)

    comp_bytes = b"".join(_materialize_component(c) for c in spec.components)
    wire_bytes = b"".join(_materialize_wire(w) for w in spec.wires)
    term_bytes = b"".join(_materialize_terminal(t) for t in spec.terminals)
    inserted = comp_bytes + term_bytes + wire_bytes
    n = len(inserted)
    if n == 0:
        return dsn

    new_dsn = bytearray(dsn[:insertion_point] + inserted + dsn[insertion_point:])
    _patch_offset_references(new_dsn, insertion_point, n, orig_len=len(dsn))
    return bytes(new_dsn)


def _patch_offset_references(
    new_dsn: bytearray, insertion_point: int, shift: int, orig_len: int
) -> None:
    """
    Находит u32-значения в DSN, чьи значения лежат в `[insertion_point,
    orig_len]` — считаем их pointer'ами на позиции после вставки и
    сдвигаем на `shift`. Сканируется весь файл кроме самой вставленной
    области.
    """
    patched = 0
    for start, end in [
        (0, insertion_point - 3),
        (insertion_point + shift, len(new_dsn) - 4),
    ]:
        i = max(0, start)
        while i < end:
            v = struct.unpack_from("<I", new_dsn, i)[0]
            if insertion_point <= v <= orig_len:
                struct.pack_into("<I", new_dsn, i, v + shift)
                patched += 1
                i += 4
            else:
                i += 1
    log.info("patched %d back-references (+%d bytes)", patched, shift)


def _find_circuit_payload_end(dsn: bytes, cs: int, ce: int) -> int:
    """
    Возвращает точку вставки новых объектов — прямо перед началом
    следующей секции (SPICE), т.е. AFTER любых терминаторных `0xff`
    байт. Это сохраняет последовательность "wires → 0xff terminator"
    и добавляет наш контент после terminator'а.
    """
    return ce


def _find_last_wire_end(dsn: bytes, cs: int, ce: int) -> int:
    """Возвращает оффсет сразу после последнего провода в CIRCUIT секции."""
    last_end = None
    i = cs
    while True:
        pos = dsn.find(_WIRE_ANCHOR, i, ce)
        if pos == -1:
            break
        hdr_end = pos + len(_WIRE_ANCHOR)
        if hdr_end + 4 > ce:
            break
        pad, count = struct.unpack_from("<HH", dsn, hdr_end)
        if pad != 0 or count == 0 or count > 64:
            i = pos + 1
            continue
        verts_end = hdr_end + 4 + count * 8
        if verts_end > ce:
            break
        last_end = verts_end
        i = verts_end
    if last_end is None:
        # проводов в chassis нет — footer = с ce (начало SPICE)
        return ce
    return last_end


def _materialize_component(spec: ComponentSpec) -> bytes:
    tpl = component_lib.load(spec.device)
    return component_lib.instantiate(tpl, spec.refdes, spec.x, spec.y)


def _materialize_wire(spec: WireSpec) -> bytes:
    count = len(spec.vertices)
    buf = bytearray(_WIRE_ANCHOR)
    buf += struct.pack("<HH", 0, count)
    for x, y in spec.vertices:
        buf += struct.pack("<ii", x, y)
    return bytes(buf)


def _materialize_terminal(spec: TerminalSpec) -> bytes:
    tpl = component_lib.load(spec.kind)
    return component_lib.instantiate_terminal(tpl, spec.x, spec.y)


def _rebuild_cdb(cdb: bytes, chassis_dsn: bytes, spec: SchematicSpec) -> bytes:
    """
    Перестраивает per-component блок CDB с добавлением spec.components.

    Существующие entries chassis'а (MCU U1, осциллограф $IOSCILLOSCOPE и пр.)
    читаются из chassis CDB, новые компоненты добавляются в конец с
    инкрементальными id.
    """
    # Парсим существующие entries из chassis CDB
    existing = _parse_existing_cdb_entries(cdb)
    next_id = max((e.id for e in existing), default=1) + 1

    # Добавляем наши компоненты
    for c in spec.components:
        try:
            tpl = component_lib.load(c.device)
            pins = list(tpl.pin_offsets.keys())
        except FileNotFoundError:
            pins = []
        existing.append(CDBComponentEntry(id=next_id, refdes=c.refdes, pins=pins))
        next_id += 1

    return replace_per_component_block(cdb, existing)


def _parse_existing_cdb_entries(cdb: bytes) -> list[CDBComponentEntry]:
    """Парсит существующие per-component entries из chassis CDB."""
    start, end = find_per_component_block(cdb)
    block = cdb[start:end]
    entries: list[CDBComponentEntry] = []
    off = 0
    while off + 24 < len(block):  # минимум header + refdes_len + pin_count
        # Проверка на FFFFFFFF separator — сигнал конца entries
        if block[off : off + 4] == b"\xff\xff\xff\xff":
            # После ffffffff идёт TAIL, не парсим дальше
            break
        # Header 16B
        id_, const2, zero, id_rep = struct.unpack_from("<IIII", block, off)
        if const2 != 2 or zero != 0 or id_ != id_rep:
            break  # не похоже на entry
        off += 16
        rlen = block[off]
        off += 1
        refdes = block[off : off + rlen].decode("ascii") if rlen else ""
        off += rlen
        pin_count = struct.unpack_from("<I", block, off)[0]
        off += 4
        pins: list[str] = []
        for _ in range(pin_count):
            plen = block[off]
            off += 1
            pins.append(block[off : off + plen].decode("ascii"))
            off += plen + 1  # name + trailing 00
        # Trailer 8B
        off += 8
        # Separator 4B (zeros between entries)
        if off + 4 <= len(block) and block[off : off + 4] in (
            b"\x00\x00\x00\x00",
            b"\xff\xff\xff\xff",
        ):
            off += 4
        entries.append(CDBComponentEntry(id=id_, refdes=refdes, pins=pins))
    return entries


def pin_position(
    component: ComponentSpec, pin_name: str
) -> tuple[int, int]:
    """
    Абсолютные координаты пина компонента. Требует чтобы у device
    в библиотеке был прописан `pin_offsets[pin_name]`.
    """
    tpl = component_lib.load(component.device)
    if pin_name not in tpl.pin_offsets:
        raise KeyError(
            f"{component.device}: pin '{pin_name}' не известен. "
            f"Доступны: {sorted(tpl.pin_offsets)}"
        )
    dx, dy = tpl.pin_offsets[pin_name]
    return component.x + dx, component.y + dy


def connect_pin(
    a: ComponentSpec, pin_a: str, b: ComponentSpec, pin_b: str
) -> WireSpec:
    """
    Генерит L-образный провод между двумя пинами. Сначала горизонталь,
    потом вертикаль.
    """
    pa = pin_position(a, pin_a)
    pb = pin_position(b, pin_b)
    if pa[1] == pb[1]:
        vertices = [pa, pb]
    elif pa[0] == pb[0]:
        vertices = [pa, pb]
    else:
        # L-route: через (pb.x, pa.y)
        vertices = [pa, (pb[0], pa[1]), pb]
    return WireSpec(vertices=vertices)


def _patch_firmware(
    members: dict[str, bytes], asm: str | None, hex_: str | None
) -> None:
    """Заменяет main.asm и/или Debug.HEX в чlassis-архиве."""
    asm_path = next(
        (n for n in members if n.endswith("/main.asm") or n == "main.asm"),
        None,
    )
    hex_path = next(
        (n for n in members if n.endswith("/Debug.HEX")),
        None,
    )
    if asm is not None and asm_path:
        members[asm_path] = asm.encode("ascii", errors="replace")
    if hex_ is not None and hex_path:
        members[hex_path] = hex_.encode("ascii", errors="replace")
