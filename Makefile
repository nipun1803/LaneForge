# =============================================================================
#  SIMD Vector Processing Unit - build & simulation flow
# =============================================================================
#  Usage:
#    make test_<name>            compile + run tb/tb_<name>.v   (e.g. make test_lane_alu)
#    make test_<name> LANES=8    same, overriding the testbench LANES parameter
#    make all_tests              run every tb/tb_*.v
#    make wave_<name>            run test, then print the VCD path to open
#    make synth LANES=4          Yosys generic synthesis + cell statistics
#    make clean                  remove build outputs
# =============================================================================

IVERILOG := iverilog
VVP      := vvp
YOSYS    := yosys

# Design parameters (passed to the testbench top as -P overrides)
LANES ?= 4
WIDTH ?= 8

RTL_DIR  := rtl
TB_DIR   := tb
SIM_DIR  := sim
RES_DIR  := results

RTL_SRCS := $(wildcard $(RTL_DIR)/*.v)
TB_SRCS  := $(wildcard $(TB_DIR)/tb_*.v)
TESTS    := $(patsubst $(TB_DIR)/tb_%.v,%,$(TB_SRCS))

# -g2012 : SystemVerilog-2012 features ($signed, int, etc.)
# -Wall  : all warnings (catches implicit nets / width mismatches early)
# -I     : `include search path - rtl/ for simd_defs.vh, tb/ for ref_alu.vh
IVFLAGS  := -g2012 -Wall -I $(RTL_DIR) -I $(TB_DIR)

.PHONY: all_tests clean synth help

# Keep compiled sims (otherwise make deletes them as intermediates)
.SECONDARY:

help:
	@echo "Targets: test_<name> | all_tests | wave_<name> | synth | clean"
	@echo "Tests found: $(TESTS)"

# --- Simulation ---------------------------------------------------------------
# Build outputs are tagged with LANES/WIDTH so a 4-lane build is never reused
# for an 8-lane run (parameters are baked in at compile time).
CFG      := L$(LANES)_W$(WIDTH)
TB_INCS  := $(wildcard $(TB_DIR)/*.vh) $(wildcard $(RTL_DIR)/*.vh)

# Compile the testbench with every RTL file; -s picks the testbench as top.
$(SIM_DIR)/tb_%_$(CFG).out: $(TB_DIR)/tb_%.v $(RTL_SRCS) $(TB_INCS) | $(SIM_DIR)
	$(IVERILOG) $(IVFLAGS) -s tb_$* \
	    -P tb_$*.LANES=$(LANES) -P tb_$*.WIDTH=$(WIDTH) \
	    -o $@ $(RTL_SRCS) $<

# Run, keep a log, and exit non-zero unless the bench printed "ALL PASS",
# so `make a && make b` and `make all_tests` stop at the first failure.
test_%: $(SIM_DIR)/tb_%_$(CFG).out
	@echo "=== Running tb_$* (LANES=$(LANES), WIDTH=$(WIDTH)) ==="
	@$(VVP) -n $< +vcd=$(SIM_DIR)/tb_$*_$(CFG).vcd | tee $(SIM_DIR)/tb_$*_$(CFG).log
	@grep -q "^ALL PASS" $(SIM_DIR)/tb_$*_$(CFG).log || \
	    (echo ">>> tb_$* FAILED (LANES=$(LANES), WIDTH=$(WIDTH))"; exit 1)

wave_%: test_%
	@echo "Open $(SIM_DIR)/tb_$*_$(CFG).vcd in VS Code (WaveTrace)"

all_tests:
	@for t in $(TESTS); do \
	    $(MAKE) --no-print-directory test_$$t || exit 1; \
	done
	@echo "=== ALL TESTBENCHES COMPLETED ==="

# --- Synthesis (generic cells, resource estimate) -----------------------------
synth: | $(RES_DIR)
	$(YOSYS) -q -p "read_verilog -sv -I$(RTL_DIR) $(RTL_SRCS); \
	    chparam -set LANES $(LANES) -set WIDTH $(WIDTH) simd_top; \
	    synth -top simd_top; \
	    tee -o $(RES_DIR)/synth_L$(LANES)_W$(WIDTH).txt stat"
	@echo "Generic cells, whole design and per module (LANES=$(LANES), WIDTH=$(WIDTH)):"
	@grep -A8 "design hierarchy" $(RES_DIR)/synth_L$(LANES)_W$(WIDTH).txt | grep -E "^ +[0-9]+ "
	@echo "Full report: $(RES_DIR)/synth_L$(LANES)_W$(WIDTH).txt"

# --- Housekeeping -------------------------------------------------------------
$(SIM_DIR) $(RES_DIR):
	mkdir -p $@

clean:
	rm -f $(SIM_DIR)/*.out $(SIM_DIR)/*.vcd $(SIM_DIR)/*.log
