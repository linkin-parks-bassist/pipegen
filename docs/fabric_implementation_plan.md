# Pigen fabric implementation plan

This plan deliberately starts with a small, deterministic fabric that is easy
to inspect and prove correct. Optimization and richer port syntax come after
the carrier path, ready/valid behavior, and generated RTL are trustworthy.

## V0 contract — locked for implementation

V0 is deliberately a backend milestone, not an attempt to invent all of
Pigen's eventual extended-SystemVerilog surface at once. Its source is a
whitespace-tolerant `.fabric` connectivity declaration; its generated boundary
is explicit ready/valid/payload signals (plus an opaque delivered-path sideband
on routed receives). The following decisions are fixed for this milestone:

- fixed-width packets are exactly `{path, payload}`; `PATH_W` is compiler-owned;
- a three-port router takes the current path LSB, selects its cyclic exit, and
  rotates the whole path right on a successful forward transfer;
- direct `>` links are exclusive, skid-buffered, payload-only connections;
- soft-arrow lengths are preserved as topology intent, while the first backend
  uses a deterministic balanced tree rather than claiming optimisation it does
  not yet perform;
- every endpoint and router ingress has a two-entry elastic FIFO, sustaining
  one packet per cycle without inter-router combinational ready loops;
  transport remains best-effort under arbitrary traffic;
- source recognition is generated from reversible delivered path signatures;
- no user-facing `packet` declarations, metadata/QoS, replies, deep FIFOs, or
  topology optimisation are silently included in V0.

V0 is accepted only when `python3 -m unittest -v`, `make -C examples fabric`,
and `make -C examples fabric-verilator` pass. The checked example must cover a
direct link, multiple routed sources into one input, source-path recognition,
and downstream backpressure.

## Completed — routing-network diagram engine

When compiling a fabric, also emit an SVG diagram of the generated routing
network. (`.svg` is the standard extension for the requested scalable-vector
diagram.) The diagram is a compiler artifact, just like the route manifest: it
must reflect the *elaborated* topology rather than merely redraw the source
connections.

- The default artifact is written beside the RTL as `OUTPUT.svg`; `--diagram
  PATH` selects a different path and `--no-diagram` suppresses it.
- Show source-handle and destination-input ports on shared unit nodes, generated
  routers, direct links, and routed physical links. Label routed edges with
  stable router port numbers; label endpoint attachments and connections with
  their declared names and route bits where that remains legible.
- Use the same deterministic ordering and topology IR as RTL and manifest
  generation, so unchanged input produces byte-identical SVG. Direct links
  must be visibly distinct from routed links.
- Use a deterministic spring layout seeded from the generated router tree:
  units begin around the perimeter, routers settle from the centre, link lengths
  are equalized, router incident edges are distributed angularly, direct links
  receive crossing protection, and padded footprint collisions prevent overlap.
  The physics has no artificial canvas walls; the final SVG is fitted to its
  settled bounds. Disconnected components are packed into separate padded slots.
- Add snapshot/structural tests that verify every emitted router, endpoint, and
  direct/routed connection has a corresponding diagram node or edge, without
  node overlaps or direct-link crossings.

This completed artifact makes route and placement decisions inspectable before
any topology optimisation work begins.

## Locked contract for the common declaration frontend

Pipelines and fabrics are peer, named design-unit declarations alongside
ordinary modules—not bodies implicitly inlined into an enclosing module. They
share the module-style parameter and explicit port-declaration frontend, and
each compiles to a separately instantiable SystemVerilog module. Any future
fabric inlining is an explicit, opt-in locality transformation after this model
is established; it is not part of the base language or this roadmap.

A fabric declaration must explicitly declare **exactly one** clock port and
**exactly one** reset port. Missing, duplicated, inferred, or name-heuristic
clock/reset bindings are errors. The port declarations establish polarity and
type in the same way as ordinary module declarations; the fabric compiler does
not guess from names or inherit signals from a parent. The generated module
uses those declared ports directly.

All fabric boundary ports are likewise declared, typed, and directional in the
common port grammar. Ready/valid transport remains deliberately ergonomic: a
dedicated registered/skidded ready/valid-port primitive declares the associated
payload and valid/ready behavior, including named output handles and recognized
input sources. It is a port primitive, not an exception that leaves an implicit
boundary signal.

The common port IR must leave room for future interface primitives—first-class
AXI and other protocol/interface declarations—without making routers inspect
their payloads. Such primitives will lower to declared signals/adapters and
may carry protocol-specific validation; they are a later frontend capability,
not an inferred `inout` convention. Each physical routed link receives the
maximum payload width of the selected connections crossing that link; router
ports are therefore independently sized and perform only LSB-aligned
zero-extension or MSB truncation. These rules are specified now so the frontend
can be added without changing routing semantics; they are not claimed as part
of the current single-`PAYLOAD_W` V0 emitter.

## 1. Keep Pigen as one frontend

`pigen.py` remains the entry point. Its first dispatch is by top-level source
form:

```text
pipeline NAME ... begin      -> pipeline lowering
fabric NAME ... begin        -> fabric lowering
```

The fabric parser follows the Pigen style: indentation is cosmetic,
blank lines are free, declarations look like ordinary SystemVerilog, and blocks
end explicitly with `endfabric`.

The initial command shape is:

```sh
./pigen.py fabric control.fabric -o control.sv
```

Pipeline invocation may later become an explicit `pipeline` subcommand too, but
that CLI cleanup is not needed to begin fabric work.

### V0 grammar and binding contract

The first parser accepts the following endpoint forms:

```text
source      := instance '.' output_port '.' destination_handle
destination := instance '.' input_port [ '.' recognized_source ]
connection  := source arrow destination ';'
arrow       := '>' | '-'* '>'       // at least one '-' for a soft tier
```

Each source handle binds exactly once. A destination input may accept many soft
connections; its optional final component names one of the input's declared
recognized sources. A hard direct `>` connection makes both its source handle
and destination input exclusive. The initial parser also accepts `option`
settings and `parameter integer PAYLOAD_W`; `PATH_W` is never source text.

## 2. Define a small fabric IR

Add plain Python data structures for:

- `Fabric`: name, payload width expression, options, and connections;
- `Endpoint`: a named module input or output destination handle and its required
  send/receive directions;
- `Connection`: source handle, destination input, arrow tier, and direct-link
  flag;
- `SourceBinding`: an optional destination-local recognized source name for a
  connection;
- `Topology`: routers, cyclicly numbered ports, links, and direct links;
- `Route`: source endpoint, destination endpoint, and LSB-first direction bits.

The parser must recognize `>`, `->`, `-->`, and longer arrows without making
whitespace significant. It rejects an unbound or multiply bound output handle,
a direct-link handle or destination input that appears in any other connection,
mismatched payload widths, malformed endpoint names, and unknown options.

## 3. Build one correct baseline topology

The first topology backend is a deterministic balanced binary routing tree for
all non-direct endpoints. Internal vertices are three-port interconnects:
parent plus two children. The root's unused third port is tied off. Destination
handle and input names are sorted stably before leaf assignment, making topology
generation repeatable.

This is not the final optimizer. It is a useful baseline because every endpoint
pair has one simple route, router degree never exceeds three, and maximum route
length is bounded by twice the tree depth. Hard `>` connections bypass the tree
entirely as dedicated links. Soft arrow tiers are retained in the IR and route
manifest but have no placement guarantee in this first backend.

For each tree path, the compiler uses the fixed cyclic port numbering at each
router to encode the required relative turn bit. `PATH_W` is the maximum route
length among non-direct connections. It is emitted in RTL but absent from user
source. Each router rotates the path rather than shifting it, preserving all
route bits.

For routes that target an input with recognized sources, reverse-trace the
delivered rotated word through the generated topology. The first non-router
attachment reached is the unique sender, so the route's delivered word is
already a unique source signature at that input. No tag allocation and no
extra `PATH_W` bits are required.

An endpoint in this tree is either one source handle or one destination input.
Source adapters prepend their fixed zero-padded initial route; destination
adapters remove the path before presenting the payload, while retaining the
delivered path internally for source comparisons and future replies. A fabric
with only hard direct links emits no tree, router, or path field.

## 4. Implement the transport primitives first

Write and test two reusable SystemVerilog templates:

1. **Endpoint adapter / skid buffer.** Every unit-facing transport port has at
   least one elastic entry. A send-side `out.handle.ready` means that entry can
   accept a new packet; an accepted `out.handle <= data` keeps the emitted
   payload and valid stable until the external transfer completes.
2. **Three-port router.** Parameterized only by fixed `PACKET_W`; each port has
   an input elastic entry. The router examines `path[0]`, selects clockwise or
   counter-clockwise, rotates the packet path on forwarding, and performs
   two-way round-robin arbitration per output.

The router has no endpoint IDs, destination table, packet-type awareness, or
route-length state. The first implementation uses one registered forwarding
stage per router to prevent combinational ready loops. It may expose optional
debug counters, but no required runtime metadata.

V0 fixes both router and endpoint depth at two elastic entries. This permits
simultaneous dequeue/enqueue at full throughput without a ready signal crossing
the registered boundary. The parsed depth options are validated as at least one
and may be emitted as parameters, but arbitrary-depth FIFO generation is
deferred until the two-entry semantics are fully tested.

## 5. Emit a complete fabric module

The fabric backend emits:

- a `NAME__router` primitive and endpoint skid adapter;
- one instance per generated router;
- direct `>` links using only endpoint adapters;
- non-direct source-handle adapters with source route constants;
- the top-level fabric module with only the needed input/output/inout channel
  directions;
- a text route manifest containing endpoint names, router links, cyclic port
  numbers, route bits, hop counts, and delivered source signatures.

Initially the generated fabric ports may be explicit ready/valid signals. The
pleasant `packet` port declarations, destination handles, and procedural
`out.handle <= data` lowering are the next frontend step, built on the
already-tested adapters rather than mixed into the first fabric emitter.

## 6. Verify before optimizing

Tests are required at four levels:

1. Parser tests for arrow tiers, whitespace freedom, direct-link exclusivity,
   and deterministic errors.
2. Pure-Python topology tests that exhaustively trace every generated route to
   its requested endpoint, verify `PATH_W` is minimal for routing, and
   reverse-trace every delivered route to its original source. They also verify
   canonical zero padding and unique delivered path words per destination input.
3. Router RTL simulations covering every ingress/turn, both-contention cases,
   round-robin fairness, backpressure, and path rotations. A pure-Python test
   verifies the return-path transform; emitting a user-facing reply adapter is
   deliberately a later feature.
4. End-to-end Icarus and Verilator tests for a fabric with direct and routed
   links under randomized `valid`/`ready` stalls, checked against a software
   queue model.

The implementation records two useful future-network rules now: a tree has
unique static routes but still has local contention on shared trunk links; and
an optional multipath topology must bound its route variants per connection.
At a destination, the compiler maps each permitted delivered rotating-path
signature to a source (and route variant), rejecting collisions. Multiple
variants may improve static load distribution but are opt-in because they can
reorder traffic; adaptive selection is a separate, non-V0 feature.

The generated route manifest is part of test output and the primary debugging
tool when a graph or path is surprising.

## 7. Make topology smarter, without changing router RTL

Once the tree backend is correct, add deterministic alternatives:

- leaf placement that groups short-arrow-tier endpoints;
- weighted and lexicographic arrow-tier objectives;
- placement-locality and router-count objectives;
- exact search for small fabrics and documented deterministic heuristics above
  that limit.

All of these change only graph and route generation. The packet format and
blind router primitive stay unchanged.

## 8. Add richer language features deliberately

After the fabric emitter is stable:

- implement the common module-style declaration/port frontend, including the
  exactly-one-clock/exactly-one-reset fabric validation;
- lower the dedicated registered/skidded ready/valid port primitive, including
  named output handles and recognized input sources;
- lower user-visible `.ready`, `.valid`, and payload accessors;
- allow optional opaque metadata, then optional router-visible QoS fields;
- add monitoring hooks and configuration for deeper endpoint FIFOs;
- add declared AXI and other interface primitives with protocol-specific
  adapters and validation;
- consider explicit fabric inlining only as a later, opt-in locality feature;
- consider an explicitly constrained liveness mode if best-effort transport is
  insufficient.

## First implementation milestone

The first meaningful deliverable is a `.fabric` file containing one hard direct
link and several `->` routes. Pigen should deterministically emit a balanced
tree fabric, a route manifest, and synthesizable SystemVerilog that passes
randomized ready/valid simulation. Its acceptance criteria are: direct links
contain no router instances; every routed connection reaches its intended input;
all generated routes are canonically zero-padded; source localparams match their
declared senders; the same input file produces byte-identical manifest and RTL;
and Icarus plus Verilator pass the generated testbench. No global optimizer,
metadata, dynamic addressing, reply syntax, or frontend magic is required for
that milestone.
