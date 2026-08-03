# Q1.15 two-gain pipeline

This example sends a signed Q1.15 sample through two hard-coded Q1.15 gains.
Each stage declares only the typed packed tuple it receives and yields; all
intermediate product and saturation values are local to that stage. The gains are
`0.75` (`16'sd24576`) and `0.50` (`16'sd16384`). Each multiplication produces a
signed 32-bit Q2.30 intermediate, followed by an arithmetic right shift of 15
bits and signed saturation back to Q1.15.

Run the simulation and write a waveform:

```sh
cd examples
make run
```

Open it in GTKWave:

```sh
make wave
```

The VCD is written to `examples/build/q15_two_gain.vcd`. The test drives `+0.5`
(`16384` in Q1.15), holds `out_ready` low briefly, and checks for `+0.1875`
(`6144`) at the output.

## Biquad low-pass chirp

`biquad_tdf2_lpf.pipe` is a two-state transposed direct-form-II biquad step.
It accepts Q1.15 `x`, `z1`, and `z2` plus five Q3.13 coefficients, preserves
Q4.28 products and wide accumulators inside the generated pipeline, then
saturates `y`, `z1_next`, and `z2_next` back to Q1.15 for external feedback.

The supplied coefficients implement an RBJ-style low-pass at approximately
0.10 of the sample rate with Q≈0.707. Run its Verilator chirp simulation with:

```sh
cd examples
make biquad
```

This writes [biquad_chirp.vcd](build/biquad_chirp.vcd) and
`build/biquad_chirp.csv`; open the waveform with `make biquad-wave`.
The supplied 192-sample chirp measures roughly −1.0 dB gain at its low-frequency
start, −16.4 dB in the middle, and −40.3 dB near its high-frequency end.

## Direct-form-I biquad low-pass chirp

`biquad_df1_lpf.pipe` is the strict direct-form-I version, preserving explicit
Q1.15 `x1`, `x2`, `y1`, and `y2` histories. It accepts the same five Q3.13
coefficients, and schedules the equation as 2 MACs (`b0*x + b1*x1`), 2 MACs
(`b2*x2 - a1*y1`), then one final feedback MAC (`-a2*y2`). Coefficients and
their samples are peeled away stage by stage; the remaining tuple is passed on
without repeating its declarations. Wide Q4.28 accumulators are kept until the
last stage, which returns `y`, `x1_next`, `x2_next`, `y1_next`, and `y2_next`
for external feedback.

```sh
cd examples
make biquad-df1
make biquad-df1-wave
```

The Verilator simulation writes `build/biquad_df1_chirp.vcd` and
`build/biquad_df1_chirp.csv`. Its measured gain is approximately −1.2 dB at
the low-frequency start, −12.6 dB in the middle, and −43.7 dB at the end.
