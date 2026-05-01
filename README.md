# pdsprj-mcp

MCP-сервер (Model Context Protocol), позволяющий Claude читать и
изменять проекты Proteus 8 (`.pdsprj`). Рассчитан на типичный
сценарий микроконтроллерных лабораторных: вы рисуете схему в Proteus,
отдаёте файл Claude'у, а тот объясняет схему, пишет ассемблер для
8051 / AT89C51 и кладёт прошивку обратно в архив — не выходя из
переписки.

> **Не аффилирован с Labcenter Electronics Ltd.** «Proteus» — их
> торговая марка. Подробнее в [`NOTICE`](NOTICE).

## Что умеет

Сервер предоставляет пять MCP-инструментов:

| Инструмент | Назначение |
|---|---|
| `parse_schematic(path)` | JSON: компоненты, пины, провода, свойства |
| `explain_schematic(path)` | Markdown-сводка, сгруппирована по device-type |
| `get_asm(path)` | Прочитать `FIRMWARE/<MCU>/main.asm` |
| `get_hex(path)` | Прочитать `Debug.HEX` (Intel HEX) |
| `set_firmware(path, asm_code, output_path?, hex_code?)` | Записать новый ASM (и опц. HEX) в архив |

Остальные файлы внутри ZIP `.pdsprj` проходят без изменений.

## Требования

- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/) (рекомендуется) или `pip`
- Лицензионный Proteus Design Suite — для рисования схем и проверки
  результатов. Сам сервер Proteus не запускает.

## Установка

```bash
git clone https://github.com/danspiridonov/pdsprj-mcp.git
cd pdsprj-mcp
uv sync
```

`uv sync` создаст виртуальное окружение в `.venv/` и установит
консольную команду `pdsprj-mcp`.

Smoke-тест:

```bash
uv run pytest              # тесты парсера
uv run pdsprj-mcp          # запустит MCP-сервер на stdio (Ctrl-C для выхода)
```

## Подключение к Claude

Сервер общается по MCP через stdio. Укажите клиенту путь к
`pdsprj-mcp` внутри `.venv/` проекта.

### Claude Desktop

Отредактируйте `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) или соответствующий файл в вашей ОС:

```json
{
  "mcpServers": {
    "pdsprj": {
      "command": "/абсолютный/путь/к/pdsprj-mcp/.venv/bin/pdsprj-mcp"
    }
  }
}
```

Перезапустите Claude Desktop. Пять инструментов появятся под
сервером `pdsprj`.

### Claude Code

```bash
claude mcp add pdsprj /абсолютный/путь/к/pdsprj-mcp/.venv/bin/pdsprj-mcp
```

### Любой другой MCP-клиент

Запустите бинарник — он говорит по MCP в stdin/stdout:

```bash
/абсолютный/путь/к/pdsprj-mcp/.venv/bin/pdsprj-mcp
```

## Типовой workflow

1. **Вы** открываете Proteus, ставите AT89C51 + нужные периферийные
   компоненты (кнопки, светодиоды, резисторы, индикаторы), разводите
   провода от пинов MCU и сохраняете как `lab.pdsprj`.
2. **Вы** пишете Claude'у:
   > «Вот `~/work/lab.pdsprj`. Задание: зажигать LED на P1.0 пока
   > нажата кнопка на P3.2. Объясни что подключено и напиши ASM».
3. **Claude** вызывает `explain_schematic` чтобы привязать ответ к
   реально разведённым пинам, читает существующий шаблон через
   `get_asm`, пишет программу, кладёт её в архив через `set_firmware`.
4. **Вы** открываете результат в Proteus, делаете *Build → Compile*
   (или подгружаете HEX напрямую) и запускаете симуляцию.

## Что внутри `.pdsprj`

`.pdsprj` — это ZIP-архив:

```
ROOT.DSN          бинарная схема (закрытый формат, частично разобран)
ROOT.CDB          netlist + маппинг pin name/number (закрытый формат)
PROJECT.XML       метаданные проекта
FIRMWARE/<MCU>/
  main.asm        обычный текст — то, что пишет set_firmware
  Debug.HEX       Intel HEX — то, что пишет set_firmware
```

Сервер читает `ROOT.DSN` + `ROOT.CDB` для информации о компонентах
и проводах, и редактирует `FIRMWARE/...` на месте. `ROOT.DSN` он не
переписывает.

## Структура проекта

```
src/pdsprj_mcp/
  parser.py       — парсер бинарного DSN (Pydantic-модели)
  explain.py      — markdown-сводка схемы
  asm_tools.py    — чтение/запись ASM/HEX внутри ZIP
  mcp_server.py   — FastMCP-обёртка + console entry-point
tests/
  test_parser.py  — тесты парсера на приватном датасете
docs/
  format.md       — справка по формату .pdsprj
  limitations.md  — границы применимости и нерешённые места
```

## Документация

- [`docs/format.md`](docs/format.md) — справка по бинарному формату
  `.pdsprj` (что парсер читает из `ROOT.DSN` и `ROOT.CDB`).
- [`docs/limitations.md`](docs/limitations.md) — границы применимости
  и почему сервер не генерирует схемы с нуля.

## Разработка

```bash
uv sync                  # установить runtime + dev зависимости
uv run pytest            # тесты
uv run pdsprj-mcp        # запустить сервер (Ctrl-C для выхода)
```

Тесты парсера требуют датасет, которого нет в репозитории
(студенческие работы). Если `samples/` пуст — тесты пропускаются.

## Лицензия

MIT — см. [`LICENSE`](LICENSE) и [`NOTICE`](NOTICE).
