PIGEN := ../pigen.py
PIPE := q15_two_gain.pipe
RTL := build/q15_two_gain.sv
SIM := build/q15_two_gain_sim
VCD := build/q15_two_gain.vcd
FABRIC := fabric_demo.fabric
FABRIC_RTL := build/fabric_demo.sv
FABRIC_SIM := build/fabric_demo_sim
FABRIC_VCD := build/fabric_demo.vcd
FABRIC_VERILATOR_SIM := build/fabric_obj/fabric_demo_verilator
NETWORK_FABRIC := mixer_network.fabric
NETWORK_RTL := build/mixer_network.sv
MAC_PIPE := mac.pipe
MAC_RTL := build/mac.sv
MAC_SIM := build/mac_sim
MAC_VCD := build/mac.vcd
BIQUAD_PIPE := biquad_tdf2_lpf.pipe
BIQUAD_RTL := build/biquad_tdf2_lpf.sv
BIQUAD_SIM := build/biquad_obj/biquad_chirp_sim
BIQUAD_VCD := build/biquad_chirp.vcd
DF1_PIPE := biquad_df1_lpf.pipe
DF1_RTL := build/biquad_df1_lpf.sv
DF1_SIM := build/biquad_df1_obj/biquad_df1_chirp_sim
DF1_VCD := build/biquad_df1_chirp.vcd

.PHONY: all run wave fabric fabric-wave fabric-verilator fabric-network mac mac-wave biquad biquad-wave biquad-df1 biquad-df1-wave clean

all: run

build:
	mkdir -p $@

$(RTL): $(PIPE) $(PIGEN) | build
	$(PIGEN) --skid-step=4 $(PIPE) -o $@

$(SIM): $(RTL) q15_two_gain_tb.sv
	iverilog -g2012 -s q15_two_gain_tb -o $@ $(RTL) q15_two_gain_tb.sv

run: $(SIM)
	./$(SIM)

wave: run
	gtkwave $(VCD)

$(FABRIC_RTL): $(FABRIC) $(PIGEN) ../fabric.py | build
	$(PIGEN) fabric $(FABRIC) -o $@

$(FABRIC_SIM): $(FABRIC_RTL) fabric_demo_tb.sv
	iverilog -g2012 -s fabric_demo_tb -o $@ $(FABRIC_RTL) fabric_demo_tb.sv

fabric: $(FABRIC_RTL) fabric_demo_tb.sv
	# Always compile locally: Icarus .vvp files are version-specific.
	iverilog -g2012 -s fabric_demo_tb -o $(FABRIC_SIM) $(FABRIC_RTL) fabric_demo_tb.sv
	./$(FABRIC_SIM)

fabric-wave: fabric
	gtkwave $(FABRIC_VCD)

$(FABRIC_VERILATOR_SIM): $(FABRIC_RTL) fabric_demo_tb.sv
	verilator --binary --trace --timing -Wno-fatal --top-module fabric_demo_tb --Mdir build/fabric_obj -o fabric_demo_verilator $(FABRIC_RTL) fabric_demo_tb.sv

fabric-verilator: $(FABRIC_VERILATOR_SIM)
	./$(FABRIC_VERILATOR_SIM)

$(NETWORK_RTL): $(NETWORK_FABRIC) $(PIGEN) ../fabric.py | build
	$(PIGEN) fabric $(NETWORK_FABRIC) -o $@

fabric-network: $(NETWORK_RTL)
	@echo "Generated $(NETWORK_RTL), $(NETWORK_RTL).routes, and $(NETWORK_RTL).svg"

$(MAC_RTL): $(MAC_PIPE) $(PIGEN) | build
	$(PIGEN) --skid-step=4 $(MAC_PIPE) -o $@

$(MAC_SIM): $(MAC_RTL) mac_tb.sv
	iverilog -g2012 -s mac_tb -o $@ $(MAC_RTL) mac_tb.sv

mac: $(MAC_SIM)
	./$(MAC_SIM)

mac-wave: mac
	gtkwave $(MAC_VCD)

$(BIQUAD_RTL): $(BIQUAD_PIPE) $(PIGEN) | build
	$(PIGEN) --skid-step=4 $(BIQUAD_PIPE) -o $@

$(BIQUAD_SIM): $(BIQUAD_RTL) biquad_tdf2_lpf_tb.sv
	verilator --binary --trace --timing -Wno-fatal --top-module biquad_tdf2_lpf_tb --Mdir build/biquad_obj -o biquad_chirp_sim $(BIQUAD_RTL) biquad_tdf2_lpf_tb.sv

biquad: $(BIQUAD_SIM)
	./$(BIQUAD_SIM)

biquad-wave: biquad
	gtkwave $(BIQUAD_VCD)

$(DF1_RTL): $(DF1_PIPE) $(PIGEN) | build
	$(PIGEN) --skid-step=4 $(DF1_PIPE) -o $@

$(DF1_SIM): $(DF1_RTL) biquad_df1_lpf_tb.sv
	verilator --binary --trace --timing -Wno-fatal --top-module biquad_df1_lpf_tb --Mdir build/biquad_df1_obj -o biquad_df1_chirp_sim $(DF1_RTL) biquad_df1_lpf_tb.sv

biquad-df1: $(DF1_SIM)
	./$(DF1_SIM)

biquad-df1-wave: biquad-df1
	gtkwave $(DF1_VCD)

clean:
	rm -f $(RTL) $(SIM) $(VCD) $(FABRIC_RTL) $(FABRIC_SIM) $(FABRIC_VCD) build/fabric_demo.sv.routes build/fabric_demo.sv.svg $(NETWORK_RTL) $(NETWORK_RTL).routes $(NETWORK_RTL).svg $(MAC_RTL) $(MAC_SIM) $(MAC_VCD) $(BIQUAD_RTL) $(BIQUAD_VCD) build/biquad_chirp.csv $(DF1_RTL) $(DF1_VCD) build/biquad_df1_chirp.csv
	rm -rf build/fabric_obj build/biquad_obj build/biquad_df1_obj
