"""
Работа с ASM/HEX файлами внутри `.pdsprj`.

`.pdsprj` — обычный ZIP. Прошивка лежит в `FIRMWARE/<MCU>/main.asm`
и `FIRMWARE/<MCU>/Debug/Debug.HEX`. Компиляция ASM → HEX не
выполняется (этим занимается Proteus IDE / ASEM-51).
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)


def _find_firmware_files(zf: zipfile.ZipFile) -> tuple[str | None, str | None]:
    asm = next(
        (n for n in zf.namelist() if n.endswith("/main.asm") or n == "main.asm"),
        None,
    )
    hex_ = next((n for n in zf.namelist() if n.endswith("/Debug.HEX")), None)
    return asm, hex_


def get_asm(pdsprj_path: Path) -> str:
    """Возвращает содержимое `main.asm` из `.pdsprj`."""
    with zipfile.ZipFile(pdsprj_path) as zf:
        asm_name, _ = _find_firmware_files(zf)
        if asm_name is None:
            raise FileNotFoundError(f"main.asm не найден в {pdsprj_path}")
        return zf.read(asm_name).decode("ascii", errors="replace")


def get_hex(pdsprj_path: Path) -> str:
    """Возвращает содержимое `Debug.HEX`."""
    with zipfile.ZipFile(pdsprj_path) as zf:
        _, hex_name = _find_firmware_files(zf)
        if hex_name is None:
            raise FileNotFoundError(f"Debug.HEX не найден в {pdsprj_path}")
        return zf.read(hex_name).decode("ascii", errors="replace")


def set_asm(
    pdsprj_path: Path,
    asm_code: str,
    output_path: Path | None = None,
    hex_code: str | None = None,
) -> Path:
    """
    Записывает новый ASM (и опционально HEX) в `.pdsprj`.

    Если `output_path` не задан — модифицируется исходный файл. Все
    остальные файлы архива сохраняются как есть.
    """
    pdsprj_path = Path(pdsprj_path)
    output_path = Path(output_path) if output_path else pdsprj_path

    with zipfile.ZipFile(pdsprj_path) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
        asm_name, hex_name = _find_firmware_files(zf)

    if asm_name is None:
        raise FileNotFoundError(f"main.asm не найден в {pdsprj_path}")

    members[asm_name] = asm_code.encode("ascii", errors="replace")
    if hex_code is not None:
        if hex_name is None:
            raise FileNotFoundError(f"Debug.HEX не найден в {pdsprj_path}")
        members[hex_name] = hex_code.encode("ascii", errors="replace")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    log.info("wrote %s", output_path)
    return output_path
