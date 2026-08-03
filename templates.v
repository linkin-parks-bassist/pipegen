module pipeline_stage_template #(parameter packet_width)
	(
		input wire clk,
		input wire reset,
		
		input wire enable,
	
		input  wire in_valid,
		output wire in_ready,
		
		output reg out_valid,
		input wire out_ready,
		
		input wire [packet_width - 1 : 0] packet_in,
		output reg [packet_width - 1 : 0] packet_out,
	);
	
	assign in_ready = ~out_valid | out_ready;
	
	wire take_in  = in_ready & in_valid;
	wire take_out = out_valid & out_ready;

	always @(posedge clk) begin
		if (reset) begin
			out_valid 	<= 0;
		end else if (enable) begin
			if (take_in) begin
				

				out_valid <= 1;
			end else if (take_out) begin
				out_valid <= 0;
			end
		end
	end
endmodule

module skid_buffer #(parameter packet_width)
	(
		input wire clk,
		input wire reset,
		
		input wire enable,
		
		input wire  in_valid,
		output wire in_ready,
		
		output reg out_valid,
		input wire out_ready,
		
		input wire [packet_width - 1 : 0] packet_in,
		output reg [packet_width - 1 : 0] packet_out
	);

	reg [packet_width - 1 : 0] packet_skid;
	reg skid;
	
	assign in_ready = ~(out_valid & skid);
	
	wire take_in = in_ready & in_valid;
	wire take_out = out_valid & out_ready;
	
	always @(posedge clk) begin
		if (reset) begin
			skid <= 0;
			out_valid <= 0;
			packet_out <= 0;
		end else if (enable) begin
			case ({take_in, take_out})
				2'b00: begin
				
				end
				
				2'b01: begin
					if (skid) begin
						packet_out <= packet_skid;
						skid <= 0;
					end else begin
						out_valid <= 0;
					end
				end
				
				2'b10: begin
					if (out_valid) begin
						packet_skid <= packet_in;
						skid <= 1;
					end else begin
						packet_out <= packet_in;
						out_valid <= 1;
					end
				end
				
				2'b11: begin
					if (skid) begin
						packet_out <= packet_skid;
						packet_skid <= packet_in;
					end else begin
						packet_out <= packet_in;
					end
				end
			endcase
		end
	end
endmodule
