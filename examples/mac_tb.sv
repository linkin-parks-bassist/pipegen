`timescale 1ns/1ps

module mac_tb;
    logic clk = 1'b0;
    logic reset = 1'b1;
    logic enable = 1'b1;
    logic in_valid = 1'b0;
    logic in_ready;
    logic [47:0] packet_in = '0;
    logic out_valid;
    logic out_ready = 1'b1;
    logic [15:0] packet_out;

    mac dut (.*);

    always #5 clk = ~clk;

    task automatic send(input logic [15:0] m, input logic [15:0] x, input logic [15:0] b);
        begin
            @(negedge clk);
            packet_in = {m, x, b};
            in_valid = 1'b1;
            do @(posedge clk); while (!in_ready);
            @(negedge clk);
            in_valid = 1'b0;
        end
    endtask

    task automatic expect_output(input logic [15:0] expected);
        begin
            wait (out_valid);
            if (packet_out !== expected)
                $fatal(1, "expected %0d, got %0d", expected, packet_out);
            @(posedge clk);
        end
    endtask

    initial begin
        $dumpfile("build/mac.vcd");
        $dumpvars(0, mac_tb);

        repeat (2) @(posedge clk);
        reset = 1'b0;

        // (3 * 7) + 2 = 23. Hold the consumer to exercise backpressure.
        out_ready = 1'b0;
        send(16'd3, 16'd7, 16'd2);
        expect_output(16'd23);
        repeat (2) @(posedge clk);
        out_ready = 1'b1;
        @(posedge clk);

        // (65535 * 2) + 3 = 131073, deliberately truncated to 16 bits.
        send(16'hffff, 16'd2, 16'd3);
        expect_output(16'd1);
        $finish;
    end
endmodule
