`timescale 1ns/1ps

module fabric_demo_tb;
    logic clk = 1'b0;
    logic reset = 1'b1;
    logic enable = 1'b1;

    logic direct_src__tx__direct__valid = 1'b0;
    logic direct_src__tx__direct__ready;
    logic [7:0] direct_src__tx__direct__payload = '0;
    logic direct_dst__rx__valid;
    logic direct_dst__rx__ready = 1'b1;
    logic [7:0] direct_dst__rx__payload;

    logic source_a__tx__to_sink__valid = 1'b0;
    logic source_a__tx__to_sink__ready;
    logic [7:0] source_a__tx__to_sink__payload = '0;
    logic source_d__tx__to_sink__valid = 1'b0;
    logic source_d__tx__to_sink__ready;
    logic [7:0] source_d__tx__to_sink__payload = '0;
    logic sink__rx__valid;
    logic sink__rx__ready = 1'b1;
    logic [7:0] sink__rx__payload;
    logic [1:0] sink__rx__path;

    fabric_demo dut (.*);

    always #5 clk = ~clk;

    task automatic send_direct(input logic [7:0] value);
        begin
            @(negedge clk);
            direct_src__tx__direct__payload = value;
            direct_src__tx__direct__valid = 1'b1;
            do @(posedge clk); while (!direct_src__tx__direct__ready);
            @(negedge clk);
            direct_src__tx__direct__valid = 1'b0;
        end
    endtask

    task automatic send_a(input logic [7:0] value);
        begin
            @(negedge clk);
            source_a__tx__to_sink__payload = value;
            source_a__tx__to_sink__valid = 1'b1;
            do @(posedge clk); while (!source_a__tx__to_sink__ready);
            @(negedge clk);
            source_a__tx__to_sink__valid = 1'b0;
        end
    endtask

    task automatic send_d(input logic [7:0] value);
        begin
            @(negedge clk);
            source_d__tx__to_sink__payload = value;
            source_d__tx__to_sink__valid = 1'b1;
            do @(posedge clk); while (!source_d__tx__to_sink__ready);
            @(negedge clk);
            source_d__tx__to_sink__valid = 1'b0;
        end
    endtask

    task automatic expect_direct(input logic [7:0] expected);
        begin
            wait (direct_dst__rx__valid);
            if (direct_dst__rx__payload !== expected)
                $fatal(1, "direct expected %0d, got %0d", expected, direct_dst__rx__payload);
            @(posedge clk);
        end
    endtask

    task automatic expect_sink(input logic [7:0] expected, input logic [1:0] expected_path);
        begin
            wait (sink__rx__valid);
            if (sink__rx__payload !== expected)
                $fatal(1, "sink expected %0d, got %0d", expected, sink__rx__payload);
            if (sink__rx__path !== expected_path)
                $fatal(1, "sink path expected %b, got %b", expected_path, sink__rx__path);
            @(posedge clk);
            // Let the destination skid register observe the completed transfer
            // before the next expectation changes sink readiness.
            @(negedge clk);
        end
    endtask

    initial begin
        $dumpfile("build/fabric_demo.vcd");
        $dumpvars(0, fabric_demo_tb);

        repeat (2) @(posedge clk);
        reset = 1'b0;

        direct_dst__rx__ready = 1'b0;
        send_direct(8'h5a);
        expect_direct(8'h5a);
        repeat (2) @(posedge clk);
        direct_dst__rx__ready = 1'b1;
        @(posedge clk);

        send_a(8'h31);
        expect_sink(8'h31, dut.sink__rx__SOURCE__a);

        sink__rx__ready = 1'b0;
        send_d(8'hd4);
        expect_sink(8'hd4, dut.sink__rx__SOURCE__d);
        repeat (2) @(posedge clk);
        sink__rx__ready = 1'b1;
        @(posedge clk);
        $finish;
    end
endmodule
