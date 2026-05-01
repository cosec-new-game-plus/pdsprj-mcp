"""
Парсер `.pdsprj` файлов Proteus 8.

Извлекает схему (компоненты + провода) из `ROOT.DSN` и дополняет её
метаданными из `ROOT.CDB` (package, pin-маппинг).

См. FORMAT.md для описания бинарного формата.
"""

from __future__ import annotations

import logging
import re
import struct
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


_DEVICE_RE = re.compile(rb"^[A-Z$][A-Z0-9\-_]{1,31}$")
_PIN_NAME_RE = re.compile(rb"^[A-Z0-9$./\\_\-\s]+$")
_PIN_NUM_RE = re.compile(rb"^[0-9]+$")
_PRINTABLE_RE = re.compile(rb"^[\x20-\x7e]+$")
_PROP_RE = re.compile(rb"\{([A-Z0-9_ ]{1,32})=([^}\n]{0,256})\}")

_WIRE_ANCHOR = b"\x02\x7fWIRE\x00"
_COMPONENT_ID_ANCHOR = b"Default Font\x00COMPONENT ID\x00"


class Pin(BaseModel):
    name: str
    number: str


class Component(BaseModel):
    refdes: str
    x: int
    y: int
    device: str | None = None
    value: str | None = None
    device_tag: int | None = Field(
        default=None,
        description="Pin-block tag from DSN (обычно = числу пинов пакета)",
    )
    package: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    pins: list[Pin] = Field(default_factory=list)
    dsn_offset: int = Field(
        default=0,
        description="Оффсет начала записи в ROOT.DSN (для отладки / splice)",
    )
    dsn_end: int = 0


class Wire(BaseModel):
    vertices: list[tuple[int, int]]

    @property
    def is_polyline(self) -> bool:
        return len(self.vertices) > 2


class Schematic(BaseModel):
    components: list[Component]
    wires: list[Wire]
    source_file: Path | None = None


def parse_schematic(pdsprj_path: Path) -> Schematic:
    """Парсит `.pdsprj` → `Schematic` с компонентами и проводами."""
    pdsprj_path = Path(pdsprj_path)
    with zipfile.ZipFile(pdsprj_path) as zf:
        dsn = zf.read("ROOT.DSN")
        try:
            cdb = zf.read("ROOT.CDB")
        except KeyError:
            cdb = b""

    cs, ce = _find_circuit(dsn)
    components = _parse_components(dsn, cs, ce)
    wires = _parse_wires(dsn, cs, ce)
    if cdb:
        _enrich_from_cdb(components, cdb)
    return Schematic(components=components, wires=wires, source_file=pdsprj_path)


def _find_circuit(data: bytes) -> tuple[int, int]:
    """Возвращает `[start, end)` первой секции `ISIS CIRCUIT FILE` (сама схема)."""
    start = data.find(b"ISIS CIRCUIT FILE")
    if start == -1:
        raise ValueError("ROOT.DSN: нет секции ISIS CIRCUIT FILE")
    end = data.find(b"ISIS CIRCUIT FILE", start + 1)
    return start, end if end != -1 else len(data)


def _parse_components(data: bytes, start: int, end: int) -> list[Component]:
    headers = _find_component_headers(data, start, end)
    result: list[Component] = []
    for idx, (off, refdes, x, y) in enumerate(headers):
        next_off = headers[idx + 1][0] if idx + 1 < len(headers) else end
        body_start = off + 2 + len(refdes) + 8
        labels = _parse_labels(data, body_start, next_off)
        props = _parse_props(data, body_start, next_off)
        dev = _find_device_tag(data, body_start, next_off)
        result.append(
            Component(
                refdes=refdes,
                x=x,
                y=y,
                device=dev[1] if dev else labels.get("COMPONENT VALUE"),
                device_tag=dev[0] if dev else None,
                value=labels.get("COMPONENT VALUE"),
                properties=props,
                dsn_offset=off,
                dsn_end=next_off,
            )
        )
    return result


def _find_component_headers(
    data: bytes, start: int, end: int
) -> list[tuple[int, str, int, int]]:
    """
    Якорный поиск компонентов: от каждого `COMPONENT ID` label идём назад
    на `34 + len(refdes)` байт и проверяем разметку `ff [len] [refdes]
    [x:i32] [y:i32]`.
    """
    out: list[tuple[int, str, int, int]] = []
    i = start
    while True:
        pos = data.find(_COMPONENT_ID_ANCHOR, i, end)
        if pos == -1:
            break
        lh = pos - 2
        if lh < start or data[lh] != 0xFF or data[lh + 1] not in (0, 1, 2):
            i = pos + 1
            continue
        found: tuple[int, str, int, int] | None = None
        for rlen in range(0, 9):
            obj_start = lh - (34 + rlen)
            if obj_start < start or data[obj_start] != 0xFF:
                continue
            if data[obj_start + 1] != rlen:
                continue
            if rlen == 0:
                refdes = ""
            else:
                chunk = data[obj_start + 2 : obj_start + 2 + rlen]
                if not all(32 <= b < 127 for b in chunk):
                    continue
                refdes = chunk.decode("ascii")
            x, y = struct.unpack_from("<ii", data, obj_start + 2 + rlen)
            if not (-20_000_000 <= x <= 20_000_000 and -20_000_000 <= y <= 20_000_000):
                continue
            found = (obj_start, refdes, x, y)
            break
        if found is not None:
            out.append(found)
        i = pos + len(_COMPONENT_ID_ANCHOR)
    out.sort()
    return out


def _parse_labels(data: bytes, start: int, end: int) -> dict[str, str]:
    labels: dict[str, str] = {}
    i = start
    while i < end - 20:
        if data[i] != 0xFF or data[i + 1] not in (0x00, 0x01, 0x02):
            i += 1
            continue
        if data[i + 2 : i + 14] != b"Default Font":
            i += 1
            continue
        kind_start = i + 2 + len("Default Font") + 1
        kind_end = data.find(b"\x00", kind_start, kind_start + 64)
        if kind_end == -1:
            i += 1
            continue
        kind = data[kind_start:kind_end].decode("ascii", errors="replace")
        value = _find_value_after(data, kind_end + 1, min(end, kind_end + 128))
        if value is not None:
            labels.setdefault(kind, value)
        i = kind_end + 1
    return labels


def _find_value_after(data: bytes, start: int, limit: int) -> str | None:
    i = start
    while i < limit - 2:
        if data[i] != 0xFF:
            i += 1
            continue
        n = data[i + 1]
        if n == 0 or n > 0x80:
            i += 1
            continue
        if i + 2 + n > limit:
            return None
        chunk = data[i + 2 : i + 2 + n]
        if chunk.startswith(b"Default Font") or b"Default Font" in data[i + 2 : i + 2 + 16]:
            return None
        if all(32 <= b < 127 for b in chunk):
            return chunk.decode("ascii")
        i += 1
    return None


def _parse_props(data: bytes, start: int, end: int) -> dict[str, str]:
    chunk = data[start:end]
    props: dict[str, str] = {}
    for m in _PROP_RE.finditer(chunk):
        props[m.group(1).decode("ascii", "replace")] = m.group(2).decode("ascii", "replace")
    return props


def _find_device_tag(
    data: bytes, start: int, end: int
) -> tuple[int, str] | None:
    i = start
    while i < end - 4:
        tag = struct.unpack_from("<H", data, i)[0]
        if tag == 0 or tag > 0x200:
            i += 1
            continue
        n = data[i + 2]
        if n < 2 or n > 0x40 or i + 3 + n > end:
            i += 1
            continue
        name = data[i + 3 : i + 3 + n]
        if not _DEVICE_RE.match(name):
            i += 1
            continue
        return tag, name.decode("ascii")
    return None


def _parse_wires(data: bytes, start: int, end: int) -> list[Wire]:
    wires: list[Wire] = []
    i = start
    while True:
        pos = data.find(_WIRE_ANCHOR, i, end)
        if pos == -1:
            break
        header_end = pos + len(_WIRE_ANCHOR)
        if header_end + 4 > end:
            break
        pad = struct.unpack_from("<H", data, header_end)[0]
        count = struct.unpack_from("<H", data, header_end + 2)[0]
        vertices_end = header_end + 4 + count * 8
        if pad != 0 or count == 0 or count > 64 or vertices_end > end:
            i = pos + 1
            continue
        verts = [
            struct.unpack_from("<ii", data, header_end + 4 + k * 8)
            for k in range(count)
        ]
        wires.append(Wire(vertices=verts))
        i = vertices_end
    return wires


# ---------- CDB enrichment ----------


def _enrich_from_cdb(components: list[Component], cdb: bytes) -> None:
    """Заполняет `package`, `pins`, а также доуточняет `properties` из CDB."""
    by_refdes: dict[str, Component] = {c.refdes: c for c in components if c.refdes}
    for inst in _parse_cdb_instances(cdb):
        c = by_refdes.get(inst["refdes"])
        if c is None:
            continue
        c.package = inst["package"]
        if not c.value:
            c.value = inst["value"] or None
        if not c.device or c.device != inst["device"]:
            c.device = c.device or inst["device"]
        for k, v in inst["properties"].items():
            c.properties.setdefault(k, v)

    pin_blocks = _parse_cdb_pin_blocks(cdb)
    # Эвристика: самый большой блок пинов — MCU (AT89C51). Для прочих
    # packaged-компонентов пары pin_name/number тривиальны ("1"→"1"),
    # и не так важны для ASM-логики. Присваиваем полный блок MCU если он есть.
    if pin_blocks:
        biggest = max(pin_blocks, key=lambda b: len(b))
        if len(biggest) >= 10:
            for c in components:
                if c.device_tag == len(biggest):
                    c.pins = [Pin(name=n, number=num) for n, num in biggest]


def _parse_cdb_instances(cdb: bytes) -> list[dict]:
    out: list[dict] = []
    covered: set[int] = set()
    for off in range(len(cdb)):
        if off in covered:
            continue
        inst = _try_parse_cdb_instance(cdb, off)
        if inst is None:
            continue
        out.append(inst)
        approx_len = (
            1 + len(inst["refdes"])
            + 1 + len(inst["value"])
            + 1 + len(inst["device"])
            + 1 + len(inst["package"])
        )
        for k in range(off, off + approx_len):
            covered.add(k)
    return out


def _try_parse_cdb_instance(data: bytes, off: int) -> dict | None:
    try:
        refdes_len = data[off]
        if refdes_len == 0 or refdes_len > 16:
            return None
        refdes = data[off + 1 : off + 1 + refdes_len]
        if not _PRINTABLE_RE.match(refdes):
            return None
        i = off + 1 + refdes_len
        value_len = data[i]
        if value_len > 32:
            return None
        value = data[i + 1 : i + 1 + value_len]
        if value_len and not _PRINTABLE_RE.match(value):
            return None
        i += 1 + value_len
        device_len = data[i]
        if device_len < 2 or device_len > 32:
            return None
        device = data[i + 1 : i + 1 + device_len]
        if not _DEVICE_RE.match(device):
            return None
        i += 1 + device_len
        package_len = data[i]
        if package_len < 2 or package_len > 32:
            return None
        package = data[i + 1 : i + 1 + package_len]
        if not _PRINTABLE_RE.match(package):
            return None
        i += 1 + package_len
    except IndexError:
        return None

    brace = data.find(b"{", i, min(len(data), i + 16))
    if brace == -1:
        return None

    props: dict[str, str] = {}
    region = data[brace : brace + 4096]
    for m in _PROP_RE.finditer(region):
        props[m.group(1).decode("ascii", "replace")] = m.group(2).decode("ascii", "replace")

    return {
        "refdes": refdes.decode("ascii"),
        "value": value.decode("ascii") if value_len else "",
        "device": device.decode("ascii"),
        "package": package.decode("ascii"),
        "properties": props,
    }


def _parse_cdb_pin_blocks(data: bytes) -> list[list[tuple[str, str]]]:
    blocks: list[list[tuple[str, str]]] = []
    covered: set[int] = set()
    i = 0
    while i < len(data) - 8:
        if i in covered:
            i += 1
            continue
        pairs = _read_pin_pairs(data, i)
        if len(pairs) >= 2:
            blocks.append(pairs)
            end = i
            for name, num in pairs:
                end += 1 + len(name) + 1 + len(num)
            for k in range(i, end):
                covered.add(k)
            i = end
        else:
            i += 1
    return blocks


def _read_pin_pairs(data: bytes, start: int, max_pairs: int = 64) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    i = start
    for _ in range(max_pairs):
        if i + 2 >= len(data):
            break
        nlen = data[i]
        if nlen == 0 or nlen > 16:
            break
        name = data[i + 1 : i + 1 + nlen]
        if not _PIN_NAME_RE.match(name):
            break
        i += 1 + nlen
        if i >= len(data):
            break
        vlen = data[i]
        if vlen == 0 or vlen > 8:
            break
        num = data[i + 1 : i + 1 + vlen]
        if not _PIN_NUM_RE.match(num):
            break
        pairs.append((name.decode("ascii"), num.decode("ascii")))
        i += 1 + vlen
    return pairs
