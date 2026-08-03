import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pipegen


SOURCE = '''pipeline example #(
    parameter integer W = 8
) begin

    stage add {
        logic signed [W-1:0] left,
        right,
        unsigned [1:0] opcode
    } yields {
        result
    } begin

        logic signed [W-1:0] right;
        logic signed [W:0] result = left + right;

        if (opcode == 2'b11) begin
            result = result + 1;
        end

    endstage

    stage finish {input_sum} yields {forwarded} begin
        logic signed [W:0] input_sum;
        wire signed [W:0] forwarded = input_sum;
    endstage

endpipeline
'''


class PipegenTests(unittest.TestCase):
    def test_brace_tuples_resolve_typed_and_bare_names(self):
        pipe = pipegen.parse_pipe(SOURCE)
        self.assertEqual([signal.name for signal in pipe.inputs], ["left", "right", "opcode"])
        self.assertEqual([signal.name for signal in pipe.outputs], ["forwarded"])
        self.assertEqual(pipe.inputs[0].width, "W")
        text = pipegen.generate(pipe, 4)
        self.assertIn("logic signed [W:0] result;", text)
        self.assertIn("result = left + right;", text)
        self.assertIn("packet_comb = {result};", text)
        self.assertIn("example__skid", text)

    def test_whitespace_is_cosmetic_inside_a_stage(self):
        compact = '''pipeline compact begin
stage one {x} yields {y} begin logic [7:0] x; logic [7:0] y = x + 1; endstage
endpipeline
'''
        pipe = pipegen.parse_pipe(compact)
        self.assertEqual(pipe.inputs[0].width, "8")
        self.assertIn("y = x + 1;", pipegen.generate(pipe))

    def test_later_stage_can_inherit_carried_tuple_types(self):
        source = '''pipeline carried begin
    stage first {x} yields {mid} begin
        logic signed [7:0] x;
        logic signed [7:0] mid = x;
    endstage

    stage second {renamed_mid} yields {out} begin
        logic signed [7:0] out = renamed_mid;
    endstage
endpipeline
'''
        pipe = pipegen.parse_pipe(source)
        self.assertEqual(pipe.stages[1].inputs[0].name, "renamed_mid")
        self.assertEqual(pipe.stages[1].inputs[0].width, "8")
        text = pipegen.generate(pipe, 0)
        self.assertIn("logic signed [7:0] renamed_mid;", text)
        self.assertIn("renamed_mid = packet_in[0 +: (8)];", text)

    def test_width_mismatch_is_rejected_at_boundary(self):
        invalid = SOURCE.replace("stage finish {input_sum}", "stage finish {logic signed [W-1:0] input_sum}").replace("        logic signed [W:0] input_sum;\n", "")
        with self.assertRaisesRegex(pipegen.PipegenError, "width mismatch"):
            pipegen.parse_pipe(invalid)

    def test_bare_tuple_name_requires_body_declaration(self):
        invalid = SOURCE.replace("logic signed [W-1:0] right;", "", 1)
        with self.assertRaisesRegex(pipegen.PipegenError, "requires a declaration"):
            pipegen.parse_pipe(invalid)

    def test_header_typed_value_must_not_be_redeclared(self):
        invalid = SOURCE.replace("logic signed [W-1:0] right;", "logic signed [W-1:0] left;", 1)
        with self.assertRaisesRegex(pipegen.PipegenError, "must not be redeclared"):
            pipegen.parse_pipe(invalid)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "x.pipe", root / "x.sv"
            source.write_text(SOURCE)
            result = subprocess.run(["python3", "pipegen.py", "--skid_step=0", "--module", "renamed", str(source), "-o", str(output)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("module renamed", output.read_text())

    @unittest.skipUnless(shutil.which("iverilog"), "iverilog is not installed")
    def test_generated_rtl_compiles_with_iverilog(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "example.sv"
            output.write_text(pipegen.generate(pipegen.parse_pipe(SOURCE), 4))
            result = subprocess.run(["iverilog", "-g2012", "-s", "example", "-o", str(Path(directory) / "sim"), str(output)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog is not installed")
    def test_signed_packet_transfers_through_inferred_interface(self):
        testbench = '''
module tb;
  logic clk = 0, reset = 1, enable = 1, in_valid = 0, out_ready = 1;
  logic [17:0] packet_in;
  logic in_ready, out_valid;
  logic [8:0] packet_out;
  example dut (.*);
  always #5 clk = ~clk;
  initial begin
    repeat (2) @(posedge clk);
    reset = 0;
    @(negedge clk);
    packet_in = {-8'sd3, -8'sd4, 2'b00};
    in_valid = 1;
    @(negedge clk);
    in_valid = 0;
    wait (out_valid);
    if ($signed(packet_out) !== -9'sd7) $fatal(1, "expected -7, got %0d", $signed(packet_out));
    @(posedge clk);
    $finish;
  end
endmodule
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, executable = root / "tb.sv", root / "sim"
            source.write_text(pipegen.generate(pipegen.parse_pipe(SOURCE), 0) + testbench)
            compile_result = subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", str(executable), str(source)], text=True, capture_output=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            run_result = subprocess.run(["vvp", str(executable)], text=True, capture_output=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog is not installed")
    def test_mac_example_generates_and_simulates(self):
        root = Path(__file__).parent
        pipe = pipegen.parse_pipe((root / "examples" / "mac.pipe").read_text())
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "mac.sv"
            executable = Path(directory) / "sim"
            generated.write_text(pipegen.generate(pipe))
            result = subprocess.run(
                ["iverilog", "-g2012", "-s", "mac_tb", "-o", str(executable), str(generated), str(root / "examples" / "mac_tb.sv")],
                text=True,
                capture_output=True,
                cwd=root / "examples",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(["vvp", str(executable)], text=True, capture_output=True, cwd=root / "examples")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
