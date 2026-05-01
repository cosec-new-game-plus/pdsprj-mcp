"""
Writer для ROOT.CDB (Connectivity DataBase).

Критично для генератора: когда добавляем новый компонент в DSN,
нужно также добавить его запись в per-component connectivity блок
CDB — иначе Proteus тихо не рендерит компонент.

Структура блока (reverse-engineered из samples 3l.pdsprj, butt.pdsprj):
  [Entry_1 separator] [Entry_2 separator] ... [Entry_N FFFFFFFF] [TAIL 36B]

Entry:
  [id:u32][const=2:u32][zero:u32][id:u32 repeat]      # 16B header
  [refdes_len:u8] [refdes:ascii]                       # 1+rlen
  [pin_count:u32]                                      # 4B (=0 для последнего entry!)
  (pins) × pin_count                                   # 3B каждый: len+name+00
  [zero:u32] [id_link:u32]                             # 8B trailer
                                                       # id_link=0 если refdes пустой

Separator:
  Между обычными entries: 00 00 00 00 (4B)
  После последнего entry (перед TAIL): FF FF FF FF (4B)

TAIL (36 bytes):
  [01]×3 [last_id:u32] [01]×2 [00]×12
  где last_id = id последнего entry с не-пустым refdes.
  Если нет entries с refdes — все 6 слотов = 01.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass
class CDBComponentEntry:
    id: int                  # уникальный ID (MCU=1, первый added=2, далее инкремент)
    refdes: str              # "" для virtual primitives без refdes
    pins: list[str]          # например ["A", "K"] для LED, [] для BUTTON


def build_per_component_block(entries: list[CDBComponentEntry]) -> bytes:
    """Собирает per-component connectivity блок из списка entries."""
    out = b""
    for i, c in enumerate(entries):
        is_last = i == len(entries) - 1
        has_refdes = bool(c.refdes)

        entry = struct.pack("<IIII", c.id, 2, 0, c.id)
        entry += bytes([len(c.refdes)]) + c.refdes.encode("ascii")

        if is_last:
            entry += struct.pack("<I", 0)
        else:
            entry += struct.pack("<I", len(c.pins))
            for p in c.pins:
                entry += bytes([len(p)]) + p.encode("ascii") + b"\x00"

        id_link = c.id if has_refdes else 0
        entry += struct.pack("<II", 0, id_link)

        out += entry + (b"\xff\xff\xff\xff" if is_last else b"\x00\x00\x00\x00")

    # TAIL
    refdes_entries = [c for c in entries if c.refdes]
    if refdes_entries:
        last_id = refdes_entries[-1].id
        tail = (
            b"\x01\x00\x00\x00" * 3
            + struct.pack("<I", last_id)
            + b"\x01\x00\x00\x00" * 2
        )
    else:
        tail = b"\x01\x00\x00\x00" * 6
    tail += b"\x00" * 12
    return out + tail


def find_per_component_block(cdb: bytes) -> tuple[int, int]:
    """
    Находит границы существующего per-component блока в CDB.
    Начало: сразу после MCU pin-definitions (~offset 542).
    Конец: перед началом первого component instance (маркер "\x02U1\x07AT89C51").
    """
    start = 542  # эмпирически — сразу после MCU 40-pin definitions
    end = cdb.find(b"\x02U1\x07AT89C51")
    if end == -1:
        raise ValueError("U1 AT89C51 instance не найден в CDB")
    return start, end


def replace_per_component_block(
    cdb: bytes, new_entries: list[CDBComponentEntry]
) -> bytes:
    """Заменяет per-component блок в CDB новым набором entries."""
    start, end = find_per_component_block(cdb)
    new_block = build_per_component_block(new_entries)
    return cdb[:start] + new_block + cdb[end:]
