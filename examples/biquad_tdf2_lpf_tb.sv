`timescale 1ns/1ps

module biquad_tdf2_lpf_tb;
    logic clk = 1'b0;
    logic reset = 1'b1;
    logic enable = 1'b1;
    logic in_valid = 1'b0;
    logic in_ready;
    logic out_valid;
    logic out_ready = 1'b1;

    logic signed [15:0] x = '0;
    logic signed [15:0] z1 = '0;
    logic signed [15:0] z2 = '0;
    logic signed [15:0] b0 = 16'sd553;
    logic signed [15:0] b1 = 16'sd1105;
    logic signed [15:0] b2 = 16'sd553;
    logic signed [15:0] a1 = -16'sd9363;
    logic signed [15:0] a2 = 16'sd3382;

    wire signed [127:0] packet_in = {x, z1, z2, b0, b1, b2, a1, a2};
    wire signed [47:0] packet_out;
    wire signed [15:0] y;
    wire signed [15:0] z1_next;
    wire signed [15:0] z2_next;
    assign {y, z1_next, z2_next} = packet_out;

    biquad_tdf2_lpf dut (.*);

    always #5 clk = ~clk;

    integer csv;
    integer index;
    real phase;
    real frequency;
    real amplitude;
    logic signed [15:0] chirp_sample;

    task automatic run_sample(input logic signed [15:0] next_x, input integer sample_index);
        begin
            @(negedge clk);
            x = next_x;
            in_valid = 1'b1;
            do @(posedge clk); while (!in_ready);
            @(negedge clk);
            in_valid = 1'b0;
            wait (out_valid);
            $fdisplay(csv, "%0d,%0d,%0d", sample_index, x, y);
            z1 = z1_next;
            z2 = z2_next;
        end
    endtask

    initial begin
        $dumpfile("build/biquad_chirp.vcd");
        $dumpvars(0, biquad_tdf2_lpf_tb);
        csv = $fopen("build/biquad_chirp.csv", "w");
        $fdisplay(csv, "index,input_q15,output_q15");

        repeat (2) @(posedge clk);
        reset = 1'b0;
        phase = 0.0;
        amplitude = 0.60 * 32767.0;

        for (index = 0; index < 192; index = index + 1) begin
            frequency = 0.005 + (0.445 * index / 191.0);
            phase = phase + 6.283185307179586 * frequency;
            chirp_sample = $signed(16'($rtoi(amplitude * $sin(phase))));
            run_sample(chirp_sample, index);
        end

        $fclose(csv);
        @(posedge clk);
        $finish;
    end
endmodule
