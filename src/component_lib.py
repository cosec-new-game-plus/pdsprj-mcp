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
import struct
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

    import zipfile

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
    struct.pack_into("<ii", raw, xy_off, 0, 0)

    return ComponentTemplate(
        device=comp.device,
        package=comp.package,
        device_tag=comp.device_tag,
        template_hex=raw.hex(),
        refdes_len=rlen,
        xy_offset=xy_off,
        pin_offsets=pin_offsets or {},
        properties=dict(comp.properties),
        source_file=pdsprj_path.name,
        source_refdes=refdes,
    )


def instantiate(
    template: ComponentTemplate, refdes: str, x: int, y: int
) -> bytes:
    """
    Материализует template → готовый бинарный блок компонента с заданными
    refdes и координатами.

    Переписывает `[ff len refdes]` и `[x:i32 y:i32]`. Остальные байты
    (24B header flags, labels, свойства, pin block) остаются как есть.
    """
    if len(refdes) > 255:
        raise ValueError("refdes слишком длинный (max 255)")

    template_bytes = template.template_bytes
    original_rlen = template.refdes_len

    new_refdes = refdes.encode("ascii")
    new_rlen = len(new_refdes)

    # [ff][len][refdes_bytes×rlen][body...]
    head = bytes([0xFF, new_rlen]) + new_refdes
    # body в оригинальном template начинается на offset 2+original_rlen
    body = template_bytes[2 + original_rlen :]
    result = bytearray(head + body)

    # xy_offset в новом буфере сдвинут если refdes изменил длину
    new_xy_off = 2 + new_rlen
    struct.pack_into("<ii", result, new_xy_off, x, y)

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
    для каждого уникального device type.
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
    return saved
