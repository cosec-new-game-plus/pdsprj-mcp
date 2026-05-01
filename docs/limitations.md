# Why this server doesn't generate schematics

A short design note on why `pdsprj-mcp` is read-and-modify rather
than read-and-write. If you are tempted to add a `generate_schematic`
tool, please read this first.

## Summary

Proteus's `.pdsprj` binary contains absolute byte-offset pointers
that bind a component to its surrounding file. Those pointers can't
be re-targeted from outside the IDE without reverse-engineering the
library section in full, and getting them wrong does not produce a
"slightly broken" schematic — it produces an immediate access
violation in `ISIS.DLL`.

We tried. It doesn't work in any reasonable amount of effort. The
write surface in this server is therefore limited to firmware files
(`main.asm`, `Debug.HEX`), where modification is trivial and safe.

## What we tried

The natural strategy is **splice**: take an existing `.pdsprj` as a
chassis, parse a "template" of each device (its byte block from a
reference file), and append new components to the CIRCUIT section.
Wires can be appended the same way — they have no library pointers
at all.

Three increasingly painful failure modes appeared:

### 1. Cross-chassis splice — missing library symbols

Component records contain absolute u32 offsets into the source
file's library section, pointing at internal symbols like
`$MKRORIGIN` and `$MKRLABEL`. Cloning a component from chassis A
into chassis B leaves those pointers aimed at offsets that hold
unrelated bytes in B. Result:

```
Schematic Capture: Symbol "$MKRORIGIN" used but not found in libraries.
Schematic Capture: Symbol "$MKRLABEL" used but not found in libraries.
Fatal Error: Internal Exception: access violation in module 'ISIS.DLL'.
```

Mitigation: extract the template from the same chassis you intend to
splice into. This makes the library pointers valid.

### 2. Same-chassis splice — duplicate net ownership

Inside the CIRCUIT section, components also contain absolute u32
pointers to **other components' pin blocks**. These represent net
connections: when component A's pin is wired to component B's pin,
A's record holds a pointer into B.

A cloned component carries the source's outgoing pointers verbatim.
The clone now claims it shares pins with the same neighbours as the
original. Proteus opens the file and crashes:

```
Fatal Error: SEH trap E06D7363 in module 'KERNELBASE.DLL'.
```

Mitigation: in our parser we identify these as "external pointers"
and zero them out at clone time, producing an unconnected component.
That works — until you want connections, which is most of the time.

### 3. Self-pointers and pin blocks

Multi-pin components (e.g. resistors with two pins) embed their pin
blocks inside their own record and reference them by absolute offset.
Cloning the bytes leaves these self-pointers aimed at the source
component's pin blocks, again causing duplicate ownership.

Mitigation: rewrite each self-pointer at clone time as
`new_offset + (target - source_offset)`. This actually works, but
only for the self-reference class.

## What's missing for a real solution

To synthesise a schematic from scratch you need at minimum:

1. **A parser for `ISIS SCHEMATIC FILE`** (the library section, ~95
   % of the file). It contains symbol shapes, font tables, and the
   pin-coordinate-and-orientation tables the renderer uses.
2. **A way to compose a library section** for a target chassis from
   the union of device-type definitions you want to support.
   Library headers contain checksums or counters that change with
   composition; their algorithm is not public.
3. **Net assignment** — pointer-to-pin-block topology has to be
   regenerated from a logical netlist, not copied from a sample.
4. **An end-to-end test loop**: every "looks plausible" output has
   to round-trip through Proteus to be trusted, because failure mode
   #2 above is silent at parse time.

Items (1) and (2) are the bulk of the work. Without a public
specification or an open-source reference implementation, this is
weeks-to-months of byte-level reverse engineering.

## What we do instead

The user draws the schematic in Proteus once. From that point on
the file's binary state is canonical and untouched: the server
reads it (parser, explainer) and edits only the firmware files,
which are plain ASCII inside the same ZIP. This is enough for the
core use case — generating 8051 assembly grounded in the actual pins
the user wired up — and avoids all three failure modes by construction.

If the project ever needs schematic mutation, the safest next steps
are (in order of difficulty):

- Append new **wires** between coordinates the user has already
  exposed — wires have no library pointers and only need
  back-reference patching of nearby u32 offsets in the CIRCUIT
  section.
- Append **terminals** (`$TERPOWER`, `$TERGROUND`) — single-symbol
  objects with relatively few outgoing pointers; same caveats as
  wires.
- Anything that introduces a new device-type is gated on the
  library-section reverse engineering above.
