# Pigen

## Attribution

Pigen was directed by its author, who supplied the ideas, language direction,
iterative review, and the original base SystemVerilog templates. OpenAI Codex
wrote the Python implementation, examples, tests, and documentation. It is
shared as AI-generated work under human direction; the author does not claim
to have written the generated code.

Pigen is a small source-to-SystemVerilog compiler for pleasant, local RTL
construction. Like its namesake, it carries data exactly where it needs to go
while keeping the journey out of the way: through locally described pipelines
or across declaratively connected fabrics. Its current pipeline pass generates
a self-contained ready/valid pipeline from a stage-oriented source language.
The blind-fabric subsystem uses the same principles for generated packet
interconnects; see [the fabric draft](docs/fabric.md) and its
[implementation plan](docs/fabric_implementation_plan.md).

```sh
./pigen.py --skid-step=4 filter.pipe -o filter.sv
```

`--skid_step` is an alias. The default periodic skid interval is four stages;
use `--skid-step=0` to disable periodic insertion. `--module NAME` overrides
the declared pipeline name.

## Fabric v0

Pigen can also emit a blind, source-routed ready/valid fabric. Fabric paths,
router placement, and route manifests are generated; the source names only the
connections and their directness priority.

```sh
./pigen.py fabric examples/fabric_demo.fabric -o fabric_demo.sv
```

This writes `fabric_demo.sv`, `fabric_demo.sv.routes`, and an SVG diagram at
`fabric_demo.sv.svg`. The first backend implements hard direct links plus a
deterministic three-port routing tree for all routed links. Use `--diagram
PATH` for a different diagram path or `--no-diagram` to suppress it. Run the
supplied direct-and-routed example with `cd examples && make fabric`.

## Source form

```systemverilog
pipeline add_one begin
    stage add {logic [7:0] x} yields {logic [7:0] result} begin
        result = x + 1;
    endstage
endpipeline
```

A less trivial, but still simple, example would be a MAC pipeline:


```systemverilog
pipeline mac #(
    parameter integer W = 16
) begin

    stage M {m, x, b} yields {mx, b} begin

        logic [W-1:0] m;
        logic [W-1:0] x;
        logic [W-1:0] b;

        logic [2*W-1:0] mx = m * x;

    endstage

    stage AC {mx, b} yields {result} begin

        logic [2*W-1:0] mx_plus_b = mx + b;
        logic [W-1:0] result = mx_plus_b[W-1:0];

    endstage

endpipeline
```

The same tiny pipeline, written by hand in vanilla SystemVerilog, is already
mostly payload plumbing and ready/valid bookkeeping. This is equivalent to the
example above and deliberately has no skid buffer:

```systemverilog
module mac_manual #(
    parameter integer W = 16
) (
    input  logic             clk,
    input  logic             reset,
    input  logic             enable,

    input  logic             in_valid,
    output logic             in_ready,
    output logic             out_valid,
    input  logic             out_ready,

    input  logic [3*W-1:0]   packet_in,
    output logic [W-1:0]     packet_out
);

    logic [W-1:0] m;
    logic [W-1:0] x;
    logic [W-1:0] b;
    assign {m, x, b} = packet_in;

    logic             multiply_valid;
    logic             multiply_ready;
    logic [2*W-1:0]   multiply_mx;
    logic [W-1:0]     multiply_b;

    logic             accumulate_ready;
    logic [W-1:0]     result;
    assign packet_out = result;

    assign accumulate_ready = ~out_valid | out_ready;
    assign multiply_ready = accumulate_ready;
    assign in_ready = ~multiply_valid | multiply_ready;

    wire take_multiply_in = in_valid & in_ready;
    wire take_multiply_out = multiply_valid & multiply_ready;
    wire take_accumulate_in = multiply_valid & accumulate_ready;
    wire take_accumulate_out = out_valid & out_ready;

    always_ff @(posedge clk) begin
        if (reset) begin
            multiply_valid <= 1'b0;
        end else if (enable) begin
            if (take_multiply_in) begin
                multiply_mx <= m * x;
                multiply_b <= b;
                multiply_valid <= 1'b1;
            end else if (take_multiply_out) begin
                multiply_valid <= 1'b0;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (reset) begin
            out_valid <= 1'b0;
            result <= '0;
        end else if (enable) begin
            if (take_accumulate_in) begin
                result <= multiply_mx + multiply_b;
                out_valid <= 1'b1;
            end else if (take_accumulate_out) begin
                out_valid <= 1'b0;
            end
        end
    end
endmodule
```

The first stage must give each incoming value a packed type. After that, carried
values inherit their preceding stage's type positionally, so a later stage only
needs to declare the values it creates. `logic` initializer declarations become
assignments in the generated combinational block; initialized `wire`s stay
continuous assignments. Add `skid` or `no_skid` inside a stage only when you
want to override the default skid-buffer spacing.

Run checks with:

```sh
python3 -m unittest -v
```
