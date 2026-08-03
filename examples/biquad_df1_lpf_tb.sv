`timescale 1ns/1ps

module biquad_df1_lpf_tb;
    logic clk = 1'b0;
    logic reset = 1'b1;
    logic enable = 1'b1;
    logic in_valid = 1'b0;
    logic in_ready;
    logic out_valid;
    logic out_ready = 1'b1;

    logic signed [15:0] x = '0;
    logic signed [15:0] x1 = '0;
    logic signed [15:0] x2 = '0;
    logic signed [15:0] y1 = '0;
    logic signed [15:0] y2 = '0;
    logic signed [15:0] b0 = 16'sd553;
    logic signed [15:0] b1 = 16'sd1105;
    logic signed [15:0] b2 = 16'sd553;
    logic signed [15:0] a1 = -16'sd9363;
    logic signed [15:0] a2 = 16'sd3382;

    wire signed [159:0] packet_in = {x, x1, x2, y1, y2, b0, b1, b2, a1, a2};
    wire signed [79:0] packet_out;
    wire signed [15:0] y;
    wire signed [15:0] x1_next;
    wire signed [15:0] x2_next;
    wire signed [15:0] y1_next;
    wire signed [15:0] y2_next;
    assign {y, x1_next, x2_next, y1_next, y2_next} = packet_out;

    biquad_df1_lpf dut (.*);

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
            x1 = x1_next;
            x2 = x2_next;
            y1 = y1_next;
            y2 = y2_next;
        end
    endtask

    initial begin
        $dumpfile("build/biquad_df1_chirp.vcd");
        $dumpvars(0, biquad_df1_lpf_tb);
        csv = $fopen("build/biquad_df1_chirp.csv", "w");
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
