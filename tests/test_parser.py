"""Тесты парсера на датасете ВШЭ (samples/hse-microcontrollers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdsprj_mcp.parser import Schematic, parse_schematic

DATASET = Path(__file__).parent.parent / "samples" / "hse-microcontrollers" / "Labs"

# Ожидаемые счётчики из FORMAT.md §4
EXPECTED = {
    "task-2-1.pdsprj": {"components": 2, "wires": 2},
    "task-2-2.pdsprj": {"components": 2, "wires": 2},
    "task-2-3.pdsprj": {"components": 3, "wires": 6},
    "task-2-4.pdsprj": {"components": 4, "wires": 7},
    "sem-3-1.pdsprj": {"components": 12, "wires": 38},
    "sem-3-2.pdsprj": {"components": 3, "wires": 11},
    "sem-3-3.pdsprj": {"components": 3, "wires": 11},
    "sem-04.pdsprj": {"components": 16, "wires": 29},
    "sem-4-1.pdsprj": {"components": 3, "wires": 14},
}


@pytest.fixture(scope="session")
def sample_files() -> list[Path]:
    files = sorted(DATASET.rglob("*.pdsprj"))
    if not files:
        pytest.skip(f"Нет sample-файлов в {DATASET}")
    return files


@pytest.mark.parametrize("name,expected", list(EXPECTED.items()))
def test_counts(name: str, expected: dict) -> None:
    matches = list(DATASET.rglob(name))
    if not matches:
        pytest.skip(f"Нет {name}")
    sch = parse_schematic(matches[0])
    assert len(sch.components) == expected["components"], (
        f"{name}: компоненты {len(sch.components)} != ожидаемо {expected['components']}"
    )
    assert len(sch.wires) == expected["wires"], (
        f"{name}: провода {len(sch.wires)} != ожидаемо {expected['wires']}"
    )


def test_at89c51_present_in_all(sample_files: list[Path]) -> None:
    """AT89C51 должен быть в каждом файле датасета (курс микроконтроллеров)."""
    for f in sample_files:
        sch = parse_schematic(f)
        mcus = [c for c in sch.components if c.device == "AT89C51"]
        assert len(mcus) == 1, f"{f.name}: AT89C51 не найден или найдено >1"
        mcu = mcus[0]
        assert mcu.package == "DIL40", f"{f.name}: package MCU = {mcu.package}"
        assert len(mcu.pins) == 40, f"{f.name}: pins MCU = {len(mcu.pins)}"
        assert "PROGRAM" in mcu.properties, f"{f.name}: у MCU нет PROGRAM prop"


def test_sem_3_1_multi_components(sample_files: list[Path]) -> None:
    """sem-3-1 имеет 7SEG + несколько BUTTON (самая насыщенная схема)."""
    f = next((f for f in sample_files if f.name == "sem-3-1.pdsprj"), None)
    if f is None:
        pytest.skip("нет sem-3-1.pdsprj")
    sch = parse_schematic(f)
    devices = [c.device for c in sch.components if c.device]
    assert any("7SEG" in d for d in devices)
    assert sum(1 for d in devices if "BUTTON" in d) >= 5


def test_wire_vertices_valid(sample_files: list[Path]) -> None:
    """Провода имеют ≥2 вершины."""
    for f in sample_files:
        sch = parse_schematic(f)
        for w in sch.wires:
            assert len(w.vertices) >= 2, f"{f.name}: провод с <2 вершинами"
