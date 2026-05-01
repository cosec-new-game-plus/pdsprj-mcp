# Proteus 8 `.pdsprj` — format reference

A condensed, public-facing reverse-engineering reference for the
parts of the format this server reads. Verified against schematics
created with Proteus 8.10 (`RELEASE=810`, `FILEVER=826`, MCU
AT89C51). All findings come from analysing files produced by a
licensed copy of Proteus Design Suite — no Labcenter source,
binary, or library was decompiled. See [`NOTICE`](../NOTICE).

> **Scope.** This document covers what the parser uses. The
> library section and several flag fields remain undecoded; see
> [Limitations](#9-limitations).

---

## 1. Container

`.pdsprj` is a plain ZIP, no compression or password.

```
project.pdsprj
├── PROJECT.XML                 metadata (release, file ver, timestamps)
├── FIRMWARE.XML                MCU family + compiler
├── ROOT.DSN                    binary schematic — main payload
├── ROOT.CDB                    Connectivity DataBase — netlist
├── SCRIPTS/PWRRAILS.DAT        text "*RAILS\n*BINDINGS\n"
└── FIRMWARE/<MCU>/
    ├── main.asm                assembly source
    └── Debug/
        ├── Debug.HEX           Intel HEX firmware
        └── Debug.SDI           debug-symbol info (CSV)
```

### `PROJECT.XML`

```xml
<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<PROJECT>
  <TIMESTAMP RELEASE="810" FILEVER="826"
             MODIFIED="..." CUSTID="..." CREATED="..."/>
  <VARIANTS/>
  <SCRIPTS><STRING>PWRRAILS.DAT</STRING></SCRIPTS>
</PROJECT>
```

- `RELEASE=810` → Proteus 8.10
- `FILEVER=826` → internal format revision
- `CUSTID` → user license identifier
- `CREATED`/`MODIFIED` → UNIX timestamps

### `FIRMWARE.XML`

```xml
<FIRMWARE FAMILY="8051" COMPILER="ASEM-51 (Proteus)" TYPE="AT89C51">
  <PROJECTS><STRING>AT89C51</STRING></PROJECTS>
</FIRMWARE>
```

---

## 2. `ROOT.DSN` — binary schematic

### 2.1 Top-level layout

Three logical sections, all keyed by the ASCII tags `ISIS SCHEMATIC FILE`
and `ISIS CIRCUIT FILE`:

```
┌────────────────────────────────────────┐
│ ISIS SCHEMATIC FILE …  (offset 0)      │  library — device definitions,
│                                        │  fonts, symbol shapes
├────────────────────────────────────────┤
│ ISIS CIRCUIT FILE …    (offset N₁)     │  schematic — components, wires
├────────────────────────────────────────┤
│ ISIS CIRCUIT FILE …    (offset N₂)     │  SPICE simulation parameters
└────────────────────────────────────────┘
```

The two `ISIS CIRCUIT FILE` headers are located by repeated
`bytes.find`. Section 1 holds the schematic, section 2 holds SPICE
settings (always at end of file).

### 2.2 CIRCUIT-section header (48 bytes)

```
+0x00   "ISIS CIRCUIT FILE"
+0x11   1A                      EOF-like marker
+0x12   <22 bytes flags / bounds>  (stable across files of same project family)
+0x28   "OBJECT DATA\0\0\0"
+0x30   <first object>
```

### 2.3 Component record

```
Component :=
  ff [len:u8] [refdes:ascii × len]      e.g. "U1", "R12". Empty (ff 00) for
                                        virtual primitives like LED, BUTTON.
  [x:i32 LE] [y:i32 LE]                 component position
  [24 bytes header flags]               orient/mirror/layer (see §2.3.1)
  {Label}*                              displayed text fields (§2.3.2)
  {{KEY=VALUE}\n}*                      property block
  [\n × ≥1]                             separator
  [optional pin block]                  [tag:u16] [len:u8] DEVICE_NAME (§2.3.3)
```

#### 2.3.1 24-byte header

```
6 bytes  : orientation / mirror / layer (not fully decoded)
4 bytes  : i32 tag — observed 0x32=50 for MCU instances
4 bytes  : i32 — bbox width
4 bytes  : i32 — bbox height
4 bytes  : i32 = 0
2 bytes  : 0x20 0x01 — stable end-of-header marker
```

#### 2.3.2 Label

```
Label :=
  ff [flag:u8 ∈ {00, 01, 02}]           01=visible, 00=hidden, 02=inverse
  "Default Font\0"                      C-string: font name
  "<FIELD_KIND>\0"                      C-string: role of this label
  [≈4 bytes padding]
  ff [len:u8] [value:ascii × len]       displayed text
  [x:i32 LE] [y:i32 LE]                 label position
  [16 bytes style]                      colour / size / rotation (not decoded)
```

Observed `FIELD_KIND` values:

| Kind | Purpose | Example value |
|---|---|---|
| `COMPONENT ID` | refdes display | `U1`, `R5` |
| `COMPONENT VALUE` | device-type display | `AT89C51`, `LED-BLUE`, `BUTTON` |
| `SUBCKT NAME` | sub-circuit name | usually empty |
| `PROPERTIES` | anchor for `{KEY=VAL}` block | no value |
| `TERMINAL LABEL` | terminal caption | `VCC`, `GND` |
| `WIRE LABEL` | net name on wire | rare (named nets only) |

#### 2.3.3 Pin block

After properties may come `[tag:u16 LE] [len:u8] DEVICE_NAME`:

| `tag` | Pins | Components seen |
|---:|---:|---|
| `0x0028` | 40 | `AT89C51` (DIL40) |
| `0x000c` | 12 | `7SEG-MPX4-CA/CC` |
| `0x0008` | 8 | `7SEG-COM-CATHODE` |
| `0x0004` | 4 | `$IOSCILLOSCOPE` |
| `0x0002` | 2 | `LED`, `BUTTON`, `RES` |

The `tag` value matches the package pin count for every observed
component, but the pin block is sometimes absent — pin geometry then
lives in the library section.

### 2.4 Terminals (`$TERPOWER`, `$TERGROUND`, …)

A separate object type used for VCC / GND / NC pins on the schematic.
Not a component, not a wire.

```
Terminal :=
  10                            object type tag (1 byte)
  [x:i32 LE] [y:i32 LE]         position
  [4 bytes flags]               orient / type (not decoded)
  [kind_len:u8] [kind:ascii]    e.g. "$TERPOWER", "$TERGROUND",
                                "$TERBIDIR", "$TERNC"
  [≈100 bytes]                  TERMINAL LABEL + net name + padding
```

Block size for VCC / GND: 124–125 bytes.

### 2.5 Wire

```
Wire :=
  02 7f                         tag 0x7f02 = WIRE
  "WIRE\0"                      C-string type name
  [pad:u16 = 0]
  [count:u16 LE]                vertices in the polyline (2..8 typical)
  (x:i32 LE, y:i32 LE) × count  vertices
```

Wires are polylines — at minimum two vertices. Most are orthogonal
because Proteus auto-routes 90° corners.

### 2.6 Coordinate system

Units are **0.0001 inch** (ten-thousandths of an inch).

- `x = 0x131190 = 1 249 680` → 124.968″
- Default snap grid: `0x10000 = 65 536` units (6.5536″)
- Negative coordinates extend left / above the origin
- Observed range across the dataset: ±20 M units

### 2.7 Wire ↔ pin connectivity

**Not stored explicitly.** Per ISIS docs ("Wiring Up"): *"If you place
a component pin end directly against a wire it will be considered
connected to the wire and a junction dot will be created."*

Connectivity is computed geometrically:

1. Each pin's absolute coordinate = `component_xy + pin_offset` (the
   offset comes from the library section, rotated by the component's
   orientation flags).
2. If a pin coordinate coincides with a wire vertex → connected.
3. If multiple wires meet at a point → junction (WIRE DOT).

---

## 3. `ROOT.CDB` — Connectivity DataBase

A length-prefixed text-style database. Primitive: `[len:u8] [bytes×len]`
ASCII strings.

### 3.1 Structure

```
┌────────────────────────────────────┐
│ Header (0..0x40)                   │  version + "Master Sheet"
├────────────────────────────────────┤
│ Pin definitions for the MCU        │  always at offset 0x77 in our data
├────────────────────────────────────┤
│ Per-component connectivity         │  (pin name, pin number) pairs
│                                    │  for every component with pins
├────────────────────────────────────┤
│ Component instances                │  refdes + value + device + package
│                                    │  + {PROPERTIES}
└────────────────────────────────────┘
```

### 3.2 Pin definitions

```
Block :=
  [pkg_id:u32 LE]
  [unknown:u32]                 often = 1
  [pin_count:u32 LE]
  [zeros: variable]
  (
    [name_len:u8] [pin_name:ascii]      e.g. "P0.0/AD0", "VCC"
    [num_len:u8]  [pin_number:ascii]    e.g. "39", "40"
  ) × pin_count
```

`AT89C51` pin definitions live at offset `0x77` in every observed file.

### 3.3 Per-component connectivity

For every component that has pins (packaged or virtual):

```
Entry :=
  [id:u32]                      unique component id (1..N, monotone)
  [unknown:u32 = 2]
  [zero:u32 = 0]
  [id_repeat:u32]
  [refdes_len:u8] [refdes]
  [pin_count:u32]
  (
    [name_len:u8] [pin_name] [00]       null-terminated pin name
  ) × pin_count
  [zero:u32 = 0]
  [zero:u8 = 0]
```

Entries are separated by `ff ff ff ff` (u32). After the last entry
comes a 48-byte tail (not fully decoded) with the largest component
id and a slot array of `01` bytes.

### 3.4 Component instances

The CDB tail. One record per **packaged** component (anything with a
PCB footprint):

```
Instance :=
  [len]refdes                   "U1", "R2"
  [len]value                    "AT89C51", "333", "10k" — display value
  [len]device                   "AT89C51", "RES"
  [len]package                  "DIL40", "RES40A"
  [i32]                         size of the property block
  {KEY=VALUE}\n × N             properties
```

Example for an AT89C51 instance:

```
"U1" "AT89C51" "AT89C51" "DIL40" <size>
{PRIMITIVE=DIGITAL}
{PACKAGE=DIL40}
{MODDLL=MCS8051.DLL}
{CLOCK=12MHz}
{CODEGEN=ASEM51}
{X2=0} {HWDOG=0} {ROM=4096} {IRAM=256} {XRAM=0} {EEPROM=0}
{ITFMOD=AT89}
{PROGRAM=…\Debug.HEX}
```

Virtual primitives (LED, BUTTON, `$IOSCILLOSCOPE`) appear in the
per-component connectivity block (§3.3) but not in the instances tail
(§3.4) — they have no PCB footprint.

---

## 4. Parsing heuristics

### 4.1 Components — anchor-based

Every component has a `COMPONENT ID` label. Use it as the anchor and
walk back to the start of the record:

```
ANCHOR = ff [01|02|00] "Default Font\0COMPONENT ID\0"

For each match at offset L:
    # Layout: ff [len] [refdes] [x:4] [y:4] [24 bytes flags] [COMPONENT ID label]
    # L - obj_start = 2 + len(refdes) + 8 + 24
    For rlen in 0..8:
        obj_start = L - (34 + rlen)
        if data[obj_start] == 0xff and data[obj_start+1] == rlen:
            x, y = unpack("<ii", data[obj_start+2+rlen : obj_start+10+rlen])
            if -20M <= x,y <= +20M:
                yield (obj_start, refdes, x, y); break
```

This catches every component including unnamed virtual primitives
(`refdes=""`).

### 4.2 Wires

```
ANCHOR = "\x02\x7fWIRE\x00"

For each match at offset W:
    pad   = u16(data[W+7 : W+9])     always 0
    count = u16(data[W+9 : W+11])    2..8 typical
    vertices = [
        (i32 at W+11+8k, i32 at W+15+8k)
        for k in range(count)
    ]
```

### 4.3 CDB instances — 4-string sequence + `{` lookahead

```
For each candidate offset:
    refdes  = read_lp_str(data, off)
    value   = read_lp_str(...)
    device  = read_lp_str(...)        match ^[A-Z$][A-Z0-9\-_]+$
    package = read_lp_str(...)
    if `{` not in data[pos : pos+16]:
        reject
    parse_props(...)
```

---

## 5. Validation

The parser was validated against a 9-file corpus. Every component
count and wire count matches the description of the corresponding
assignment:

| File | Description | Components | Wires |
|---|---|---:|---:|
| task-2-1 | 16-bit timer → LED at 1 Hz | 2 | 2 |
| task-2-2 | 13-bit timer → LED at 1 Hz | 2 | 2 |
| task-2-3 | button counter → LED | 3 | 6 |
| task-2-4 | 4 frequencies on 2 pins | 4 | 7 |
| sem-3-1 | 7-segment + 10 buttons | 12 | 38 |
| sem-3-2 | 7-segment counter | 3 | 11 |
| sem-3-3 | 7-segment + LED | 3 | 11 |
| sem-04 | 4×7-segment + resistors | 16 | 29 |
| sem-4-1 | 4×7-segment counter | 3 | 14 |

---

## 6. Coordinate notes for tooling

Embedded coordinates appear in many places inside a component record:

- The component's own `(x, y)` (§2.3)
- Each label's own `(x, y)` (§2.3.2)
- Sometimes absolute pin coordinates inside the pin block

Anything that wants to **move** a component on the canvas needs to
update *all* of these — Proteus refuses to render a component if its
labels remain at stale absolute coordinates. This server doesn't do
that (it only reads the schematic and writes firmware), but it's
worth knowing if you intend to extend the codebase.

---

## 7. Limitations

What is **not** decoded and would require further work:

1. **Library section (`ISIS SCHEMATIC FILE`)** — device-type
   definitions, font tables, symbol shapes. Required to compute
   absolute pin coordinates from component position + orientation.
2. **24-byte component header flags** — orientation / mirror / layer
   bits aren't fully mapped.
3. **Label style (16 bytes)** — colour, font size, rotation.
4. **Terminal-only objects** (VCC / GND placed standalone) — anchor
   pattern not yet derived.
5. **WIRE DOT junctions** — visible in Proteus but not parsed
   separately (they are inferred from wire vertex coincidence).
6. **Encrypted libraries (`.LML`, `.PWL`)** — out of scope.
7. **Internal pointers in the component record** — every component
   record contains absolute u32 offsets into the library section
   (e.g. `$MKRORIGIN`, `$MKRLABEL`) and absolute pointers to pin
   blocks elsewhere in CIRCUIT (representing net connections).
   These are why this server **does not generate or clone components**
   — copying the bytes verbatim leaves the pointers referring to the
   original chassis's library, which crashes Proteus when the file is
   opened. See [`limitations.md`](limitations.md) for the full
   discussion.

---

## 8. External references

- ISIS / Proteus official semantics (not the binary format):
  - <http://systembus.com/proteus/ISIS/GENERAL_CONCEPTS/Wiring_Up.htm>
  - <http://systembus.com/proteus/ISIS/OBJECT_SPECIFICS/Components.htm>
- Labcenter's text netlist format **SDF** (`"ISIS SCHEMATIC
  DESCRIPTION FORMAT 3.0"`) is exported via *Tools → Netlist
  Compiler* — it is not embedded in `.pdsprj`.
- No public open-source parser of `ROOT.DSN` / `.pdsprj` is known
  (as of early 2026).

---

## 9. Applicability

Findings here come from files produced by Proteus 8.10 with VSM
Studio + ASEM-51 toolchain, schematics of 2–16 components and 2–38
wires, MCU AT89C51.

The format is expected to be stable across the Proteus 8.x major
line. **Proteus 7 used a different container** (an OLE2 compound
document) and is not covered.
