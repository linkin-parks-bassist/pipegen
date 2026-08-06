"""Blind source-routed fabric parsing, topology construction, and SV emission."""

from __future__ import annotations

import re
import math
from html import escape
from collections import deque
from dataclasses import dataclass, field

from pigen import IDENT, PigenError, SourceLine, source_lines


@dataclass(frozen=True, order=True)
class SourceHandle:
    instance: str
    port: str
    handle: str

    @property
    def text(self) -> str:
        return f"{self.instance}.{self.port}.{self.handle}"


@dataclass(frozen=True, order=True)
class DestinationInput:
    instance: str
    port: str

    @property
    def text(self) -> str:
        return f"{self.instance}.{self.port}"


@dataclass(frozen=True)
class Connection:
    source: SourceHandle
    destination: DestinationInput
    recognized_source: str | None
    tier: int
    direct: bool
    line: int

    @property
    def arrow(self) -> str:
        return ">" if self.direct else "-" * self.tier + ">"


@dataclass
class Fabric:
    name: str
    params: list[tuple[str, str, int]]
    options: dict[str, str]
    connections: list[Connection]

    @property
    def payload_width(self) -> str:
        for name, _, _ in self.params:
            if name == "PAYLOAD_W":
                return name
        raise PigenError(1, "fabric requires `parameter integer PAYLOAD_W = ...`")


@dataclass(frozen=True, order=True)
class Endpoint:
    direction: str
    name: SourceHandle | DestinationInput

    @property
    def text(self) -> str:
        return f"{self.direction}:{self.name.text}"


@dataclass(frozen=True, order=True)
class DiagramUnit:
    """One rendered module instance, potentially with several port anchors."""
    instance: str


@dataclass(frozen=True)
class Attachment:
    kind: str
    target: str | Endpoint | None
    port: int | None = None


@dataclass(frozen=True)
class Route:
    connection: Connection
    bits: tuple[int, ...]
    path_word: int
    delivered_word: int

    @property
    def hops(self) -> int:
        return len(self.bits)


@dataclass
class Topology:
    routers: dict[str, list[Attachment | None]]
    endpoint_ports: dict[Endpoint, tuple[str, int]]
    routes: dict[Connection, Route]
    path_width: int


class FabricParser:
    def __init__(self, text: str):
        self.lines = source_lines(text)
        self.i = 0

    def current(self) -> SourceLine:
        if self.i >= len(self.lines):
            raise PigenError(self.lines[-1].number if self.lines else 1, "unexpected end of file")
        return self.lines[self.i]

    def parse(self) -> Fabric:
        if not self.lines:
            raise PigenError(1, "file is empty")
        first = self.current()
        match = re.fullmatch(r"fabric\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(#\(|begin)", first.text)
        if not match:
            raise PigenError(first.number, "expected `fabric NAME begin` or `fabric NAME #(`")
        name, opening = match.groups()
        self.i += 1
        params = self.parse_parameters() if opening == "#(" else []
        options: dict[str, str] = {}
        connections: list[Connection] = []
        while self.current().text != "endfabric":
            line = self.current()
            self.i += 1
            option = re.fullmatch(r"option\s+([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*([^;]+);", line.text)
            if option:
                key, value = option.groups()
                if key in options:
                    raise PigenError(line.number, f"duplicate fabric option `{key}`")
                options[key] = value.strip()
                continue
            connections.append(parse_connection(line))
        self.i += 1
        if self.i != len(self.lines):
            raise PigenError(self.current().number, "content after endfabric")
        fabric = Fabric(name, params, options, connections)
        validate_fabric(fabric, first.number)
        return fabric

    def parse_parameters(self) -> list[tuple[str, str, int]]:
        result: list[tuple[str, str, int]] = []
        names: set[str] = set()
        while True:
            line = self.current()
            if line.text == ") begin":
                self.i += 1
                return result
            match = re.fullmatch(r"parameter\s+integer\s+([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.+?)\s*,?", line.text)
            if not match:
                raise PigenError(line.number, "expected `parameter integer NAME = DEFAULT` or `) begin`")
            name, default = match.groups()
            if name in names:
                raise PigenError(line.number, f"duplicate parameter `{name}`")
            names.add(name)
            result.append((name, default.strip(), line.number))
            self.i += 1


def parse_source(text: str, line: int) -> SourceHandle:
    parts = text.split(".")
    if len(parts) != 3 or not all(IDENT.fullmatch(part) for part in parts):
        raise PigenError(line, "source must be `instance.output_port.destination_handle`")
    return SourceHandle(*parts)


def parse_destination(text: str, line: int) -> tuple[DestinationInput, str | None]:
    parts = text.split(".")
    if len(parts) not in (2, 3) or not all(IDENT.fullmatch(part) for part in parts):
        raise PigenError(line, "destination must be `instance.input_port` or `instance.input_port.recognized_source`")
    return DestinationInput(parts[0], parts[1]), parts[2] if len(parts) == 3 else None


def parse_connection(line: SourceLine) -> Connection:
    match = re.fullmatch(r"(.+?)\s*(-+>|>)\s*(.+?)\s*;", line.text)
    if not match:
        raise PigenError(line.number, "expected `source > destination;` or `source -> destination;`")
    source_text, arrow, destination_text = match.groups()
    source = parse_source(source_text.strip(), line.number)
    destination, recognized_source = parse_destination(destination_text.strip(), line.number)
    return Connection(source, destination, recognized_source, 0 if arrow == ">" else len(arrow) - 1, arrow == ">", line.number)


def validate_fabric(fabric: Fabric, line: int) -> None:
    if not fabric.connections:
        raise PigenError(line, "fabric requires at least one connection")
    fabric.payload_width
    allowed_options = {"objective", "router_buffer_depth", "endpoint_fifo_depth"}
    for key, value in fabric.options.items():
        if key not in allowed_options:
            raise PigenError(line, f"unknown fabric option `{key}`")
        if key.endswith("_depth"):
            if not value.isdecimal() or int(value) < 1:
                raise PigenError(line, f"`{key}` must be an integer of at least one")
            if int(value) != 2:
                raise PigenError(line, f"V0 fixes `{key}` at 2 entries")
    sources: set[SourceHandle] = set()
    direct_destinations: set[DestinationInput] = set()
    destinations: dict[DestinationInput, list[Connection]] = {}
    recognized: set[tuple[DestinationInput, str]] = set()
    for connection in fabric.connections:
        if connection.source in sources:
            raise PigenError(connection.line, f"source handle `{connection.source.text}` is bound more than once")
        sources.add(connection.source)
        destinations.setdefault(connection.destination, []).append(connection)
        if connection.recognized_source:
            key = (connection.destination, connection.recognized_source)
            if key in recognized:
                raise PigenError(connection.line, f"recognized source `{connection.destination.text}.{connection.recognized_source}` is bound more than once")
            recognized.add(key)
        if connection.direct:
            direct_destinations.add(connection.destination)
    for destination, links in destinations.items():
        if destination in direct_destinations and len(links) != 1:
            raise PigenError(next(link.line for link in links if link.direct), f"direct destination `{destination.text}` cannot appear in any other connection")


def parse_fabric(text: str) -> Fabric:
    return FabricParser(text).parse()


def endpoint_for_source(source: SourceHandle) -> Endpoint:
    return Endpoint("source", source)


def endpoint_for_destination(destination: DestinationInput) -> Endpoint:
    return Endpoint("destination", destination)


def rotate_right(value: int, width: int, count: int = 1) -> int:
    if width <= 0:
        return 0
    mask = (1 << width) - 1
    for _ in range(count % width):
        value = ((value >> 1) | ((value & 1) << (width - 1))) & mask
    return value


def rotate_left(value: int, width: int) -> int:
    if width <= 0:
        return 0
    mask = (1 << width) - 1
    return ((value << 1) | (value >> (width - 1))) & mask


def return_path(delivered_word: int, width: int) -> int:
    """Convert a delivered rotating route into a normal forward route home."""
    result = 0
    for index in range(width):
        bit = (delivered_word >> index) & 1
        result |= (1 - bit) << (width - 1 - index)
    return result


def build_topology(fabric: Fabric) -> Topology:
    non_direct = [connection for connection in fabric.connections if not connection.direct]
    if not non_direct:
        return Topology({}, {}, {}, 0)
    endpoints = sorted({endpoint_for_source(connection.source) for connection in non_direct} | {endpoint_for_destination(connection.destination) for connection in non_direct})
    leaf_count = 1
    while leaf_count < len(endpoints):
        leaf_count *= 2
    leaves: list[Endpoint | None | str] = [*endpoints, *([None] * (leaf_count - len(endpoints)))]
    routers: dict[str, list[Attachment | None]] = {}
    endpoint_ports: dict[Endpoint, tuple[str, int]] = {}
    counter = 0

    def make_parent(left: Endpoint | None | str, right: Endpoint | None | str) -> Endpoint | None | str:
        nonlocal counter
        # Padding the leaf list to a power of two is an implementation detail,
        # not hardware.  Propagate a lone real child upward instead of emitting
        # a degree-one router for an empty sibling subtree.
        if left is None:
            return right
        if right is None:
            return left
        name = f"r{counter}"
        counter += 1
        routers[name] = [None, None, None]
        for port, child in ((1, left), (2, right)):
            if child is None:
                routers[name][port] = Attachment("dummy", None)
            elif isinstance(child, Endpoint):
                routers[name][port] = Attachment("endpoint", child)
                endpoint_ports[child] = (name, port)
            else:
                routers[name][port] = Attachment("router", child, 0)
                routers[child][0] = Attachment("router", name, port)
        return name

    level: list[Endpoint | None | str] = leaves
    while len(level) > 1:
        level = [make_parent(level[index], level[index + 1]) for index in range(0, len(level), 2)]
    root = level[0]
    assert isinstance(root, str)
    routers[root][0] = Attachment("dummy", None)

    routes: dict[Connection, Route] = {}
    for connection in non_direct:
        source = endpoint_for_source(connection.source)
        destination = endpoint_for_destination(connection.destination)
        bits = tuple(route_bits(routers, endpoint_ports, source, destination))
        routes[connection] = Route(connection, bits, sum(bit << index for index, bit in enumerate(bits)), 0)
    path_width = max(route.hops for route in routes.values())
    routes = {connection: Route(route.connection, route.bits, route.path_word, rotate_right(route.path_word, path_width, route.hops)) for connection, route in routes.items()}
    topology = Topology(routers, endpoint_ports, routes, path_width)
    signatures: dict[tuple[DestinationInput, int], Connection] = {}
    for connection, route in routes.items():
        destination_endpoint = endpoint_for_destination(connection.destination)
        if forward_trace(topology, connection.source, route.path_word) != destination_endpoint:
            raise AssertionError(f"route from `{connection.source.text}` did not reach `{connection.destination.text}`")
        if reverse_trace(topology, connection.destination, route.delivered_word) != endpoint_for_source(connection.source):
            raise AssertionError(f"route to `{connection.destination.text}` did not reverse to `{connection.source.text}`")
        key = (connection.destination, route.delivered_word)
        if key in signatures:
            raise AssertionError(f"routes from `{connection.source.text}` and `{signatures[key].source.text}` collide at `{connection.destination.text}`")
        signatures[key] = connection
    return topology


def neighbor_for_port(routers: dict[str, list[Attachment | None]], router: str, port: int) -> tuple[str, str | Endpoint]:
    attachment = routers[router][port]
    assert attachment is not None
    if attachment.kind == "router":
        assert isinstance(attachment.target, str)
        return "router", attachment.target
    if attachment.kind == "endpoint":
        assert isinstance(attachment.target, Endpoint)
        return "endpoint", attachment.target
    raise ValueError("dummy ports are not routeable")


def port_to_neighbor(routers: dict[str, list[Attachment | None]], router: str, neighbor_kind: str, neighbor: str | Endpoint) -> int:
    for port, attachment in enumerate(routers[router]):
        if attachment is None:
            continue
        if attachment.kind == neighbor_kind and attachment.target == neighbor:
            return port
    raise ValueError(f"router `{router}` is not linked to `{neighbor}`")


def route_bits(routers: dict[str, list[Attachment | None]], endpoint_ports: dict[Endpoint, tuple[str, int]], source: Endpoint, destination: Endpoint) -> list[int]:
    adjacency: dict[tuple[str, str | Endpoint], list[tuple[str, str | Endpoint]]] = {}

    def add(left: tuple[str, str | Endpoint], right: tuple[str, str | Endpoint]) -> None:
        adjacency.setdefault(left, []).append(right)
        adjacency.setdefault(right, []).append(left)

    for router, ports in routers.items():
        for attachment in ports:
            if attachment and attachment.kind == "router" and isinstance(attachment.target, str):
                add(("router", router), ("router", attachment.target))
            elif attachment and attachment.kind == "endpoint" and isinstance(attachment.target, Endpoint):
                add(("router", router), ("endpoint", attachment.target))
    start, finish = ("endpoint", source), ("endpoint", destination)
    queue: deque[tuple[str, str | Endpoint]] = deque([start])
    previous: dict[tuple[str, str | Endpoint], tuple[str, str | Endpoint] | None] = {start: None}
    while queue:
        current = queue.popleft()
        if current == finish:
            break
        for next_ in adjacency.get(current, []):
            if next_ not in previous:
                previous[next_] = current
                queue.append(next_)
    if finish not in previous:
        raise AssertionError("tree did not connect route endpoints")
    path: list[tuple[str, str | Endpoint]] = []
    current: tuple[str, str | Endpoint] | None = finish
    while current is not None:
        path.append(current)
        current = previous[current]
    path.reverse()
    bits: list[int] = []
    for index, (kind, value) in enumerate(path):
        if kind != "router":
            continue
        assert isinstance(value, str)
        previous_kind, previous_value = path[index - 1]
        next_kind, next_value = path[index + 1]
        in_port = port_to_neighbor(routers, value, previous_kind, previous_value)
        out_port = port_to_neighbor(routers, value, next_kind, next_value)
        if out_port == (in_port + 1) % 3:
            bits.append(0)
        elif out_port == (in_port + 2) % 3:
            bits.append(1)
        else:
            raise AssertionError("tree route attempted a U-turn")
    return bits


def reverse_trace(topology: Topology, destination: DestinationInput, delivered_word: int) -> Endpoint:
    current_router, out_port = topology.endpoint_ports[endpoint_for_destination(destination)]
    word = delivered_word
    while True:
        bit = (word >> (topology.path_width - 1)) & 1
        in_port = (out_port - 1) % 3 if bit == 0 else (out_port - 2) % 3
        attachment = topology.routers[current_router][in_port]
        assert attachment is not None
        word = rotate_left(word, topology.path_width)
        if attachment.kind == "endpoint":
            assert isinstance(attachment.target, Endpoint)
            return attachment.target
        if attachment.kind != "router" or not isinstance(attachment.target, str) or attachment.port is None:
            raise AssertionError("reverse route reached a non-source attachment")
        current_router, out_port = attachment.target, attachment.port


def forward_trace_from_endpoint(topology: Topology, endpoint: Endpoint, path_word: int) -> Endpoint:
    current_router, in_port = topology.endpoint_ports[endpoint]
    word = path_word
    while True:
        bit = word & 1
        out_port = (in_port + 1) % 3 if bit == 0 else (in_port + 2) % 3
        attachment = topology.routers[current_router][out_port]
        assert attachment is not None
        word = rotate_right(word, topology.path_width)
        if attachment.kind == "endpoint":
            assert isinstance(attachment.target, Endpoint)
            return attachment.target
        if attachment.kind != "router" or not isinstance(attachment.target, str) or attachment.port is None:
            raise AssertionError("forward route reached a non-destination attachment")
        current_router, in_port = attachment.target, attachment.port


def forward_trace(topology: Topology, source: SourceHandle, path_word: int) -> Endpoint:
    return forward_trace_from_endpoint(topology, endpoint_for_source(source), path_word)


def sv_name(*parts: str) -> str:
    return "__".join(re.sub(r"[^A-Za-z0-9_$]", "_", part) for part in parts)


def parameter_block(fabric: Fabric) -> str:
    if not fabric.params:
        return ""
    return " #(\n" + ",\n".join(f"\tparameter integer {name} = {default}" for name, default, _ in fabric.params) + "\n)"


def render_skid(name: str) -> str:
    return f'''module {name}__skid #(parameter integer PACKET_W = 1)
	(
		input  logic clk,
		input  logic reset,

		input  logic enable,

		input  logic in_valid,
		output logic in_ready,
		input  logic [PACKET_W-1:0] packet_in,

		output logic out_valid,
		input  logic out_ready,
		output logic [PACKET_W-1:0] packet_out
	);

	logic [1:0] count;
	logic [PACKET_W-1:0] packet0;
	logic [PACKET_W-1:0] packet1;
	wire push = in_valid & in_ready;
	wire pop = out_valid & out_ready;

	// Two entries give full throughput without feeding downstream ready back
	// into the producer.  This is the timing boundary used at every endpoint.
	assign in_ready = enable & (count != 2);
	assign out_valid = enable & (count != 0);
	assign packet_out = packet0;

	always_ff @(posedge clk) begin
		if (reset) begin
			count <= 0;
			packet0 <= '0;
			packet1 <= '0;
		end else if (enable) begin
			case ({{push, pop}})
				2'b10: begin
					if (count == 0) packet0 <= packet_in;
					else            packet1 <= packet_in;
					count <= count + 1'b1;
				end
				2'b01: begin
					if (count == 2) packet0 <= packet1;
					count <= count - 1'b1;
				end
				2'b11: begin
					if (count == 2) begin
						packet0 <= packet1;
						packet1 <= packet_in;
					end else begin
						packet0 <= packet_in;
					end
				end
				default: begin end
			endcase
		end
	end
endmodule
'''


def render_router(name: str) -> str:
    declarations: list[str] = []
    for index in range(3):
        declarations += [f"\tlogic [1:0] p{index}_count;", f"\tlogic [PACKET_W-1:0] p{index}_packet0;", f"\tlogic [PACKET_W-1:0] p{index}_packet1;", f"\tlogic [1:0] grant{index};", f"\tlogic grant{index}_valid;", f"\tlogic rr{index};"]
    for index in range(3):
        declarations += [f"\twire p{index}_remove;", f"\twire p{index}_push = p{index}_in_valid & p{index}_in_ready;"]
    declarations.append("")
    for index in range(3):
        declarations.append(f"\tassign p{index}_in_ready = enable & (p{index}_count != 2);")
    declarations.append("")
    for index in range(3):
        for target in range(3):
            if target == index:
                continue
            bit = "~" if target == (index + 1) % 3 else ""
            declarations.append(f"\twire p{index}_to_p{target} = (p{index}_count != 0) & {bit}p{index}_packet0[PAYLOAD_W];")
    declarations.append("")
    body = [f"module {name}__router #(parameter integer PAYLOAD_W = 1, parameter integer PATH_W = 1, parameter integer PACKET_W = PAYLOAD_W + PATH_W)", "\t(", "\t\tinput  logic clk,", "\t\tinput  logic reset,", "", "\t\tinput  logic enable,", ""]
    router_ports: list[str] = []
    for index in range(3):
        router_ports += [f"\t\tinput  logic p{index}_in_valid", f"\t\toutput logic p{index}_in_ready", f"\t\tinput  logic [PACKET_W-1:0] p{index}_in_packet", f"\t\toutput logic p{index}_out_valid", f"\t\tinput  logic p{index}_out_ready", f"\t\toutput logic [PACKET_W-1:0] p{index}_out_packet"]
    body += [entry + ("," if position != len(router_ports) - 1 else "") for position, entry in enumerate(router_ports)]
    body += ["\t);", "", "\tfunction automatic [PACKET_W-1:0] forward_packet(input logic [PACKET_W-1:0] packet);", "\t\tlogic [PATH_W-1:0] path;", "\t\tbegin", "\t\t\tpath = packet[PACKET_W-1:PAYLOAD_W];", "\t\t\tpath = path >> 1;", "\t\t\tpath[PATH_W-1] = packet[PAYLOAD_W];", "\t\t\tforward_packet = {path, packet[PAYLOAD_W-1:0]};", "\t\tend", "\tendfunction", ""] + declarations
    for output in range(3):
        first, second = (output + 2) % 3, (output + 1) % 3
        body += ["", "\talways @* begin", f"\t\tgrant{output}_valid = 1'b0;", f"\t\tgrant{output} = 2'd0;", f"\t\tp{output}_out_valid = 1'b0;", f"\t\tp{output}_out_packet = '0;", f"\t\tif (p{first}_to_p{output} & p{second}_to_p{output}) begin", f"\t\t\tgrant{output}_valid = 1'b1;", f"\t\t\tgrant{output} = rr{output} ? 2'd{second} : 2'd{first};", f"\t\tend else if (p{first}_to_p{output}) begin", f"\t\t\tgrant{output}_valid = 1'b1;", f"\t\t\tgrant{output} = 2'd{first};", f"\t\tend else if (p{second}_to_p{output}) begin", f"\t\t\tgrant{output}_valid = 1'b1;", f"\t\t\tgrant{output} = 2'd{second};", "\t\tend", f"\t\tif (enable & grant{output}_valid) begin", f"\t\t\tp{output}_out_valid = 1'b1;", f"\t\t\tcase (grant{output})", f"\t\t\t\t2'd0: p{output}_out_packet = forward_packet(p0_packet0);", f"\t\t\t\t2'd1: p{output}_out_packet = forward_packet(p1_packet0);", f"\t\t\t\tdefault: p{output}_out_packet = forward_packet(p2_packet0);", "\t\t\tendcase", "\t\tend", "\tend"]
    for index in range(3):
        terms = [f"(p{output}_out_valid & p{output}_out_ready & (grant{output} == 2'd{index}))" for output in range(3)]
        body.append(f"\tassign p{index}_remove = " + " | ".join(terms) + ";")
    for output in range(3):
        body.append(f"\twire p{output}_fire = p{output}_out_valid & p{output}_out_ready;")
    body += ["", "\talways_ff @(posedge clk) begin", "\t\tif (reset) begin"]
    for index in range(3):
        body += [f"\t\t\tp{index}_count <= 0;", f"\t\t\tp{index}_packet0 <= '0;", f"\t\t\tp{index}_packet1 <= '0;", f"\t\t\trr{index} <= 1'b0;"]
    body += ["\t\tend else if (enable) begin"]
    for index in range(3):
        body += [f"\t\t\tcase ({{p{index}_push, p{index}_remove}})", "\t\t\t\t2'b10: begin", f"\t\t\t\t\tif (p{index}_count == 0) p{index}_packet0 <= p{index}_in_packet;", f"\t\t\t\t\telse                     p{index}_packet1 <= p{index}_in_packet;", f"\t\t\t\t\tp{index}_count <= p{index}_count + 1'b1;", "\t\t\t\tend", "\t\t\t\t2'b01: begin", f"\t\t\t\t\tif (p{index}_count == 2) p{index}_packet0 <= p{index}_packet1;", f"\t\t\t\t\tp{index}_count <= p{index}_count - 1'b1;", "\t\t\t\tend", "\t\t\t\t2'b11: begin", f"\t\t\t\t\tif (p{index}_count == 2) begin", f"\t\t\t\t\t\tp{index}_packet0 <= p{index}_packet1;", f"\t\t\t\t\t\tp{index}_packet1 <= p{index}_in_packet;", "\t\t\t\t\tend else begin", f"\t\t\t\t\t\tp{index}_packet0 <= p{index}_in_packet;", "\t\t\t\t\tend", "\t\t\t\tend", "\t\t\t\tdefault: begin end", "\t\t\tendcase"]
    for output in range(3):
        first = (output + 2) % 3
        body += [f"\t\t\tif (p{output}_fire) begin", f"\t\t\t\trr{output} <= (grant{output} == 2'd{first});", "\t\t\tend"]
    body += ["\t\tend", "\tend", "endmodule", ""]
    return "\n".join(body)


def source_signal(source: SourceHandle, suffix: str) -> str:
    return sv_name(source.instance, source.port, source.handle, suffix)


def destination_signal(destination: DestinationInput, suffix: str) -> str:
    return sv_name(destination.instance, destination.port, suffix)


def render_fabric(fabric: Fabric, topology: Topology) -> str:
    sources = sorted({connection.source for connection in fabric.connections})
    destinations = sorted({connection.destination for connection in fabric.connections})
    direct_connections = [connection for connection in fabric.connections if connection.direct]
    routed_connections = [connection for connection in fabric.connections if not connection.direct]
    routed_destinations = {connection.destination for connection in routed_connections}
    ports = ["\t\tinput  logic clk", "\t\tinput  logic reset", "\t\tinput  logic enable"]
    for source in sources:
        ports += [f"\t\tinput  logic {source_signal(source, 'valid')}", f"\t\toutput logic {source_signal(source, 'ready')}", f"\t\tinput  logic [PAYLOAD_W-1:0] {source_signal(source, 'payload')}"]
    for destination in destinations:
        ports += [f"\t\toutput logic {destination_signal(destination, 'valid')}", f"\t\tinput  logic {destination_signal(destination, 'ready')}", f"\t\toutput logic [PAYLOAD_W-1:0] {destination_signal(destination, 'payload')}"]
        if destination in routed_destinations:
            ports.append(f"\t\toutput logic [PATH_W-1:0] {destination_signal(destination, 'path')}")
    lines = ["`timescale 1ns/1ps", "// Generated by Pigen; do not edit.", render_skid(fabric.name)]
    if topology.routers:
        lines.append(render_router(fabric.name))
    parameter_lines = [f"\tparameter integer {name} = {default}" for name, default, _ in fabric.params]
    if topology.routers:
        parameter_lines.append(f"\tparameter integer PATH_W = {topology.path_width}")
    parameters = " #(\n" + ",\n".join(parameter_lines) + "\n)" if parameter_lines else ""
    lines += [f"module {fabric.name}{parameters}", "\t(", ",\n".join(ports), "\t);", ""]
    if topology.routers:
        lines += ["\tlocalparam integer PACKET_W = PATH_W + PAYLOAD_W;", ""]
        for connection, route in sorted(topology.routes.items(), key=lambda item: item[0].source.text):
            lines.append(f"\tlocalparam logic [PATH_W-1:0] ROUTE__{sv_name(connection.source.instance, connection.source.port, connection.source.handle)} = {route.path_word};")
        for connection, route in sorted(topology.routes.items(), key=lambda item: item[0].destination.text):
            if connection.recognized_source:
                lines.append(f"\tlocalparam logic [PATH_W-1:0] {sv_name(connection.destination.instance, connection.destination.port, 'SOURCE', connection.recognized_source)} = {route.delivered_word};")
        lines.append("")
    source_packets: dict[SourceHandle, tuple[str, str, str]] = {}
    for connection in fabric.connections:
        source = connection.source
        in_packet = source_signal(source, "packet_in")
        out_valid, out_ready, out_packet = source_signal(source, "packet_valid"), source_signal(source, "packet_ready"), source_signal(source, "packet")
        width = "PAYLOAD_W" if connection.direct else "PACKET_W"
        payload = source_signal(source, "payload")
        lines += [f"\twire [{width}-1:0] {in_packet};", f"\twire {out_valid}, {out_ready};", f"\twire [{width}-1:0] {out_packet};"]
        if connection.direct:
            lines.append(f"\tassign {in_packet} = {payload};")
        else:
            route_name = f"ROUTE__{sv_name(source.instance, source.port, source.handle)}"
            lines.append(f"\tassign {in_packet} = {{{route_name}, {payload}}};")
        lines.append(f"\t{fabric.name}__skid #(.PACKET_W({width})) u_tx__{sv_name(source.instance, source.port, source.handle)} (.clk(clk), .reset(reset), .enable(enable), .in_valid({source_signal(source, 'valid')}), .in_ready({source_signal(source, 'ready')}), .packet_in({in_packet}), .out_valid({out_valid}), .out_ready({out_ready}), .packet_out({out_packet}));")
        source_packets[source] = (out_valid, out_ready, out_packet)
    lines.append("")
    destination_packets: dict[DestinationInput, tuple[str, str, str]] = {}
    for destination in destinations:
        routed = destination in routed_destinations
        width = "PACKET_W" if routed else "PAYLOAD_W"
        in_valid, in_ready, in_packet = destination_signal(destination, "packet_valid"), destination_signal(destination, "packet_ready"), destination_signal(destination, "packet")
        out_packet = destination_signal(destination, "packet_out")
        lines += [f"\twire {in_valid}, {in_ready};", f"\twire [{width}-1:0] {in_packet};", f"\twire [{width}-1:0] {out_packet};", f"\t{fabric.name}__skid #(.PACKET_W({width})) u_rx__{sv_name(destination.instance, destination.port)} (.clk(clk), .reset(reset), .enable(enable), .in_valid({in_valid}), .in_ready({in_ready}), .packet_in({in_packet}), .out_valid({destination_signal(destination, 'valid')}), .out_ready({destination_signal(destination, 'ready')}), .packet_out({out_packet}));"]
        if routed:
            lines += [f"\tassign {destination_signal(destination, 'payload')} = {out_packet}[PAYLOAD_W-1:0];", f"\tassign {destination_signal(destination, 'path')} = {out_packet}[PACKET_W-1:PAYLOAD_W];"]
        else:
            lines.append(f"\tassign {destination_signal(destination, 'payload')} = {out_packet};")
        destination_packets[destination] = (in_valid, in_ready, in_packet)
    lines.append("")
    if topology.routers:
        for router in sorted(topology.routers):
            for port in range(3):
                prefix = f"{router}_p{port}"
                lines += [f"\twire {prefix}_in_valid, {prefix}_in_ready;", f"\twire [PACKET_W-1:0] {prefix}_in_packet;", f"\twire {prefix}_out_valid, {prefix}_out_ready;", f"\twire [PACKET_W-1:0] {prefix}_out_packet;"]
        lines.append("")
        for router in sorted(topology.routers):
            connections = [".clk(clk)", ".reset(reset)", ".enable(enable)"]
            for port in range(3):
                prefix = f"{router}_p{port}"
                connections += [f".p{port}_in_valid({prefix}_in_valid)", f".p{port}_in_ready({prefix}_in_ready)", f".p{port}_in_packet({prefix}_in_packet)", f".p{port}_out_valid({prefix}_out_valid)", f".p{port}_out_ready({prefix}_out_ready)", f".p{port}_out_packet({prefix}_out_packet)"]
            lines.append(f"\t{fabric.name}__router #(.PAYLOAD_W(PAYLOAD_W), .PATH_W(PATH_W)) u_{router} (" + ", ".join(connections) + ");")
        lines.append("")
        linked: set[tuple[str, int]] = set()
        for router, ports_for_router in sorted(topology.routers.items()):
            for port, attachment in enumerate(ports_for_router):
                prefix = f"{router}_p{port}"
                if attachment is None or attachment.kind == "dummy":
                    lines += [f"\tassign {prefix}_in_valid = 1'b0;", f"\tassign {prefix}_in_packet = '0;", f"\tassign {prefix}_out_ready = 1'b1;"]
                elif attachment.kind == "router":
                    assert isinstance(attachment.target, str) and attachment.port is not None
                    if (router, port) in linked:
                        continue
                    other, other_port = attachment.target, attachment.port
                    other_prefix = f"{other}_p{other_port}"
                    lines += [f"\tassign {other_prefix}_in_valid = {prefix}_out_valid;", f"\tassign {other_prefix}_in_packet = {prefix}_out_packet;", f"\tassign {prefix}_out_ready = {other_prefix}_in_ready;", f"\tassign {prefix}_in_valid = {other_prefix}_out_valid;", f"\tassign {prefix}_in_packet = {other_prefix}_out_packet;", f"\tassign {other_prefix}_out_ready = {prefix}_in_ready;"]
                    linked.add((router, port))
                    linked.add((other, other_port))
                elif attachment.kind == "endpoint":
                    assert isinstance(attachment.target, Endpoint)
                    endpoint = attachment.target
                    if endpoint.direction == "source":
                        assert isinstance(endpoint.name, SourceHandle)
                        valid, ready, packet = source_packets[endpoint.name]
                        lines += [f"\tassign {prefix}_in_valid = {valid};", f"\tassign {prefix}_in_packet = {packet};", f"\tassign {ready} = {prefix}_in_ready;", f"\tassign {prefix}_out_ready = 1'b1;"]
                    else:
                        assert isinstance(endpoint.name, DestinationInput)
                        valid, ready, packet = destination_packets[endpoint.name]
                        lines += [f"\tassign {valid} = {prefix}_out_valid;", f"\tassign {packet} = {prefix}_out_packet;", f"\tassign {prefix}_out_ready = {ready};", f"\tassign {prefix}_in_valid = 1'b0;", f"\tassign {prefix}_in_packet = '0;"]
        lines.append("")
    for connection in direct_connections:
        valid, ready, packet = source_packets[connection.source]
        in_valid, in_ready, in_packet = destination_packets[connection.destination]
        lines += [f"\tassign {in_valid} = {valid};", f"\tassign {in_packet} = {packet};", f"\tassign {ready} = {in_ready};"]
    lines += ["endmodule", ""]
    return "\n".join(lines)


def render_manifest(fabric: Fabric, topology: Topology) -> str:
    lines = [f"fabric {fabric.name}", f"payload_width = {fabric.payload_width}", f"path_width = {topology.path_width}", "", "connections"]
    for connection in sorted(fabric.connections, key=lambda item: (item.source.text, item.destination.text)):
        if connection.direct:
            lines.append(f"  {connection.source.text} > {connection.destination.text} direct")
            continue
        route = topology.routes[connection]
        source_name = f" as {connection.recognized_source}" if connection.recognized_source else ""
        bits = "".join(str(bit) for bit in reversed(route.bits)) or "0"
        lines.append(f"  {connection.source.text} {connection.arrow} {connection.destination.text}{source_name} hops={route.hops} path=0b{route.path_word:0{topology.path_width}b} delivered=0b{route.delivered_word:0{topology.path_width}b} turns={bits}")
    if topology.routers:
        lines += ["", "routers"]
        for router, ports in sorted(topology.routers.items()):
            entries: list[str] = []
            for index, attachment in enumerate(ports):
                if attachment is None or attachment.kind == "dummy":
                    entries.append(f"p{index}=dummy")
                elif attachment.kind == "router":
                    entries.append(f"p{index}={attachment.target}.p{attachment.port}")
                else:
                    assert isinstance(attachment.target, Endpoint)
                    entries.append(f"p{index}={attachment.target.text}")
            lines.append(f"  {router}: " + ", ".join(entries))
    return "\n".join(lines) + "\n"


def _router_order(name: str) -> tuple[int, str]:
    match = re.fullmatch(r"r(\d+)", name)
    return (int(match.group(1)), name) if match else (1 << 30, name)


def _svg_text(value: object) -> str:
    return escape(str(value), quote=True)


def render_diagram(fabric: Fabric, topology: Topology) -> str:
    """Render one node per module instance, with named port anchors."""
    router_names = sorted(topology.routers, key=_router_order)
    sources = sorted({connection.source for connection in fabric.connections})
    destinations = sorted({connection.destination for connection in fabric.connections})
    source_endpoints = [endpoint_for_source(source) for source in sources]
    destination_endpoints = [endpoint_for_destination(destination) for destination in destinations]
    endpoints = [*source_endpoints, *destination_endpoints]
    endpoint_units = {endpoint: DiagramUnit(endpoint.name.instance) for endpoint in endpoints}
    units = sorted(set(endpoint_units.values()))
    unit_ports: dict[DiagramUnit, dict[str, list[Endpoint]]] = {unit: {"source": [], "destination": []} for unit in units}
    for endpoint in endpoints:
        unit_ports[endpoint_units[endpoint]][endpoint.direction].append(endpoint)
    for ports in unit_ports.values():
        for direction in ports:
            ports[direction].sort(key=lambda endpoint: endpoint.name.text)

    direct_connections = sorted((connection for connection in fabric.connections if connection.direct), key=lambda connection: (connection.source.text, connection.destination.text))
    unit_radius = {unit: max(58.0, 38.0 + 5.0 * (len(ports["source"]) + len(ports["destination"]))) for unit, ports in unit_ports.items()}
    network_height = max(900, 260 + max(len(units), len(router_names), 1) * 100)
    width = 1600
    positions: dict[str | DiagramUnit, list[float]] = {}
    home_positions: dict[str | DiagramUnit, list[float]] = {}
    router_neighbors: dict[str, set[str]] = {router: set() for router in router_names}
    for router, ports in topology.routers.items():
        for attachment in ports:
            if attachment and attachment.kind == "router" and isinstance(attachment.target, str):
                router_neighbors[router].add(attachment.target)

    def router_branch_weight(router: str, parent: str | None) -> int:
        endpoint_count = sum(attachment is not None and attachment.kind == "endpoint" for attachment in topology.routers[router])
        return max(1, endpoint_count + sum(router_branch_weight(child, router) for child in router_neighbors[router] if child != parent))

    def largest_branch(router: str) -> int:
        return max((router_branch_weight(neighbor, router) for neighbor in router_neighbors[router]), default=0)

    root = min(router_names, key=lambda router: (largest_branch(router), _router_order(router))) if router_names else None
    endpoint_targets: dict[Endpoint, list[tuple[float, float]]] = {endpoint: [] for endpoint in endpoints}
    center_x, center_y, radius_step = width / 2, network_height / 2, 130.0

    def seed_router(router: str, parent: str | None, start: float, end: float, depth: int) -> None:
        angle = (start + end) / 2
        if parent is None:
            home_positions[router] = [center_x, center_y]
        else:
            home_positions[router] = [center_x + depth * radius_step * math.cos(angle), center_y + depth * radius_step * math.sin(angle)]
        endpoints_here = [attachment.target for attachment in topology.routers[router] if attachment and attachment.kind == "endpoint"]
        endpoints_here = sorted((endpoint for endpoint in endpoints_here if isinstance(endpoint, Endpoint)), key=lambda endpoint: endpoint.text)
        children = sorted((child for child in router_neighbors[router] if child != parent), key=_router_order)
        items: list[tuple[str, Endpoint | str, int]] = [("endpoint", endpoint, 1) for endpoint in endpoints_here] + [("router", child, router_branch_weight(child, router)) for child in children]
        total = sum(weight for _, _, weight in items) or 1
        cursor = start
        for kind, item, weight in items:
            span = (end - start) * weight / total
            midpoint = cursor + span / 2
            if kind == "endpoint":
                assert isinstance(item, Endpoint)
                endpoint_targets[item].append((center_x + (depth + 1) * radius_step * math.cos(midpoint), center_y + (depth + 1) * radius_step * math.sin(midpoint)))
            else:
                assert isinstance(item, str)
                seed_router(item, router, cursor, cursor + span, depth + 1)
            cursor += span

    if root is not None:
        seed_router(root, None, 0.0, math.tau, 0)
    for index, unit in enumerate(units):
        targets = [target for endpoint in unit_ports[unit]["source"] + unit_ports[unit]["destination"] for target in endpoint_targets[endpoint]]
        if targets:
            home_positions[unit] = [sum(x for x, _ in targets) / len(targets), sum(y for _, y in targets) / len(targets)]
        else:
            column, row = index % 4, index // 4
            home_positions[unit] = [260.0 + column * 355.0, 110.0 + row * 175.0]
    # Preserve the tree's angular relationship, but use it only to order a
    # circular initial placement.  The subsequent physics chooses the final
    # geometry rather than being held to the tree drawing.
    ordered_units = sorted(units, key=lambda unit: (math.atan2(home_positions[unit][1] - center_y, home_positions[unit][0] - center_x), unit.instance))
    initial_radius = min(width, network_height) * 0.36
    for index, unit in enumerate(ordered_units):
        angle = math.tau * index / len(ordered_units)
        home_positions[unit] = [center_x + initial_radius * math.cos(angle), center_y + initial_radius * math.sin(angle)]
    for router in router_names:
        home_positions[router] = [center_x, center_y]
    positions = {node: value.copy() for node, value in home_positions.items()}

    edges: list[tuple[str | DiagramUnit, str | DiagramUnit, str, str, Endpoint | None, Endpoint | None]] = []
    port_peers: dict[Endpoint, str | DiagramUnit] = {}
    linked: set[tuple[str, int]] = set()
    for router in router_names:
        for port, attachment in enumerate(topology.routers[router]):
            if not attachment or attachment.kind == "dummy":
                continue
            if attachment.kind == "router":
                assert isinstance(attachment.target, str) and attachment.port is not None
                if (router, port) in linked:
                    continue
                edges.append((router, attachment.target, "router-link", f"{router}.p{port} ↔ {attachment.target}.p{attachment.port}", None, None))
                linked.add((router, port))
                linked.add((attachment.target, attachment.port))
            else:
                assert attachment.kind == "endpoint" and isinstance(attachment.target, Endpoint)
                if attachment.target.direction == "source":
                    edges.append((endpoint_units[attachment.target], router, "endpoint-link", f"{router}.p{port}", attachment.target, None))
                else:
                    edges.append((router, endpoint_units[attachment.target], "endpoint-link", f"{router}.p{port}", None, attachment.target))
                port_peers[attachment.target] = router
    for connection in direct_connections:
        source_endpoint, destination_endpoint = endpoint_for_source(connection.source), endpoint_for_destination(connection.destination)
        edges.append((endpoint_units[source_endpoint], endpoint_units[destination_endpoint], "direct-link", "direct >", source_endpoint, destination_endpoint))
        port_peers[source_endpoint] = endpoint_units[destination_endpoint]
        port_peers[destination_endpoint] = endpoint_units[source_endpoint]
    direct_endpoints = {endpoint for connection in direct_connections for endpoint in (endpoint_for_source(connection.source), endpoint_for_destination(connection.destination))}
    router_incident: dict[str, list[str | DiagramUnit]] = {router: [] for router in router_names}
    for left, right, kind, _, _, _ in edges:
        if kind == "direct-link":
            continue
        if isinstance(left, str):
            router_incident[left].append(right)
        if isinstance(right, str):
            router_incident[right].append(left)

    def clearance_radius(node: str | DiagramUnit) -> float:
        if isinstance(node, DiagramUnit):
            return unit_radius[node] + 14.0
        return 52.0

    def node_radius(node: str | DiagramUnit) -> float:
        return unit_radius[node] if isinstance(node, DiagramUnit) else 25.0

    def node_mass(node: str | DiagramUnit) -> float:
        # Units are deliberately more inertial than routers: the fabric's
        # internal routing machinery does most of the settling around them.
        return 3.2 if isinstance(node, DiagramUnit) else 1.0

    port_angles: dict[Endpoint, float] = {}

    def port_position(unit: DiagramUnit, endpoint: Endpoint) -> tuple[float, float]:
        x, y = positions[unit]
        angle = port_angles[endpoint]
        return x + unit_radius[unit] * math.cos(angle), y + unit_radius[unit] * math.sin(angle)

    def edge_position(node: str | DiagramUnit, endpoint: Endpoint | None, other: str | DiagramUnit) -> tuple[float, float]:
        if isinstance(node, DiagramUnit):
            assert endpoint is not None
            return port_position(node, endpoint)
        x, y = positions[node]
        other_x, other_y = positions[other]
        distance = max(math.hypot(other_x - x, other_y - y), 1.0)
        return x + 25 * (other_x - x) / distance, y + 25 * (other_y - y) / distance

    # The circular seed is only a deterministic starting point.  Every unit
    # and router then relaxes under stiff link springs, a small outward pull on
    # units, and a final footprint-aware collision pass.  There is deliberately
    # no general node repulsion: it fights the common link-length target.
    collision_start = 1200
    for iteration in range(6000):
        forces = {node: [0.0, 0.0] for node in [*router_names, *units]}
        nodes = [*router_names, *units]
        for left, right, kind, _, _, _ in edges:
            lx, ly = positions[left]
            rx, ry = positions[right]
            dx, dy = rx - lx, ry - ly
            distance = max(math.hypot(dx, dy), 1.0)
            # Shared routed links settle a little above their nominal visible
            # span; an isolated direct pair does not.  The small direct offset
            # equalizes what is actually drawn rather than center distances.
            visible_length = 220 if kind == "direct-link" else 190
            rest_length = visible_length + node_radius(left) + node_radius(right)
            stiffness = 0.320
            strength = stiffness * (distance - rest_length)
            fx, fy = dx / distance * strength, dy / distance * strength
            forces[left][0] += fx
            forces[left][1] += fy
            forces[right][0] -= fx
            forces[right][1] -= fy
            # Snap the broad tree layout toward the eight clean compass angles.
            # This is deliberately stronger than the cosmetic spring forces.
            snapped = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            normal_x, normal_y = -math.sin(snapped), math.cos(snapped)
            offset = dx * normal_x + dy * normal_y
            alignment = 0.035
            forces[left][0] += normal_x * offset * alignment
            forces[left][1] += normal_y * offset * alignment
            forces[right][0] -= normal_x * offset * alignment
            forces[right][1] -= normal_y * offset * alignment
        # Keep the wires at each router usefully distributed around it.  This
        # is a tangential force: it changes an edge's bearing, not its length,
        # and preserves the current clockwise ordering of incident links.
        for router, neighbours in router_incident.items():
            if len(neighbours) < 2:
                continue
            router_x, router_y = positions[router]
            bearings = sorted(
                ((math.atan2(positions[neighbour][1] - router_y, positions[neighbour][0] - router_x), neighbour) for neighbour in neighbours),
                key=lambda item: (item[0], item[1].instance if isinstance(item[1], DiagramUnit) else item[1]),
            )
            target_gap = math.tau / len(bearings)
            for index, (angle, neighbour) in enumerate(bearings):
                next_angle, next_neighbour = bearings[(index + 1) % len(bearings)]
                gap = (next_angle - angle) % math.tau
                error = gap - target_gap
                distance = min(
                    math.hypot(positions[neighbour][0] - router_x, positions[neighbour][1] - router_y),
                    math.hypot(positions[next_neighbour][0] - router_x, positions[next_neighbour][1] - router_y),
                )
                magnitude = error * distance * 0.055
                forces[neighbour][0] += -math.sin(angle) * magnitude
                forces[neighbour][1] += math.cos(angle) * magnitude
                forces[next_neighbour][0] -= -math.sin(next_angle) * magnitude
                forces[next_neighbour][1] -= math.cos(next_angle) * magnitude
        # Direct links are intentional hard connections and should remain
        # visually legible.  If a routed edge crosses one, push that complete
        # routed edge gently to one side; unlike pairwise repulsion this acts
        # only on an actual graph crossing.
        for direct_left, direct_right, direct_kind, _, _, _ in edges:
            if direct_kind != "direct-link":
                continue
            ax, ay = positions[direct_left]
            bx, by = positions[direct_right]
            direct_dx, direct_dy = bx - ax, by - ay
            direct_length = max(math.hypot(direct_dx, direct_dy), 1.0)
            for other_left, other_right, other_kind, _, _, _ in edges:
                if other_kind == "direct-link" or {direct_left, direct_right} & {other_left, other_right}:
                    continue
                cx, cy = positions[other_left]
                dx, dy = positions[other_right]
                first_left = direct_dx * (cy - ay) - direct_dy * (cx - ax)
                first_right = direct_dx * (dy - ay) - direct_dy * (dx - ax)
                other_left_side = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
                other_right_side = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
                if first_left * first_right >= -0.001 or other_left_side * other_right_side >= -0.001:
                    continue
                midpoint_side = direct_dx * ((cy + dy) / 2 - ay) - direct_dy * ((cx + dx) / 2 - ax)
                side = 1 if midpoint_side >= 0 else -1
                push_x, push_y = -direct_dy / direct_length * side * 1.8, direct_dx / direct_length * side * 1.8
                forces[other_left][0] += push_x
                forces[other_left][1] += push_y
                forces[other_right][0] += push_x
                forces[other_right][1] += push_y
        for node, force in forces.items():
            x, y = positions[node]
            if isinstance(node, DiagramUnit):
                radial_x, radial_y = x - center_x, y - center_y
                radial_length = math.hypot(radial_x, radial_y)
                if radial_length > 0.001:
                    # Units gently elaborate the graph outward; routers are
                    # left free to settle among the link constraints.
                    force[0] += radial_x / radial_length * 0.18
                    force[1] += radial_y / radial_length * 0.18
            mass = node_mass(node)
            positions[node] = [x + force[0] / mass, y + force[1] / mass]
        # Springs alone settle close to their rest lengths, which is not enough
        # for a wide unit block.  Resolve actual node footprints after every
        # integration step so no unit or router can remain intersecting.
        if iteration < collision_start:
            continue
        for _ in range(3):
            for left_index, left in enumerate(nodes):
                lx, ly = positions[left]
                for right in nodes[left_index + 1:]:
                    rx, ry = positions[right]
                    dx, dy = rx - lx, ry - ly
                    distance = math.hypot(dx, dy)
                    minimum = clearance_radius(left) + clearance_radius(right)
                    if distance >= minimum:
                        continue
                    if distance < 0.001:
                        angle = (left_index + 1) * 2.399963229728653
                        dx, dy, distance = math.cos(angle), math.sin(angle), 1.0
                    correction = minimum - distance
                    left_share = (1 / node_mass(left)) / (1 / node_mass(left) + 1 / node_mass(right))
                    right_share = 1 - left_share
                    move_x, move_y = dx / distance * correction, dy / distance * correction
                    positions[left] = [lx - move_x * left_share, ly - move_y * left_share]
                    positions[right] = [rx + move_x * right_share, ry + move_y * right_share]
    # Let collision resolution finish after the spring integration.  This
    # guarantees the rendered footprints do not overlap even where several
    # links pull nodes into the same region.
    for _ in range(600):
        adjusted = False
        for left_index, left in enumerate(nodes):
            lx, ly = positions[left]
            for right in nodes[left_index + 1:]:
                rx, ry = positions[right]
                dx, dy = rx - lx, ry - ly
                distance = math.hypot(dx, dy)
                minimum = clearance_radius(left) + clearance_radius(right)
                if distance >= minimum:
                    continue
                adjusted = True
                if distance < 0.001:
                    angle = (left_index + 1) * 2.399963229728653
                    dx, dy, distance = math.cos(angle), math.sin(angle), 1.0
                correction = minimum - distance
                left_share = (1 / node_mass(left)) / (1 / node_mass(left) + 1 / node_mass(right))
                right_share = 1 - left_share
                move_x, move_y = dx / distance * correction, dy / distance * correction
                positions[left] = [lx - move_x * left_share, ly - move_y * left_share]
                positions[right] = [rx + move_x * right_share, ry + move_y * right_share]
        if not adjusted:
            break

    # Disconnected fabrics have no spring relationship, so give each component
    # a separate padded slot before the cosmetic crossing pass.  This retains
    # each component's settled shape without letting unrelated networks drift
    # arbitrarily far apart.
    adjacency: dict[str | DiagramUnit, set[str | DiagramUnit]] = {node: set() for node in nodes}
    for left, right, _, _, _, _ in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    components: list[list[str | DiagramUnit]] = []
    unseen = set(nodes)
    while unseen:
        root_node = min(unseen, key=lambda node: node.instance if isinstance(node, DiagramUnit) else node)
        component, pending = [], [root_node]
        unseen.remove(root_node)
        while pending:
            node = pending.pop()
            component.append(node)
            for neighbour in adjacency[node]:
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    pending.append(neighbour)
        components.append(component)
    if len(components) > 1:
        component_bounds = []
        for component in components:
            minimum_x = min(positions[node][0] - clearance_radius(node) for node in component)
            maximum_x = max(positions[node][0] + clearance_radius(node) for node in component)
            minimum_y = min(positions[node][1] - clearance_radius(node) for node in component)
            maximum_y = max(positions[node][1] + clearance_radius(node) for node in component)
            component_bounds.append((component, minimum_x, maximum_x, minimum_y, maximum_y))
        columns = math.ceil(math.sqrt(len(component_bounds)))
        cell_width = max(maximum_x - minimum_x for _, minimum_x, maximum_x, _, _ in component_bounds)
        cell_height = max(maximum_y - minimum_y for _, _, _, minimum_y, maximum_y in component_bounds)
        component_gap = 220.0
        for index, (component, minimum_x, _, minimum_y, _) in enumerate(component_bounds):
            row, column = divmod(index, columns)
            shift_x = column * (cell_width + component_gap) - minimum_x
            shift_y = row * (cell_height + component_gap) - minimum_y
            for node in component:
                positions[node][0] += shift_x
                positions[node][1] += shift_y

    def orient(first: str | DiagramUnit, second: str | DiagramUnit, third: str | DiagramUnit) -> float:
        ax, ay = positions[first]
        bx, by = positions[second]
        cx, cy = positions[third]
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    def edges_cross(first: tuple[str | DiagramUnit, str | DiagramUnit, str, str, Endpoint | None, Endpoint | None], second: tuple[str | DiagramUnit, str | DiagramUnit, str, str, Endpoint | None, Endpoint | None]) -> bool:
        left, right, _, _, _, _ = first
        other_left, other_right, _, _, _, _ = second
        if {left, right} & {other_left, other_right}:
            return False
        first_left, first_right = orient(left, right, other_left), orient(left, right, other_right)
        second_left, second_right = orient(other_left, other_right, left), orient(other_left, other_right, right)
        return first_left * first_right < -0.001 and second_left * second_right < -0.001

    def crossing_count() -> int:
        return sum(edges_cross(first, second) for index, first in enumerate(edges) for second in edges[index + 1:])

    def crossing_cost() -> int:
        return sum(
            8 if first[2] == "direct-link" or second[2] == "direct-link" else 1
            for index, first in enumerate(edges)
            for second in edges[index + 1:]
            if edges_cross(first, second)
        )

    def direct_crossing_count() -> int:
        return sum(
            edges_cross(first, second)
            for index, first in enumerate(edges)
            for second in edges[index + 1:]
            if first[2] == "direct-link" or second[2] == "direct-link"
        )

    def clear_of_nodes(node: str | DiagramUnit, x: float, y: float) -> bool:
        return all(math.hypot(x - other_x, y - other_y) >= clearance_radius(node) + clearance_radius(other) for other, (other_x, other_y) in positions.items() if other != node)

    # A spring layout has no inherent opinion about edge crossings.  Make a
    # deterministic local search pass after it settles: candidates are accepted
    # only when they lower the global number of non-adjacent line crossings and
    # preserve the node-clearance invariant.
    crossing_offsets = (
        (-126, 0), (126, 0), (0, -126), (0, 126),
        (-90, -90), (90, -90), (-90, 90), (90, 90),
        (-84, 0), (84, 0), (0, -84), (0, 84),
        (-60, -60), (60, -60), (-60, 60), (60, 60),
        (-42, 0), (42, 0), (0, -42), (0, 42),
        (-30, -30), (30, -30), (-30, 30), (30, 30),
    )
    crossings_before = crossing_count()
    for pass_index in range(48):
        improved = False
        for offset in range(len(nodes)):
            node = nodes[(offset + pass_index) % len(nodes)]
            original = positions[node]
            best = crossing_cost()
            best_direct_crossings = direct_crossing_count()
            best_position = original
            for dx, dy in crossing_offsets:
                candidate = [original[0] + dx, original[1] + dy]
                if not clear_of_nodes(node, candidate[0], candidate[1]):
                    continue
                positions[node] = candidate
                candidate_cost = crossing_cost()
                candidate_direct_crossings = direct_crossing_count()
                if candidate_direct_crossings < best_direct_crossings or (candidate_direct_crossings == best_direct_crossings and candidate_cost < best):
                    best, best_direct_crossings, best_position = candidate_cost, candidate_direct_crossings, candidate
            positions[node] = best_position
            improved |= best_position != original
        if not improved:
            break
    crossings = crossing_count()

    # The simulation has no artificial walls.  Fit its final extents into an
    # SVG viewport only after all forces and local crossing moves are complete.
    outer_padding = 125.0
    minimum_x = min(positions[node][0] - clearance_radius(node) for node in nodes)
    maximum_x = max(positions[node][0] + clearance_radius(node) for node in nodes)
    minimum_y = min(positions[node][1] - clearance_radius(node) for node in nodes)
    maximum_y = max(positions[node][1] + clearance_radius(node) for node in nodes)
    shift_x, shift_y = outer_padding - minimum_x, outer_padding - minimum_y
    for node in nodes:
        positions[node][0] += shift_x
        positions[node][1] += shift_y
    width = math.ceil(maximum_x - minimum_x + outer_padding * 2)
    network_height = math.ceil(maximum_y - minimum_y + outer_padding * 2)

    # Ports leave circular units in the direction of their neighbour.  Ports
    # that share an octant receive a small deterministic fan-out.
    octant_ports: dict[tuple[DiagramUnit, int], list[Endpoint]] = {}
    for endpoint, peer in port_peers.items():
        unit = endpoint_units[endpoint]
        x, y = positions[unit]
        peer_x, peer_y = positions[peer]
        if endpoint in direct_endpoints:
            # A reciprocal direct pair represents one physical route.  Give
            # both ports its exact bearing rather than fanning them apart.
            port_angles[endpoint] = math.atan2(peer_y - y, peer_x - x)
            continue
        octant = round(math.atan2(peer_y - y, peer_x - x) / (math.pi / 4)) % 8
        octant_ports.setdefault((unit, octant), []).append(endpoint)
    for (unit, octant), ports in octant_ports.items():
        ports.sort(key=lambda endpoint: (math.atan2(positions[port_peers[endpoint]][1] - positions[unit][1], positions[port_peers[endpoint]][0] - positions[unit][0]), endpoint.text))
        # Ports leave at the link's true direction.  Only ports competing for
        # the same broad sector are fanned out; snapping their boundary points
        # to the sector itself can create a crossing that is absent from the
        # underlying node layout.
        direction_x = sum(math.cos(math.atan2(positions[port_peers[endpoint]][1] - positions[unit][1], positions[port_peers[endpoint]][0] - positions[unit][0])) for endpoint in ports)
        direction_y = sum(math.sin(math.atan2(positions[port_peers[endpoint]][1] - positions[unit][1], positions[port_peers[endpoint]][0] - positions[unit][0])) for endpoint in ports)
        base = math.atan2(direction_y, direction_x)
        for index, endpoint in enumerate(ports):
            port_angles[endpoint] = base + (index - (len(ports) - 1) / 2) * 0.13

    def port_label(endpoint: Endpoint) -> str:
        prefix = "out" if endpoint.direction == "source" else "in"
        name = endpoint.name.port if isinstance(endpoint.name, DestinationInput) else f"{endpoint.name.port}.{endpoint.name.handle}"
        return f"{prefix} {name}"

    def label_bounds(x: float, y: float, text: str, anchor: str) -> tuple[float, float, float, float]:
        width = len(text) * 6.1
        left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
        return left, y - 11, left + width, y + 3

    port_label_positions: dict[Endpoint, tuple[float, float, str]] = {}
    for unit in units:
        x, y = positions[unit]
        occupied: list[tuple[float, float, float, float]] = []
        for endpoint in sorted(unit_ports[unit]["destination"] + unit_ports[unit]["source"], key=lambda endpoint: port_angles[endpoint]):
            angle, text = port_angles[endpoint], port_label(endpoint)
            anchor = "start" if math.cos(angle) > 0.25 else "end" if math.cos(angle) < -0.25 else "middle"
            for extra in range(14, 115, 10):
                label_x = x + (unit_radius[unit] + extra) * math.cos(angle)
                label_y = y + (unit_radius[unit] + extra) * math.sin(angle) + 4
                bounds = label_bounds(label_x, label_y, text, anchor)
                if not any(bounds[0] < other[2] and other[0] < bounds[2] and bounds[1] < other[3] and other[1] < bounds[3] for other in occupied):
                    break
            occupied.append(bounds)
            port_label_positions[endpoint] = label_x, label_y, anchor
    # Per-unit spacing is not enough when neighbouring circles are close.
    # Nudge colliding labels further along their own radial direction until the
    # complete diagram's port names have clear bounding boxes.
    for _ in range(8):
        moved = False
        for endpoint in sorted(port_label_positions, key=lambda endpoint: endpoint.text):
            unit = endpoint_units[endpoint]
            x, y, anchor = port_label_positions[endpoint]
            text = port_label(endpoint)
            others = [
                label_bounds(other_x, other_y, port_label(other), other_anchor)
                for other, (other_x, other_y, other_anchor) in port_label_positions.items()
                if other != endpoint
            ]
            bounds = label_bounds(x, y, text, anchor)
            while any(bounds[0] < other[2] and other[0] < bounds[2] and bounds[1] < other[3] and other[1] < bounds[3] for other in others):
                x += 12 * math.cos(port_angles[endpoint])
                y += 12 * math.sin(port_angles[endpoint])
                bounds = label_bounds(x, y, text, anchor)
                moved = True
            port_label_positions[endpoint] = x, y, anchor
        if not moved:
            break

    def wire_points(edge: tuple[str | DiagramUnit, str | DiagramUnit, str, str, Endpoint | None, Endpoint | None]) -> list[tuple[float, float]]:
        left, right, kind, _, left_endpoint, right_endpoint = edge
        x1, y1 = edge_position(left, left_endpoint, right)
        x2, y2 = edge_position(right, right_endpoint, left)
        return [(x1, y1), (x2, y2)]

    def segments_cross(first_start: tuple[float, float], first_end: tuple[float, float], second_start: tuple[float, float], second_end: tuple[float, float]) -> bool:
        orientation = lambda start, end, point: (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])
        return orientation(first_start, first_end, second_start) * orientation(first_start, first_end, second_end) < -0.001 and orientation(second_start, second_end, first_start) * orientation(second_start, second_end, first_end) < -0.001

    def rendered_edges_cross(first: tuple[str | DiagramUnit, str | DiagramUnit, str, str, Endpoint | None, Endpoint | None], second: tuple[str | DiagramUnit, str | DiagramUnit, str, str, Endpoint | None, Endpoint | None]) -> bool:
        # Fan-out at a shared router or unit is not a graph crossing, even if
        # two independently drawn port stubs happen to overlap near that node.
        if {first[0], first[1]} & {second[0], second[1]}:
            return False
        first_points, second_points = wire_points(first), wire_points(second)
        return any(segments_cross(first_start, first_end, second_start, second_end) for first_start, first_end in zip(first_points, first_points[1:]) for second_start, second_end in zip(second_points, second_points[1:]))

    direct_crossings = sum(
        rendered_edges_cross(first, second)
        for index, first in enumerate(edges)
        for second in edges[index + 1:]
        if first[2] == "direct-link" or second[2] == "direct-link"
    )

    legend_y = network_height + 45
    diagram_height = legend_y + 32 + len(fabric.connections) * 20
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{diagram_height}" viewBox="0 0 {width} {diagram_height}" role="img" aria-labelledby="title desc" data-layout="tree-spring" data-crossings-before="{crossings_before}" data-crossings="{crossings}" data-direct-crossings="{direct_crossings}">',
        f'  <title id="title">Pigen routing network: {_svg_text(fabric.name)}</title>',
        '  <desc id="desc">Elaborated source-routed fabric topology, seeded from its router tree and refined with angular snap springs, unit out-pull, and padded collision resolution. Blue links are hard direct connections.</desc>',
        '  <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#52606d"/></marker></defs>',
        '  <style>text{font-family:ui-monospace,monospace;font-size:13px;fill:#172033}.edge{stroke:#52606d;stroke-width:2.2;fill:none}.direct{stroke:#1479c9;stroke-width:3.4}.port{font-size:11px;fill:#52606d;paint-order:stroke;stroke:#fff;stroke-width:3px}.router{fill:#fff4d6;stroke:#a05a00;stroke-width:2}.unit{fill:#dbeafe;stroke:#1d4ed8;stroke-width:2.5}.label{font-size:12px}.legend{font-size:12px}</style>',
        f'  <text x="24" y="30" font-size="18">fabric {_svg_text(fabric.name)} — elaborated routing network</text>',
    ]

    direct_directions = {(endpoint_units[endpoint_for_source(connection.source)], endpoint_units[endpoint_for_destination(connection.destination)]) for connection in direct_connections}
    for index, edge in enumerate(edges):
        left, right, kind, label, left_endpoint, right_endpoint = edge
        points = wire_points(edge)
        label_x = (points[0][0] + points[-1][0]) / 2
        label_y = (points[0][1] + points[-1][1]) / 2 - 5
        attributes = f'data-kind="{kind}" data-from="{_svg_text(left.instance if isinstance(left, DiagramUnit) else left)}" data-to="{_svg_text(right.instance if isinstance(right, DiagramUnit) else right)}"'
        if kind == "direct-link":
            assert left_endpoint is not None and right_endpoint is not None
            attributes += f' data-source="{_svg_text(left_endpoint.name.text)}" data-destination="{_svg_text(right_endpoint.name.text)}"'
        has_arrow = kind == "endpoint-link" or (kind == "direct-link" and (right, left) not in direct_directions)
        if has_arrow:
            attributes += ' marker-end="url(#arrow)"'
        lines += [
            f'  <line class="edge {"direct" if kind == "direct-link" else ""}" {attributes} x1="{points[0][0]:.3f}" y1="{points[0][1]:.3f}" x2="{points[-1][0]:.3f}" y2="{points[-1][1]:.3f}"/>',
            f'  <text class="port" x="{label_x:.3f}" y="{label_y:.3f}" text-anchor="middle">{_svg_text(label)}</text>',
        ]

    for unit in units:
        x, y = positions[unit]
        radius = unit_radius[unit]
        lines += [
            f'  <circle class="unit" data-kind="unit" data-unit="{_svg_text(unit.instance)}" cx="{x:.3f}" cy="{y:.3f}" r="{radius:.3f}"/>',
            f'  <text class="label" x="{x:.3f}" y="{y + 5:.3f}" text-anchor="middle">{_svg_text(unit.instance)}</text>',
        ]
        for endpoint in unit_ports[unit]["destination"] + unit_ports[unit]["source"]:
            port_x, port_y = port_position(unit, endpoint)
            label_x, label_y, text_anchor = port_label_positions[endpoint]
            lines += [
                f'  <circle data-kind="{endpoint.direction}-port" data-endpoint="{_svg_text(endpoint.text)}" cx="{port_x:.3f}" cy="{port_y:.3f}" r="4" fill="#1d4ed8"/>',
                f'  <text class="port" data-endpoint="{_svg_text(endpoint.text)}" x="{label_x:.3f}" y="{label_y:.3f}" text-anchor="{text_anchor}">{_svg_text(port_label(endpoint))}</text>',
            ]
    for name in router_names:
        x, y = positions[name]
        lines += [
            f'  <circle class="router" data-kind="router" data-router="{_svg_text(name)}" cx="{x:.3f}" cy="{y:.3f}" r="25"/>',
            f'  <text x="{x:.3f}" y="{y + 5:.3f}" text-anchor="middle">{_svg_text(name)}</text>',
        ]

    lines.append(f'  <text class="legend" x="24" y="{legend_y}">connections:</text>')
    for index, connection in enumerate(sorted(fabric.connections, key=lambda item: (item.source.text, item.destination.text))):
        if connection.direct:
            detail = "direct"
        else:
            route = topology.routes[connection]
            detail = f"hops={route.hops} path=0b{route.path_word:0{topology.path_width}b}"
        lines.append(f'  <text class="legend" x="45" y="{legend_y + 20 * (index + 1)}">{_svg_text(connection.source.text)} {_svg_text(connection.arrow)} {_svg_text(connection.destination.text)} ({detail})</text>')
    lines.append('</svg>')
    return "\n".join(lines) + "\n"


def generate_fabric(fabric: Fabric) -> tuple[str, str, Topology]:
    topology = build_topology(fabric)
    return render_fabric(fabric, topology), render_manifest(fabric, topology), topology
