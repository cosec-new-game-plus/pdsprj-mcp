"""Тесты round-trip парсер → генератор → парсер."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.component_lib import seed_from_dataset
from src.generator import (
    ComponentSpec,
    SchematicSpec,
    WireSpec,
    generate_pdsprj,
)
from src.parser import parse_schematic

DATASET = Path(__file__).parent.parent / "samples" / "hse-microcontrollers" / "Labs"
COMPONENTS = Path(__file__).parent.parent / "components"


@pytest.fixture(scope="session", autouse=True)
def _seed():
    if not COMPONENTS.exists() or not list(COMPONENTS.glob("*.json")):
        seed_from_dataset(DATASET)


def _sample(name: str) -> Path:
    files = list(DATASET.rglob(name))
    if not files:
        pytest.skip(f"нет {name}")
    return files[0]


@pytest.mark.parametrize(
    "name", ["task-2-1.pdsprj", "task-2-3.pdsprj", "sem-3-2.pdsprj"]
)
def test_empty_spec_preserves_chassis(tmp_path: Path, name: str) -> None:
    """Passthrough: пустой spec → chassis выход = chassis вход по компонентам/проводам."""
    src = _sample(name)
    orig = parse_schematic(src)
    out = tmp_path / "gen.pdsprj"
    generate_pdsprj(SchematicSpec(components=[], wires=[]), out, chassis=src)
    gen = parse_schematic(out)
    assert len(gen.components) == len(orig.components)
    assert len(gen.wires) == len(orig.wires)


def test_additions_appear_in_output(tmp_path: Path) -> None:
    """Добавленные spec-компоненты появляются поверх chassis-компонентов."""
    chassis = _sample("task-2-1.pdsprj")
    orig = parse_schematic(chassis)
    spec = SchematicSpec(
        components=[
            ComponentSpec(device="LED-BLUE", refdes="D1", x=500_000, y=500_000),
        ],
        wires=[WireSpec(vertices=[(500_000, 500_000), (600_000, 500_000)])],
    )
    out = tmp_path / "new.pdsprj"
    generate_pdsprj(spec, out, chassis=chassis)
    sch = parse_schematic(out)
    assert len(sch.components) == len(orig.components) + 1
    assert len(sch.wires) == len(orig.wires) + 1
    devices = {c.device for c in sch.components}
    assert "AT89C51" in devices  # из chassis
    assert "LED-BLUE" in devices  # из spec


def test_back_references_patched(tmp_path: Path) -> None:
    """
    После вставки N байт, u32-указатели на sc/ce должны быть сдвинуты
    на N. Без этого Proteus крашится (SEH trap).
    """
    import struct
    import zipfile

    chassis = _sample("task-2-1.pdsprj")
    spec = SchematicSpec(
        components=[],
        wires=[WireSpec(vertices=[(0, 0), (100_000, 0)])],
    )
    out = tmp_path / "patched.pdsprj"
    generate_pdsprj(spec, out, chassis=chassis)

    with zipfile.ZipFile(out) as zf:
        dsn = zf.read("ROOT.DSN")

    cs = dsn.find(b"ISIS CIRCUIT FILE")
    ce = dsn.find(b"ISIS CIRCUIT FILE", cs + 1)

    # Должен существовать u32 == ce где-то в SPICE-секции (back-ref)
    found_ce_ref = False
    for i in range(ce, len(dsn) - 4):
        if struct.unpack_from("<I", dsn, i)[0] == ce:
            found_ce_ref = True
            break
    assert found_ce_ref, "pointer на ce не обновлён — Proteus крэшнется"
