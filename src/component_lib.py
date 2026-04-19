"""
Библиотека компонентов для генерации `.pdsprj` с нуля.

Формат: для каждого device type хранится бинарный template, полученный
из sample-файла. Поля `refdes`, `x`, `y` в template занулены; их
значения прописываются при инстанцировании генератором.

Записи лежат в `components/<device>.json`:

```
{
  "device": "AT89C51",
  "package": "DIL40",
  "device_tag": 40,
  "template_hex": "...",       # нормализованные байты компонента
  "refdes_offset": 1,          # где в template записан len(refdes)+refdes
  "refdes_len": 2,             # длина refdes в оригинале (для ресайза)
  "xy_offset": 4,              # где лежат i32 x, i32 y
  "pin_offsets": {             # смещения пинов относительно (x,y)
    "P1.0": [1280, 0], ...
  }
}
```
"""

from __future__ import annotations

import json
import logging
import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from src.parser import Component, Schematic, parse_schematic

log = logging.getLogger(__name__)

COMPONENTS_DIR = Path(__file__).parent.parent / "components"


class ComponentTemplate(BaseModel):
    device: str
    package: str | None = None
    device_tag: int | None = None
    template_hex: str = Field(description="байты компонента с занулёнными refdes/x/y")
    refdes_len: int = Field(description="длина refdes в sample-файле (для reshape)")
    xy_offset: int = Field(description="позиция i32,i32 x,y внутри template")
    xy_pairs: list[tuple[int, int, int]] = Field(
        default_factory=list,
        description="(byte_offset, dx, dy) — позиции label (x,y) пар в template, относительные к orig_xy",
    )
    pin_offsets: dict[str, tuple[int, int]] = Field(default_factory=dict)
    properties: dict[str, str] = Field(default_factory=dict)
    source_file: str | None = None
    source_refdes: str | None = None

    @property
    def template_bytes(self) -> bytes:
        return bytes.fromhex(self.template_hex)


def extract_template(
    pdsprj_path: Path,
    refdes: str,
    device: str | None = None,
    pin_offsets: dict[str, tuple[int, int]] | None = None,
) -> ComponentTemplate:
    """
    Извлекает нормализованный template компонента из sample-файла.

    `device` обязателен когда в файле несколько компонентов с одинаковым
    refdes (в первую очередь — virtual primitives с пустым refdes).
    """
    sch = parse_schematic(pdsprj_path)
    candidates = [c for c in sch.components if c.refdes == refdes]
    if device is not None:
        candidates = [c for c in candidates if c.device == device]
    if not candidates:
        raise ValueError(
            f"refdes={refdes!r} device={device!r} не найден в {pdsprj_path}"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"refdes={refdes!r} device={device!r} неоднозначен в {pdsprj_path}: "
            f"{len(candidates)} компонентов"
        )
    comp = candidates[0]
    if not comp.device:
        raise ValueError(f"{refdes}: не определён device type")

    with zipfile.ZipFile(pdsprj_path) as zf:
        dsn = zf.read("ROOT.DSN")

    end = comp.dsn_end
    # Trim: парсер определяет границы компонента по `next_component`, но
    # между компонентами могут быть другие объекты — WIRE и TERMINAL.
    # Обрезаем template в начале первого такого объекта.
    cut = _find_next_object_boundary(dsn, comp.dsn_offset + 1, end)
    if cut is not None:
        end = cut
    raw = bytearray(dsn[comp.dsn_offset : end])
    rlen = len(comp.refdes)
    xy_off = 2 + rlen

    # Найти все (x:i32, y:i32) пары в template, близкие к orig_xy —
    # это label-позиции, pin-позиции и прочие embedded координаты.
    # При перемещении компонента их все надо обновить, иначе элементы
    # разбегаются и Proteus не рендерит компонент.
    xy_pairs = _find_xy_pairs_near(raw, comp.x, comp.y, bbox=2_500_000)
    # Занулить в template: чтобы template_hex содержал относительные
    # (dx, dy) offsets напрямую, instantiate просто добавит new_xy.
    for off, dx, dy in xy_pairs:
        struct.pack_into("<ii", raw, off, dx, dy)

    return ComponentTemplate(
        device=comp.device,
        package=comp.package,
        device_tag=comp.device_tag,
        template_hex=raw.hex(),
        refdes_len=rlen,
        xy_offset=xy_off,
        xy_pairs=xy_pairs,
        pin_offsets=pin_offsets or {},
        properties=dict(comp.properties),
        source_file=pdsprj_path.name,
        source_refdes=refdes,
    )


def _find_xy_pairs_near(
    data: bytes, orig_x: int, orig_y: int, bbox: int
) -> list[tuple[int, int, int]]:
    """
    Находит все оффсеты, где лежит (x:i32, y:i32) пара с |x-orig_x|<bbox
    и |y-orig_y|<bbox. Возвращает `[(offset, dx, dy), ...]`.

    Offsets не пересекающиеся — если пара найдена на off, следующий
    поиск идёт с off+8.
    """
    pairs: list[tuple[int, int, int]] = []
    i = 0
    while i + 8 <= len(data):
        x = struct.unpack_from("<i", data, i)[0]
        y = struct.unpack_from("<i", data, i + 4)[0]
        if abs(x - orig_x) < bbox and abs(y - orig_y) < bbox:
            pairs.append((i, x - orig_x, y - orig_y))
            i += 8
        else:
            i += 1
    return pairs


def instantiate(
    template: ComponentTemplate, refdes: str, x: int, y: int
) -> bytes:
    """
    Материализует template → готовый бинарный блок компонента с заданными
    refdes и координатами.

    Переписывает `[ff len refdes]`, xy компонента, И все embedded
    x/y-позиции labels (x_offsets/y_offsets) — иначе labels остаются на
    старых абсолютных координатах и Proteus не рендерит компонент.
    """
    if len(refdes) > 255:
        raise ValueError("refdes слишком длинный (max 255)")

    template_bytes = template.template_bytes
    original_rlen = template.refdes_len

    new_refdes = refdes.encode("ascii")
    new_rlen = len(new_refdes)

    head = bytes([0xFF, new_rlen]) + new_refdes
    body = template_bytes[2 + original_rlen :]
    result = bytearray(head + body)

    # Для каждого (offset, dx, dy) из xy_pairs: пишем (x+dx, y+dy).
    # Offsets сдвигаются если refdes изменил размер.
    delta = new_rlen - original_rlen
    for off, dx, dy in template.xy_pairs:
        new_off = off + delta if off >= 2 + original_rlen else off
        if 0 <= new_off <= len(result) - 8:
            struct.pack_into("<ii", result, new_off, x + dx, y + dy)

    return bytes(result)


# ---------- Library persistence ----------


def save(template: ComponentTemplate, components_dir: Path = COMPONENTS_DIR) -> Path:
    components_dir.mkdir(parents=True, exist_ok=True)
    path = components_dir / f"{_safe_name(template.device)}.json"
    path.write_text(template.model_dump_json(indent=2))
    log.info("saved %s", path)
    return path


def load(device: str, components_dir: Path = COMPONENTS_DIR) -> ComponentTemplate:
    path = components_dir / f"{_safe_name(device)}.json"
    return ComponentTemplate.model_validate_json(path.read_text())


def list_devices(components_dir: Path = COMPONENTS_DIR) -> list[str]:
    if not components_dir.exists():
        return []
    return sorted(p.stem for p in components_dir.glob("*.json"))


def _safe_name(device: str) -> str:
    return device.replace("/", "_").replace("\\", "_").replace("$", "S")


def _find_next_object_boundary(data: bytes, start: int, limit: int) -> int | None:
    """
    Находит начало ближайшего "не-компонентного" объекта в `[start, limit)`
    и возвращает оффсет, с которого начинается его заголовок.

    Известные маркеры:
      - WIRE: `\\x02\\x7fWIRE\\x00` (оффсет — сам anchor)
      - TERMINAL: `[len:u8] $TER...` — у терминала ПЕРЕД length-byte'ом
        лежит 13-байтный header `[x:i32][y:i32][5 байт flags]`. Возвращаем
        `offset - 13` чтобы обрезать вместе с header'ом.
    """
    candidates: list[int] = []
    w = data.find(b"\x02\x7fWIRE\x00", start, limit)
    if w != -1:
        candidates.append(w)
    i = start
    while i < limit - 10:
        if data[i] in (0x09, 0x0A, 0x0B, 0x0C) and data[i + 1 : i + 5] == b"$TER":
            n = data[i]
            name = data[i + 1 : i + 1 + n]
            if all(0x20 <= b < 0x7F for b in name):
                terminal_start = max(start, i - 13)
                candidates.append(terminal_start)
                break
        i += 1
    if not candidates:
        return None
    return min(candidates)


# ---------- Seeding ----------


def extract_terminal_template(
    pdsprj_path: Path, kind: str
) -> ComponentTemplate:
    """
    Извлекает binary template терминала (`$TERPOWER`, `$TERGROUND` и т.п.)
    из sample-файла. Терминал хранится в DSN как объект вида:

        [0x10][x:i32][y:i32][4B flags][len:u8][kind:ascii][метки + ~100B]

    Мы ищем первое вхождение `kind` в CIRCUIT секции, берём 13 байт
    заголовка назад, и до начала следующего объекта.
    """
    pdsprj_path = Path(pdsprj_path)
    with zipfile.ZipFile(pdsprj_path) as zf:
        dsn = zf.read("ROOT.DSN")
    cs = dsn.find(b"ISIS CIRCUIT FILE")
    ce = dsn.find(b"ISIS CIRCUIT FILE", cs + 1)
    ce = ce if ce != -1 else len(dsn)

    pattern = (
        b"\x10" + b".{12}" + bytes([len(kind)]) + re.escape(kind.encode("ascii"))
    )
    m = re.search(pattern, dsn[cs:ce], flags=re.DOTALL)
    if m is None:
        raise ValueError(f"{kind} не найден в CIRCUIT секции {pdsprj_path.name}")
    start = cs + m.start()
    # Ищем следующий объект после начала ТЕКУЩЕГО терминала + его header'а
    # (14 байт заголовка + len name + ~40 байт label'а внутри). Пропускаем
    # минимум 60 байт чтобы не зацепить внутренние $TER* строки (например
    # "TERMINAL LABEL" внутри терминала).
    search_from = start + 60
    boundary = _find_next_object_boundary(dsn, search_from, ce)
    end = boundary if boundary is not None else ce

    raw = bytearray(dsn[start:end])
    struct.pack_into("<ii", raw, 1, 0, 0)  # занулить xy

    return ComponentTemplate(
        device=kind,
        device_tag=None,
        template_hex=raw.hex(),
        refdes_len=0,
        xy_offset=1,
        source_file=pdsprj_path.name,
        source_refdes=kind,
    )


def instantiate_terminal(
    template: ComponentTemplate, x: int, y: int
) -> bytes:
    """Материализует terminal template с заданными координатами."""
    raw = bytearray(template.template_bytes)
    struct.pack_into("<ii", raw, template.xy_offset, x, y)
    return bytes(raw)


@dataclass
class SeedPlan:
    pdsprj: Path
    refdes: str
    pin_offsets: dict[str, tuple[int, int]] | None = None


def seed_from_dataset(
    dataset: Path, components_dir: Path = COMPONENTS_DIR
) -> list[str]:
    """
    Проходит по всем `.pdsprj` в `dataset`, извлекает по одному шаблону
    для каждого уникального device type + набор известных терминалов.
    """
    seen: set[str] = set()
    saved: list[str] = []
    for pdsprj in sorted(dataset.rglob("*.pdsprj")):
        sch = parse_schematic(pdsprj)
        for c in sch.components:
            if not c.device or c.device in seen:
                continue
            try:
                tpl = extract_template(pdsprj, c.refdes, device=c.device)
            except ValueError as e:
                log.warning("skip %s/%s: %s", pdsprj.name, c.refdes, e)
                continue
            save(tpl, components_dir)
            seen.add(c.device)
            saved.append(c.device)

    # Терминалы: отдельно, т.к. они не компоненты
    for kind in ["$TERPOWER", "$TERGROUND"]:
        if kind in seen:
            continue
        for pdsprj in sorted(dataset.rglob("*.pdsprj")):
            try:
                tpl = extract_terminal_template(pdsprj, kind)
                save(tpl, components_dir)
                seen.add(kind)
                saved.append(kind)
                break
            except ValueError:
                continue
    return saved
