#!/usr/bin/env python3
"""Generate a self-contained ready/valid SystemVerilog pipeline from a .pipe file."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


if __name__ == "__main__":
    # Fabric support imports this module for shared parser utilities.  Keep the
    # script invocation and import invocation on the same exception class.
    sys.modules.setdefault("pigen", sys.modules[__name__])


IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class PigenError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass
class SourceLine:
    text: str
    raw: str
    number: int


@dataclass
class Signal:
    name: str
    upper: str
    lower: str
    signed: bool
    base: str
    line: int

    @property
    def width(self) -> str:
        if self.lower == "0":
            if self.upper.isdecimal():
                return str(int(self.upper) + 1)
            match = re.fullmatch(r"(.+?)\s*-\s*1", self.upper)
            if match:
                return match.group(1).strip()
        return f"({self.upper}) - ({self.lower}) + 1"

    @property
    def type_text(self) -> str:
        return f"{self.base}{' signed' if self.signed else ''} [{self.upper}:{self.lower}]"


@dataclass
class HeaderItem:
    name: str
    signal: Signal | None
    line: int


@dataclass
class Declaration:
    signal: Signal
    initializer: str | None


@dataclass
class Stage:
    name: str
    inputs: list[Signal]
    yielded: list[Signal]
    declarations: list[Declaration]
    statements: list[str]
    force_skid: bool
    suppress_skid: bool
    line: int


@dataclass
class Pipeline:
    name: str
    params: list[tuple[str, str, int]]
    stages: list[Stage]

    @property
    def inputs(self) -> list[Signal]:
        return self.stages[0].inputs

    @property
    def outputs(self) -> list[Signal]:
        return self.stages[-1].yielded


def source_lines(text: str) -> list[SourceLine]:
    lines: list[SourceLine] = []
    for number, raw in enumerate(text.splitlines(), 1):
        visible = raw.split("//", 1)[0].strip()
        if not visible:
            continue
        lines.append(SourceLine(visible, raw.strip(), number))
    return lines


class Parser:
    def __init__(self, text: str):
        self.lines = source_lines(text)
        self.i = 0

    def current(self) -> SourceLine:
        if self.i >= len(self.lines):
            raise PigenError(self.lines[-1].number if self.lines else 1, "unexpected end of file")
        return self.lines[self.i]

    def parse(self) -> Pipeline:
        if not self.lines:
            raise PigenError(1, "file is empty")
        head = self.current()
        match = re.fullmatch(r"pipeline\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(#\(|begin)", head.text)
        if not match:
            raise PigenError(head.number, "expected `pipeline NAME begin` or `pipeline NAME #(`")
        name, opening = match.groups()
        self.i += 1
        params: list[tuple[str, str, int]] = []
        if opening == "#(":
            params = self.parse_parameters()
        stages: list[Stage] = []
        names: set[str] = set()
        previous: list[Signal] | None = None
        while self.current().text != "endpipeline":
            stage = self.parse_stage(previous)
            if stage.name in names:
                raise PigenError(stage.line, f"duplicate stage `{stage.name}`")
            names.add(stage.name)
            stages.append(stage)
            previous = stage.yielded
        self.i += 1
        if self.i != len(self.lines):
            raise PigenError(self.current().number, "content after endpipeline")
        if not stages:
            raise PigenError(head.number, "pipeline requires at least one stage")
        pipe = Pipeline(name, params, stages)
        self.validate(pipe)
        return pipe

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

    def parse_stage(self, inherited_inputs: list[Signal] | None) -> Stage:
        first = self.current()
        if not first.text.startswith("stage "):
            raise PigenError(first.number, "expected stage declaration or endpipeline")
        inline = re.fullmatch(r"stage\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\{(.*)\}\s+yields\s+\{(.*)\}\s+begin\s*(.*?)\s*endstage", first.text)
        if inline:
            name, inputs, outputs, body_text = inline.groups()
            self.i += 1
            fragments = [fragment.strip() + ";" for fragment in body_text.split(";") if fragment.strip()]
            body = [SourceLine(fragment, fragment, first.number) for fragment in fragments]
            return self.build_stage(name, inputs, outputs, body, False, False, first.number, inherited_inputs)
        header_parts: list[str] = []
        header_line = first.number
        while True:
            line = self.current()
            header_parts.append(line.text)
            self.i += 1
            header = " ".join(header_parts)
            match = re.fullmatch(r"stage\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\{(.*)\}\s+yields\s+\{(.*)\}\s+begin", header)
            if match:
                name, inputs, outputs = match.groups()
                break
            if line.text.endswith("begin"):
                raise PigenError(line.number, "expected `stage NAME {inputs} yields {outputs} begin`")
            if self.i >= len(self.lines):
                raise PigenError(line.number, "unterminated stage header")
        body: list[SourceLine] = []
        force_skid = suppress_skid = False
        while True:
            line = self.current()
            self.i += 1
            if line.text == "endstage":
                break
            if line.text == "skid":
                force_skid = True
            elif line.text == "no_skid":
                suppress_skid = True
            else:
                body.append(line)
        if force_skid and suppress_skid:
            raise PigenError(header_line, "stage cannot contain both skid and no_skid")
        return self.build_stage(name, inputs, outputs, body, force_skid, suppress_skid, header_line, inherited_inputs)

    @staticmethod
    def build_stage(name: str, inputs: str, outputs: str, body: list[SourceLine], force_skid: bool, suppress_skid: bool, line: int, inherited_inputs: list[Signal] | None) -> Stage:
        input_items = parse_tuple(inputs, line)
        output_items = parse_tuple(outputs, line)
        if not input_items or not output_items:
            raise PigenError(line, "stage input and yielded tuples must not be empty")
        declarations, statements = parse_body(body)
        inputs_resolved, outputs_resolved, declarations = resolve_stage_items(input_items, output_items, declarations, inherited_inputs)
        return Stage(name, inputs_resolved, outputs_resolved, declarations, statements, force_skid, suppress_skid, line)

    @staticmethod
    def validate(pipe: Pipeline) -> None:
        for current, following in zip(pipe.stages, pipe.stages[1:]):
            validate_tuple(current.yielded, following.inputs, following.line, f"yield from `{current.name}`", f"inputs of `{following.name}`")


def parse_signal(text: str, line: int) -> Signal:
    match = re.fullmatch(r"(?:(logic|wire)\s+)?(?:(signed|unsigned)\s+)?\[\s*(.+?)\s*:\s*(.+?)\s*\]\s+([A-Za-z_][A-Za-z0-9_$]*)", text.strip())
    if not match:
        raise PigenError(line, "expected a packed declaration such as `logic signed [W-1:0] sample`")
    base, sign, upper, lower, name = match.groups()
    return Signal(name, upper.strip(), lower.strip(), sign == "signed", base or "logic", line)


def parse_tuple(text: str, line: int) -> list[HeaderItem]:
    items = [item.strip() for item in text.split(",") if item.strip()]
    result: list[HeaderItem] = []
    names: set[str] = set()
    for item in items:
        if IDENT.fullmatch(item):
            header = HeaderItem(item, None, line)
        else:
            signal = parse_signal(item, line)
            header = HeaderItem(signal.name, signal, line)
        if header.name in names:
            raise PigenError(line, f"duplicate tuple value `{header.name}`")
        names.add(header.name)
        result.append(header)
    return result


def parse_declaration(line: SourceLine) -> Declaration | None:
    match = re.fullmatch(r"(?:(logic|wire)\s+)?(?:(signed|unsigned)\s+)?\[\s*(.+?)\s*:\s*(.+?)\s*\]\s+([A-Za-z_][A-Za-z0-9_$]*)(?:\s*=\s*(.+))?;", line.text)
    if not match:
        return None
    base, sign, upper, lower, name, initializer = match.groups()
    return Declaration(Signal(name, upper.strip(), lower.strip(), sign == "signed", base or "logic", line.number), initializer.strip() if initializer else None)


def parse_body(lines: list[SourceLine]) -> tuple[list[Declaration], list[str]]:
    declarations: list[Declaration] = []
    statements: list[str] = []
    names: set[str] = set()
    for line in lines:
        declaration = parse_declaration(line)
        if declaration:
            if declaration.signal.name in names:
                raise PigenError(line.number, f"duplicate local declaration `{declaration.signal.name}`")
            names.add(declaration.signal.name)
            declarations.append(declaration)
        else:
            statements.append(line.raw)
    return declarations, statements


def resolve_stage_items(inputs: list[HeaderItem], outputs: list[HeaderItem], declarations: list[Declaration], inherited_inputs: list[Signal] | None) -> tuple[list[Signal], list[Signal], list[Declaration]]:
    declared = {declaration.signal.name: declaration for declaration in declarations}
    typed = {item.name: item.signal for item in inputs + outputs if item.signal}
    for name in typed:
        if name in declared:
            raise PigenError(declared[name].signal.line, f"`{name}` is typed in the stage header and must not be redeclared in the body")
    resolved: dict[str, Signal] = {name: signal for name, signal in typed.items() if signal}
    for index, item in enumerate(inputs):
        if item.name in resolved:
            continue
        declaration = declared.get(item.name)
        if declaration:
            resolved[item.name] = declaration.signal
        elif inherited_inputs and index < len(inherited_inputs):
            previous = inherited_inputs[index]
            resolved[item.name] = Signal(item.name, previous.upper, previous.lower, previous.signed, previous.base, item.line)
        else:
            raise PigenError(item.line, f"bare input `{item.name}` requires a declaration in the first stage body")
    for item in outputs:
        if item.name in resolved:
            continue
        declaration = declared.get(item.name)
        if not declaration:
            raise PigenError(item.line, f"bare yielded value `{item.name}` requires a declaration or matching input")
        resolved[item.name] = declaration.signal
    input_names = {item.name for item in inputs}
    for name in input_names:
        declaration = declared.get(name)
        if declaration and declaration.initializer:
            raise PigenError(declaration.signal.line, f"stage input `{name}` cannot have an initializer")
    # Header-typed outputs are real local signals even when they have no body declaration.
    for item in outputs:
        if item.signal and item.name not in input_names:
            declarations.append(Declaration(item.signal, None))
    # Inputs are emitted from the packed bus, not as duplicate module declarations.
    declarations = [declaration for declaration in declarations if declaration.signal.name not in input_names]
    return [resolved[item.name] for item in inputs], [resolved[item.name] for item in outputs], declarations


def width_sum(signals: list[Signal]) -> str:
    return " + ".join(f"({signal.width})" for signal in signals)


def width_key(signal: Signal) -> str:
    return re.sub(r"\s+", "", signal.width)


def validate_tuple(producer: list[Signal], consumer: list[Signal], line: int, producer_name: str, consumer_name: str) -> None:
    if len(producer) != len(consumer):
        raise PigenError(line, f"{producer_name} has {len(producer)} value(s), but {consumer_name} has {len(consumer)}")
    for index, (produced, consumed) in enumerate(zip(producer, consumer), 1):
        if width_key(produced) != width_key(consumed):
            raise PigenError(line, f"tuple value {index} width mismatch: {producer_name} yields {produced.width}, {consumer_name} expects {consumed.width}")


def parameter_block(pipe: Pipeline) -> str:
    if not pipe.params:
        return ""
    return " #(\n" + ",\n".join(f"\tparameter integer {name} = {default}" for name, default, _ in pipe.params) + "\n)"


def port_header(in_width: str, out_width: str) -> list[str]:
    return ["\t(", "\t\tinput  logic clk,", "\t\tinput  logic reset,", "", "\t\tinput  logic enable,", "", "\t\tinput  logic in_valid,", "\t\toutput logic in_ready,", "", "\t\toutput logic out_valid,", "\t\tinput  logic out_ready,", "", f"\t\tinput  logic [{in_width}-1:0] packet_in,", f"\t\toutput logic [{out_width}-1:0] packet_out", "\t);"]


def render_stage(pipe: Pipeline, stage: Stage) -> str:
    in_width, out_width = width_sum(stage.inputs), width_sum(stage.yielded)
    lines = [f"module {pipe.name}__stage_{stage.name}{parameter_block(pipe)}"] + port_header(in_width, out_width) + [""]
    for index, signal in enumerate(stage.inputs):
        following = stage.inputs[index + 1:]
        offset = width_sum(following) if following else "0"
        lines += [f"\t{signal.type_text} {signal.name};", f"\tassign {signal.name} = packet_in[{offset} +: ({signal.width})];"]
    if stage.inputs:
        lines.append("")
    initializers: list[str] = []
    for declaration in stage.declarations:
        signal = declaration.signal
        if signal.base == "wire" and declaration.initializer:
            lines.append(f"\t{signal.type_text} {signal.name} = {declaration.initializer};")
        else:
            lines.append(f"\t{signal.type_text} {signal.name};")
            if declaration.initializer:
                initializers.append(f"{signal.name} = {declaration.initializer};")
    if stage.declarations:
        lines.append("")
    lines += [f"\tlogic [{out_width}-1:0] packet_comb;", "", "\tassign in_ready = ~out_valid | out_ready;", "", "\twire take_in = in_ready & in_valid;", "\twire take_out = out_valid & out_ready;", "", "\talways_comb begin"]
    lines += ["\t\t" + statement for statement in initializers + stage.statements]
    lines += ["\t\tpacket_comb = {" + ", ".join(signal.name for signal in stage.yielded) + "};", "\tend", "", "\talways_ff @(posedge clk) begin", "\t\tif (reset) begin", "\t\t\tout_valid <= 1'b0;", "\t\t\tpacket_out <= '0;", "\t\tend else if (enable) begin", "\t\t\tif (take_in) begin", "\t\t\t\tpacket_out <= packet_comb;", "\t\t\t\tout_valid <= 1'b1;", "\t\t\tend else if (take_out) begin", "\t\t\t\tout_valid <= 1'b0;", "\t\t\tend", "\t\tend", "\tend", "endmodule", ""]
    return "\n".join(lines)


def render_skid(pipe: Pipeline) -> str:
    return f'''module {pipe.name}__skid #(parameter integer PACKET_WIDTH = 1)
	(
		input  logic clk,
		input  logic reset,

		input  logic enable,

		input  logic in_valid,
		output logic in_ready,

		output logic out_valid,
		input  logic out_ready,

		input  logic [PACKET_WIDTH-1:0] packet_in,
		output logic [PACKET_WIDTH-1:0] packet_out
	);

	logic [PACKET_WIDTH-1:0] packet_skid;
	logic skid;

	assign in_ready = ~(out_valid & skid);

	wire take_in = in_ready & in_valid;
	wire take_out = out_valid & out_ready;

	always_ff @(posedge clk) begin
		if (reset) begin
			skid <= 1'b0;
			out_valid <= 1'b0;
			packet_out <= '0;
		end else if (enable) begin
			case ({{take_in, take_out}})
				2'b00: begin

				end

				2'b01: begin
					if (skid) begin
						packet_out <= packet_skid;
						skid <= 1'b0;
					end else begin
						out_valid <= 1'b0;
					end
				end

				2'b10: begin
					if (out_valid) begin
						packet_skid <= packet_in;
						skid <= 1'b1;
					end else begin
						packet_out <= packet_in;
						out_valid <= 1'b1;
					end
				end

				2'b11: begin
					if (skid) begin
						packet_out <= packet_skid;
						packet_skid <= packet_in;
					end else begin
						packet_out <= packet_in;
					end
				end
			endcase
		end
	end
endmodule
'''


def skid_boundaries(pipe: Pipeline, skid_step: int) -> set[int]:
    selected: set[int] = set()
    for index, stage in enumerate(pipe.stages, 1):
        periodic = skid_step > 0 and index % skid_step == 0
        if (periodic and not stage.suppress_skid) or stage.force_skid:
            selected.add(index)
    return selected


def render_top(pipe: Pipeline, skid_step: int) -> str:
    components: list[tuple[str, Stage | int, list[Signal]]] = []
    for index, stage in enumerate(pipe.stages, 1):
        components.append(("stage", stage, stage.yielded))
        if index in skid_boundaries(pipe, skid_step):
            components.append(("skid", index, stage.yielded))
    lines = [f"module {pipe.name}{parameter_block(pipe)}"] + port_header(width_sum(pipe.inputs), width_sum(pipe.outputs)) + [""]
    for index, (_, _, payload) in enumerate(components):
        lines += [f"\twire c{index}_valid, c{index}_ready;", f"\twire [{width_sum(payload)}-1:0] c{index}_packet;"]
    lines.append("")
    for index, (kind, object_, payload) in enumerate(components):
        in_valid = "in_valid" if index == 0 else f"c{index - 1}_valid"
        in_ready = "in_ready" if index == 0 else f"c{index - 1}_ready"
        packet_in = "packet_in" if index == 0 else f"c{index - 1}_packet"
        out_ready = "out_ready" if index == len(components) - 1 else f"c{index}_ready"
        if kind == "stage":
            stage = object_
            assert isinstance(stage, Stage)
            params = " #(" + ", ".join(f".{name}({name})" for name, _, _ in pipe.params) + ")" if pipe.params else ""
            module, instance = f"{pipe.name}__stage_{stage.name}{params}", f"u_{stage.name}"
        else:
            module, instance = f"{pipe.name}__skid #(.PACKET_WIDTH({width_sum(payload)}))", f"u_skid_{object_}"
        lines.append(f"\t{module} {instance} (.clk(clk), .reset(reset), .enable(enable), .in_valid({in_valid}), .in_ready({in_ready}), .out_valid(c{index}_valid), .out_ready({out_ready}), .packet_in({packet_in}), .packet_out(c{index}_packet));")
    lines += ["", f"\tassign out_valid = c{len(components)-1}_valid;", f"\tassign packet_out = c{len(components)-1}_packet;", "endmodule", ""]
    return "\n".join(lines)


def parse_pipe(text: str) -> Pipeline:
    return Parser(text).parse()


def generate(pipe: Pipeline, skid_step: int = 4) -> str:
    if skid_step < 0:
        raise ValueError("skid step must be non-negative")
    return "\n".join(["`timescale 1ns/1ps", "// Generated by pigen.py; do not edit.", render_skid(pipe), *(render_stage(pipe, stage) for stage in pipe.stages), render_top(pipe, skid_step)])


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    explicit_fabric = bool(raw_argv and raw_argv[0] == "fabric")
    if explicit_fabric:
        raw_argv.pop(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument("--skid-step", "--skid_step", type=int, default=4, dest="skid_step")
    parser.add_argument("--module", dest="module_name")
    parser.add_argument("--manifest", type=Path, help="fabric route manifest output path")
    diagram_group = parser.add_mutually_exclusive_group()
    diagram_group.add_argument("--diagram", type=Path, help="fabric SVG diagram output path")
    diagram_group.add_argument("--no-diagram", action="store_true", help="do not emit the default fabric SVG diagram")
    args = parser.parse_args(raw_argv)
    try:
        source_text = args.source.read_text()
        fabric_mode = explicit_fabric or bool(re.match(r"\s*fabric\b", source_text))
        if fabric_mode:
            from fabric import generate_fabric, parse_fabric, render_diagram

            design = parse_fabric(source_text)
            if args.module_name:
                if not IDENT.fullmatch(args.module_name):
                    raise PigenError(1, "--module must be a valid SystemVerilog identifier")
                design.name = args.module_name
            output, manifest, topology = generate_fabric(design)
            args.output.write_text(output)
            manifest_path = args.manifest or args.output.with_suffix(args.output.suffix + ".routes")
            manifest_path.write_text(manifest)
            if not args.no_diagram:
                diagram_path = args.diagram or args.output.with_suffix(args.output.suffix + ".svg")
                diagram_path.write_text(render_diagram(design, topology))
            return 0
        if args.diagram or args.no_diagram:
            raise PigenError(1, "--diagram and --no-diagram are only valid for fabrics")
        pipe = parse_pipe(source_text)
        if args.module_name:
            if not IDENT.fullmatch(args.module_name):
                raise PigenError(1, "--module must be a valid SystemVerilog identifier")
            pipe.name = args.module_name
        args.output.write_text(generate(pipe, args.skid_step))
    except (OSError, PigenError, ValueError) as exc:
        print(f"pigen: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
