# Pigen fabric — blind source-routed fabric draft

## Purpose

A fabric connects named module ports from a declarative directed connectivity
list. The compiler deterministically chooses a graph of three-port
interconnects, computes one source route for every permitted connection, and
emits the SystemVerilog wiring and routing constants.

The fabric is not address-routed. An interconnect has no knowledge of endpoint
names, addresses, packet types, route lengths, or the rest of the graph. It
only forwards ready/valid packets according to the low bit of a tool-owned path
field.

## Port primitives

The front end will provide packet transport primitives. An output contains named
destination handles; those names are part of the module interface and are bound
by a fabric declaration, rather than being global module names:

```sv
input  packet #(T) rx;
output packet #(T) tx {
    normalized;
    delayed;
};
inout  packet #(T) link;
```

An input may also declare the source names it intends to recognize:

```sv
input packet #(T) command {
    source control;
    source debugger;
};
```

`input` receives packets, `output` sends packets, and `inout` supports both
directions. An `inout` port is lowered to independent transmit and receive
ready/valid channels; it is not a shared electrical SystemVerilog `inout` net.

Every unit-facing transport handle is inherently skidded. A handle's `.ready`
is the user-facing ability to enqueue one packet, while `<=` fills that skid
entry and causes the generated external `valid` to remain asserted until the
downstream ready/valid transfer completes. The source code therefore remains
natural:

```sv
if (tx.normalized.ready) begin
    tx.normalized <= data;
end
```

The endpoint FIFO breaks the unit-to-fabric combinational timing path and
absorbs short downstream stalls. V0 uses two entries, allowing a packet to
retire and its replacement to be accepted on the same clock without propagating
downstream `ready` back into the unit.

The physical protocol of a fabric attachment is the same whether its neighbour
is a module or another interconnect. The emitter only materializes the channel
directions actually required by the declared fabric connectivity.

## Clock, reset, and declared ports

A fabric is a named design unit at the same language level as a module or
pipeline; it is not implicitly inlined into an enclosing module. Its parameters
and ports use the common module-style declaration grammar, and it compiles to a
separately instantiable module.

A fabric must declare exactly one clock port and exactly one reset port.
Missing, ambiguous, duplicate, inherited, or conventionally guessed bindings
are errors. The declaration records the reset polarity and type, so generated
logic connects to the declared ports rather than inferring meaning from names.
All other boundary ports are explicit, typed, and directional too.

The ready/valid `packet` form described here remains a dedicated skidded-port
primitive within that common declaration grammar: it declares its payload,
direction, handles, and recognized sources rather than creating implicit ports.
Future declared interface primitives, such as AXI, will lower through their own
adapters and validation rules. They do not change the blind-router payload
semantics.

The current V0 emitter has one fabric-wide `PAYLOAD_W`. The planned width-aware
frontend improves that without making a router understand payload types: each
physical link has the smallest payload width that can carry every declared
connection whose selected route crosses it. Its packet is therefore
`{path, link_payload}`. A router has independently sized ports, stores each
ingress at that port's link width, and LSB-aligns as it forwards to the selected
egress port. This is safe because the compiler proves that each connection's
declared payload width fits every physical link on its route.

At any widening boundary, upper bits are zero-filled; at any narrowing
boundary, upper bits are discarded and low bits remain. This is deliberately a
bit transport rule: it does not consult SystemVerilog signedness. A later
source form may require an explicit `zero_extend`, `sign_extend`, or truncation
annotation where the default would obscure intent. Direct `>` links use the
same endpoint conversion rule and need no router.

## Fabric declaration

Fabric declarations intentionally resemble Pigen pipelines: parameters first, a
human-readable body, ordinary whitespace, and an explicit closing keyword.
Exact keywords remain open; this is the preferred shape:

```sv
fabric control #(
    parameter integer PAYLOAD_W = 64
) begin

    option objective = max_hops;
    option router_buffer_depth = 2;
    option endpoint_fifo_depth = 2;

    pre_processing.out.processed > processing.in;
    normalizer.out.envelope -> envelope.in;
    delay_buffer.out.mixer --> mixer.in;
    meter.out.telemetry ---> telemetry.in;

endfabric
```

Arrow length is both readable wiring intent and an input to topology generation:

| Connection | Meaning |
| --- | --- |
| `a.out.handle > b.in` | Hard direct link. No generated interconnect lies between the two ends, and neither the source handle nor destination input may appear in any other fabric connection. |
| `a.out.handle -> b.in` | First soft directness tier. The generator should make this route as short and uncongested as possible. |
| `a.out.handle --> b.in` | Second soft directness tier. |
| `a.out.handle ---> b.in` | Third soft directness tier; further hyphens add further tiers. |

The default optimizer treats these tiers lexicographically: it does not make a
shorter low-tier route at the expense of a worse high-tier route. Alternative
weighted objectives may be selected explicitly. Direct links are emitted as a
dedicated ready/valid connection with the standard endpoint skid adapters; they
consume the named source handle and destination input, and bypass the
interconnect graph entirely.

The first tree backend enforces `>` immediately. It parses, records, and reports
soft-arrow tiers, but does not yet promise that `->` is shorter than `-->`; that
lexicographic placement optimization is the next topology backend rather than
a hidden property of the baseline tree.

Destination choices are static in the hardware-description sense. A source may
choose between declared handles under ordinary control flow, for example “if
`x`, send through `tx.normalized`, otherwise through `tx.delayed`”; each handle
has a fabric-bound constant route. Runtime address lookup is explicitly outside
v0. A module stays reusable because handle names are local interface names; the
fabric chooses which instantiated module input each handle reaches.

Source bindings use the same destination-local naming idea:

```sv
fabric control begin
    control.tx.command -> worker.command.control;
    debugger.tx.command -> worker.command.debugger;
    monitor.tx.command -> worker.command;  // available to the default case
endfabric
```

## Routed packet

In V0, every routed fabric packet has one fixed, uniform layout:

```sv
{ path, payload }
```

`PACKET_W = PATH_W + PAYLOAD_W` throughout that fabric. `PATH_W` is fully
opaque: Pigen derives the minimum width required by its generated topology and
routes, and it never appears in user source or unit-facing port declarations.
The current LSB selects the next turn, then the whole word rotates right. No
path bit is discarded. Individual routes may use fewer bits than the generated
`PATH_W`; bits beyond the route are irrelevant because the packet is consumed
by the destination port, not interpreted by another router. Pigen sets every
such unused bit to zero, giving each route one canonical, deterministic word.
If a fabric contains only direct `>` links, it emits no routed packet links and
therefore no zero-width `PATH_W` signal; direct adapters carry payload only.

The width-aware frontend retains the same path field but gives each physical
link its own `LINK_PACKET_W = PATH_W + LINK_PAYLOAD_W`; the path is still fixed
width within the fabric and no router interprets payload bits.

There is deliberately no hop count, terminal marker, destination ID, route
table, or address decode in the packet or in an interconnect. The graph and the
source-selected path guarantee that the final link is the desired destination.

V0 has no other packet metadata. A later fabric may explicitly declare optional
opaque metadata fields, or opt into router-visible service fields such as QoS;
those fields remain fixed width within that fabric and are paid for in packet
width and, where inspected, arbitration timing.

## Three-port interconnect

Each interconnect has ports numbered `0`, `1`, and `2` in a fixed cyclic order.
A packet arriving at port `i` has exactly two candidate exits:

| `path[0]` | Exit |
| --- | --- |
| `0` | `(i + 1) mod 3` (clockwise) |
| `1` | `(i + 2) mod 3` (counter-clockwise) |

When the packet advances through the interconnect, the emitted packet has:

```sv
path_next = {path[0], path[PATH_W-1:1]};
```

The interconnect does not inspect the remaining bits or the payload. Rotation
preserves the complete source route while advancing the next decision into the
LSB position.

For each output, only the other two inputs can contend. Arbitration is therefore
two-way round robin, with the priority bit updated only after a successful
ready/valid transfer. A timing-friendly generated implementation uses a
two-entry elastic input FIFO per port and one registered routing stage; its
only state is those buffers and the two-way arbitration priority bits. Two
entries allow full throughput without propagating `ready` combinationally
through a chain of routers. It holds no routing or endpoint state.

## Route reversibility

Because forwarding rotates rather than shifts, a delivered packet still carries
its complete route trace. Together with the destination's physical ingress into
the generated graph, that trace uniquely retraces the path to the source.

More usefully, a destination can turn the received route into an ordinary
source-directed route without adding any special router behavior. With `~` for
bitwise inversion and `bit_reverse` reversing the bit order:

```sv
return_path = bit_reverse(~received_path);
```

The first LSB of `return_path` is the inverse of the final forward turn, and
each subsequent ordinary router rotation advances the next inverse turn. The
reply therefore traverses the same graph back to the original source. This is a
future endpoint-adapter feature; the three-port interconnect remains unaware of
sources, destinations, request/response roles, or return traffic.

## Source recognition

Source declarations are a compile-time feature built from the lossless rotated
path; they do not add an origin-ID field or require any special path-bit
allocation. At a fixed destination input, reverse traversal is deterministic:
the router uses the high path bit to undo the most recent turn, rotates in the
opposite direction, and continues until it reaches the first non-interconnect
attachment. That attachment is the unique physical sender. Consequently, every
legal delivered path at an input already identifies exactly one sender, even
when shorter routes leave apparently dangling path bits.

The compiler reverse-traces each generated route to obtain its delivered path
signature. The emitted destination adapter exposes a localparam per declared
source, for example `COMMAND__SOURCE_CONTROL`, holding that signature. The
frontend can lower source matching to an ordinary comparison or `case` over the
received path:

```sv
case (command.path)
    COMMAND__SOURCE_CONTROL:  /* handle control */
    COMMAND__SOURCE_DEBUGGER: /* handle debugger */
    default:                  /* ignore, count, or handle an unknown source */
endcase
```

The eventual pleasant syntax may hide `command.path`, but the generated RTL is
intentionally this simple. The fabric may bind additional undeclared sources to
the same input; they still arrive and can be handled by `default`, while only
declared source names receive generated localparams. A direct `>` link has a
trivially known source because the destination input is exclusive.

## Topology and routing compilation

Given the declared destination-handle bindings and options, the fabric compiler
must:

1. Determine the required send and receive directions at every endpoint.
2. Build a degree-three graph that satisfies the requested optimization mode.
3. Find a valid cyclic-port route for every directed connection.
4. Encode each route as its sequence of relative direction bits, least
   significant bit first.
5. Infer each physical link's required payload width from the selected routes,
   then emit independently sized interconnect ports, endpoint adapters, and
   constant route selection logic.
6. Emit a machine-readable manifest listing the graph, port numbering, and
   source/destination route bits.
7. Verify every emitted route by simulation over the generated graph before
   accepting the fabric.

Topology selection is deterministic: the same source, options, and compiler
version yield the same graph, route manifest, and emitted RTL. Language-level
`option` values are the normal configuration mechanism; command-line flags may
provide defaults or override them for experiments, for example:

```text
--fabric-objective=max-hops
--fabric-objective=router-count
--fabric-objective=weighted-hops
--fabric-objective=placement-locality
--fabric-exact-limit=12
```

For small fabrics it may prove optimality with exact search. For larger fabrics
it may use a deterministic heuristic, but must report the objective result and
whether optimality was proved.

## Buffering, liveness, and latency

The fabric is intentionally lightweight. Every unit-facing port has its
mandatory skid entry; endpoint FIFO depth can be increased explicitly when a
unit needs to absorb more offered traffic. Interconnects remain blind carriers
with only the small buffers required by the selected router implementation.

V0 uses two entries at every router ingress and unit-facing endpoint. Allowing
a zero-depth router would reintroduce the long combinational ready paths that
the fabric is specifically intended to avoid. Deeper buffering is a later
configuration feature.

The compiler can report the exact hop count for each generated route and the
uncontended registered traversal latency implied by the chosen interconnect
implementation. It should also emit counters or optional monitor ports for
endpoint occupancy, injection stalls, and per-port contention, so users can
observe whether their traffic assumptions hold.

It cannot promise a finite wall-clock delivery bound under unrestricted offered
traffic or a permanently unready destination: ordinary ready/valid backpressure
and round-robin contention can delay a packet arbitrarily. Endpoint FIFO depth
controls injection pressure, but by itself is not a formal deadlock proof for
every cyclic topology and traffic pattern. V0 deliberately offers best-effort
transport; a later liveness mode may add a constrained topology, escape path,
virtual channels, or scheduled arbitration if a hard guarantee is needed.

## Non-goals for v0

- Runtime destination lookup or address decoding.
- Router awareness of packet format or endpoint identity.
- Variable-width packets or per-hop packet metadata.
- Hard real-time latency guarantees under arbitrary contention.
