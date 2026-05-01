# pdsprj-mcp

An MCP (Model Context Protocol) server that lets Claude read and modify
Proteus 8 schematic projects (`.pdsprj` files). Designed for the
microcontroller-assignments workflow: you draw the schematic in Proteus,
hand the file to Claude, and Claude explains the circuit, writes the
8051 / AT89C51 assembly code, and writes the firmware back into the
project archive — all without leaving the conversation.

> **Not a schematic generator.** Proteus's binary format is closed and
> components contain absolute pointers into the library section that
> can't be safely synthesised from outside the IDE. This tool inspects
> and modifies user-drawn schematics; it doesn't create new ones.

> **Not affiliated with Labcenter Electronics Ltd.** "Proteus" is their
> trademark. See [`NOTICE`](NOTICE) for full disclaimer.

## What it does

The server exposes five MCP tools:

| Tool | Purpose |
|---|---|
| `parse_schematic(path)` | Structured JSON of components, pins, wires, properties |
| `explain_schematic(path)` | Markdown summary, grouped by device type |
| `get_asm(path)` | Read `FIRMWARE/<MCU>/main.asm` |
| `get_hex(path)` | Read `Debug.HEX` (Intel HEX) |
| `set_firmware(path, asm_code, output_path?, hex_code?)` | Write new ASM / HEX into the archive |

Other files in the `.pdsprj` ZIP are passed through unchanged.

## Requirements

- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- A licensed copy of Proteus Design Suite (to draw schematics and
  verify outputs). The server itself never invokes Proteus.

## Install

```bash
git clone https://github.com/danspiridonov/pdsprj-mcp.git
cd pdsprj-mcp
uv sync
```

This creates a virtual env in `.venv/` and installs the `pdsprj-mcp`
console script.

Smoke-test:

```bash
uv run pytest              # parser tests
uv run pdsprj-mcp          # launches MCP server on stdio (Ctrl-C to stop)
```

## Connect to a Claude client

The server speaks MCP over stdio. Point your Claude client at the
`pdsprj-mcp` script inside the project's virtualenv.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent on your platform:

```json
{
  "mcpServers": {
    "pdsprj": {
      "command": "/absolute/path/to/pdsprj-mcp/.venv/bin/pdsprj-mcp"
    }
  }
}
```

Restart Claude Desktop. The five tools listed above appear under the
`pdsprj` server.

### Claude Code

```bash
claude mcp add pdsprj /absolute/path/to/pdsprj-mcp/.venv/bin/pdsprj-mcp
```

### Any other MCP client

Run the binary; it speaks MCP on stdin/stdout:

```bash
/absolute/path/to/pdsprj-mcp/.venv/bin/pdsprj-mcp
```

## Typical workflow

1. **You** open Proteus, place an AT89C51 plus whatever passive
   components and peripherals the assignment requires, wire each MCU
   pin to its destination, and save as `lab.pdsprj`.
2. **You** ask Claude something like:
   > "Here's `~/work/lab.pdsprj`. The task is to light an LED on P1.0
   > whenever the button on P3.2 is pressed. Explain what's wired up
   > and write the ASM."
3. **Claude** calls `explain_schematic` to ground the discussion in
   the actual pins you wired, calls `get_asm` to see any boilerplate
   already in the project, writes the program, and calls
   `set_firmware` to drop it into the archive.
4. **You** open the result in Proteus, hit *Build → Compile* (or load
   the HEX directly), and run the simulation.

## What's inside a `.pdsprj`

A `.pdsprj` is a ZIP containing:

```
ROOT.DSN          binary schematic (proprietary, partially reversed)
ROOT.CDB          netlist + pin name/number map (proprietary)
PROJECT.XML       project metadata
FIRMWARE/<MCU>/
  main.asm        plain text — what set_firmware writes
  Debug.HEX       Intel HEX — what set_firmware writes
```

The server reads `ROOT.DSN` + `ROOT.CDB` for component/wire info and
edits `FIRMWARE/...` in place. It never rewrites `ROOT.DSN`.

## Project layout

```
src/pdsprj_mcp/
  parser.py       — DSN binary parser (Pydantic models)
  explain.py      — Markdown summarizer
  asm_tools.py    — ASM/HEX read/write inside the ZIP
  mcp_server.py   — FastMCP wrapper + console entry-point
tests/
  test_parser.py  — exercises the parser on a private dataset
```

## Development

```bash
uv sync                  # install runtime + dev deps
uv run pytest            # run tests
uv run pdsprj-mcp        # launch the server (Ctrl-C to stop)
```

The parser tests need a dataset that isn't shipped with the repo (it's
student coursework). They are skipped if `samples/` is empty.

## License

MIT — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
