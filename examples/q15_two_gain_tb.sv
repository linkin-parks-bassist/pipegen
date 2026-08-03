`timescale 1ns/1ps

module q15_two_gain_tb;
  logic clk = 1'b0;
  logic reset = 1'b1;
  logic enable = 1'b1;
  logic in_valid = 1'b0;
  logic in_ready;
  logic signed [15:0] packet_in = '0;
  logic out_valid;
  logic out_ready = 1'b1;
  logic signed [15:0] packet_out;

  q15_two_gain dut (.*);

  always #5 clk = ~clk;

  initial begin
    $dumpfile("build/q15_two_gain.vcd");
    $dumpvars(0, q15_two_gain_tb);

    repeat (2) @(posedge clk);
    reset = 1'b0;

    // +0.5 in Q1.15.  With gains 0.75 then 0.50, expect +0.1875 (6144).
    @(negedge clk);
    packet_in = 16'sd16384;
    in_valid = 1'b1;
    @(negedge clk);
    in_valid = 1'b0;

    // Hold the final consumer briefly so the ready/valid waveforms are visible.
    out_ready = 1'b0;
    wait (out_valid);
    repeat (2) @(posedge clk);
    if (packet_out !== 16'sd6144)
      $fatal(1, "expected Q1.15 value 6144, got %0d", packet_out);
    out_ready = 1'b1;
    @(posedge clk);
    $finish;
  end
endmodule
