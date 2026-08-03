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

    stage add {sample, flags} yields {result, flags_out} begin

        logic signed [W-1:0] sample;
        logic [7:0] flags;
        logic signed [W-1:0] result = sample + 1;
        logic [7:0] flags_out = flags;

    endstage

endpipeline
```

`stage NAME {inputs} yields {outputs} begin` and `endstage` delimit a stage;
indentation and blank lines are entirely cosmetic. Tuple order is packing order:
the leftmost item is the MSB side of the packed payload.

For compact stages, the whole declaration can also be written on one line:

```pipe
stage increment {x} yields {y} begin logic [7:0] x; logic [7:0] y = x + 1; endstage
```

Each tuple item may be a bare name or a packed SystemVerilog declaration:

```pipe
stage add {
    logic signed [W-1:0] sample,
    unsigned [7:0] flags
} yields {
    logic signed [W-1:0] result,
    flags
} begin
    result = sample + 1;
endstage
```

A bare name in the first-stage tuple must be declared in that stage body. In a
later stage, a bare input inherits the packed type of the positional value from
the preceding stage, so carried values do not need repeated declarations. A
typed header item is declared by the generator and must not be repeated in the
body.
The body is ordinary combinational SystemVerilog. Declare `logic` or `wire`
locals anywhere; a `logic` declaration initializer becomes an assignment in the
generated `always_comb`, while a `wire` initializer remains a continuous wire.

Adjacent stages connect tuple values positionally. Pipegen checks tuple length
and width at every boundary; names and signedness are local to each stage.
Place `skid` or `no_skid` anywhere before `endstage` to override automatic skid
placement after that stage.

Run checks with:

```sh
python3 -m unittest -v
```
