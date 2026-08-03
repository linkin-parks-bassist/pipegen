# pipegen

## Attribution

Pipegen was directed by its author, who supplied the ideas, language direction,
iterative review, and the original base SystemVerilog templates. OpenAI Codex
wrote the Python implementation, examples, tests, and documentation. It is
shared as AI-generated work under human direction; the author does not claim
to have written the generated code.

`pipegen.py` generates a self-contained SystemVerilog ready/valid pipeline from
a small stage-oriented source language. Every stage declares its own packed
input tuple, local SystemVerilog signals, and packed output tuple. The first
stage defines top-level `packet_in`; the final stage defines `packet_out`.

```sh
./pipegen.py --skid-step=4 filter.pipe -o filter.sv
```

`--skid_step` is an alias. The default periodic skid interval is four stages;
use `--skid-step=0` to disable periodic insertion. `--module NAME` overrides
the declared pipeline name.

## Source form

```pipe
pipeline add_one #(
    parameter integer W = 16
) begin

    stage add {x} yields {result} begin

        logic [W-1:0] x;
        logic [W-1:0] result = x + 1;

    endstage

endpipeline
```

The body of a stage is ordinary combinational SystemVerilog. Write and lay it
out like Verilog; indentation and blank lines carry no meaning. The braces name
the packed values entering and leaving a stage, ordered from MSB to LSB.
Types may either live in the tuple or appear as normal declarations in the
stage body:

```pipe
stage add {
    logic [W-1:0] sample,
    logic [7:0] flags
} yields {
    logic [W-1:0] result,
    flags
} begin
    result = sample + 1;
endstage
```

A less trivial, but still simple, example would be a MAC pipeline:


```pipe
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
