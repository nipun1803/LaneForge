# =============================================================================
#  SIMD Vector Processing Unit - build & simulation flow
# =============================================================================
#  Usage:
#    make test_<name>            compile + run tb/tb_<name>.v   (e.g. make test_lane_alu)
#    make test_<name> LANES=8    same, overriding the testbench LANES parameter
#    make all_tests              run every tb/tb_*.v
#    make wave_<name>            run test, then print the VCD path to open
#    make asm                    assemble programs/*.s -> programs/*.hex (+ _data.hex)
#    make asm_test               run the assembler's self-checking tests
#    make model_test             ISA reference model tests + RTL-vs-model cross-check
#    make run_prog PROG=x.s      run one program on the RTL and the ISA model, compare
#    make random_tests           random programs, RTL vs ISA model, 12 builds (N=1000 SEED=1)
#    make random_mutations       score random programs against the 23 planted RTL bugs
#    make bench                  scalar-vs-SIMD benchmarks (LANES=1,4,8) -> results/bench.*
#    make demo                   assemble, then run the example programs in programs/
#    make synth LANES=4          Yosys generic synthesis + cell statistics
#    make synth_scaling          generic synthesis for LANES=1,4,8 -> results/synth_scaling.csv
#    make fpga                   ECP5 synthesis + place & route, LANES=1,4,8 -> results/fpga/
#    make report                 tables from results/ + build docs/report/main.pdf (tectonic)
#    make clean                  remove build outputs
# =============================================================================

IVERILOG := iverilog
VVP      := vvp
YOSYS    := yosys
PYTHON   ?= python3

# Design parameters (passed to the testbench top as -P overrides)
LANES ?= 4
WIDTH ?= 8

RTL_DIR  := rtl
TB_DIR   := tb
SIM_DIR  := sim
RES_DIR  := results
SYNTH_DIR := synthesis

RTL_SRCS := $(wildcard $(RTL_DIR)/*.v)
TB_SRCS  := $(wildcard $(TB_DIR)/tb_*.v)
TESTS    := $(patsubst $(TB_DIR)/tb_%.v,%,$(TB_SRCS))

# -g2012 : SystemVerilog-2012 features ($signed, int, etc.)
# -Wall  : all warnings (catches implicit nets / width mismatches early)
# -I     : `include search path - rtl/ for simd_defs.vh, tb/ for ref_alu.vh
IVFLAGS  := -g2012 -Wall -I $(RTL_DIR) -I $(TB_DIR)

.PHONY: all_tests clean synth help demo verify_matrix mutation_tests asm asm_test model_test run_prog random_tests random_mutations bench synth_scaling fpga report

# Keep compiled sims (otherwise make deletes them as intermediates)
.SECONDARY:

help:
	@echo "Targets: test_<name> | all_tests | verify_matrix | mutation_tests | wave_<name> | asm | asm_test | model_test | run_prog | random_tests | random_mutations | bench | demo | synth | synth_scaling | fpga | report | clean"
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

# Complete configuration logs and isolated RTL mutation / coverage probes.
verify_matrix:
	$(PYTHON) scripts/verify_evidence.py matrix --iverilog "$(IVERILOG)" --vvp "$(VVP)"

mutation_tests:
	$(PYTHON) scripts/verify_evidence.py mutations --iverilog "$(IVERILOG)" --vvp "$(VVP)"

# --- Assembler (scripts/lfasm.py; opcodes and fields come from rtl/simd_defs.vh)
# The example programs are written for the default LANES=4, WIDTH=8 build.
ASM_SRCS := $(wildcard programs/*.s)
ASM_HEX  := $(ASM_SRCS:.s=.hex)
ASM_DATA := $(ASM_SRCS:.s=_data.hex)

# One assembler run writes both files (a pattern rule with two targets is run
# once for both), so deleting either one makes `make asm` rebuild it.
programs/%.hex programs/%_data.hex: programs/%.s scripts/lfasm.py $(RTL_DIR)/simd_defs.vh
	$(PYTHON) scripts/lfasm.py $< --lanes 4 --width 8

asm: $(ASM_HEX) $(ASM_DATA)

asm_test:
	$(PYTHON) scripts/test_lfasm.py

# --- ISA reference model (scripts/lfmodel.py) and program runner (tb/prog_runner.v)
model_test:
	$(PYTHON) scripts/test_lfmodel.py

run_prog:
	@test -n "$(PROG)" || (echo "usage: make run_prog PROG=file.s [LANES=4 WIDTH=8]"; exit 1)
	$(PYTHON) scripts/lfrun.py $(PROG) --lanes $(LANES) --width $(WIDTH) --iverilog "$(IVERILOG)" --vvp "$(VVP)"

# --- Random-program verification and benchmarks (Phase II) ------------------------
N    ?= 1000
SEED ?= 1

random_tests:
	$(PYTHON) scripts/random_verify.py --n $(N) --seed $(SEED) --iverilog "$(IVERILOG)" --vvp "$(VVP)"

random_mutations:
	$(PYTHON) scripts/random_verify.py --mutations --n 300 --seed $(SEED) --iverilog "$(IVERILOG)" --vvp "$(VVP)"

bench:
	$(PYTHON) scripts/bench.py --iverilog "$(IVERILOG)" --vvp "$(VVP)"

# --- Example programs (programs/*.hex, loaded via IMEM_INIT / DMEM_INIT) -------
demo: asm | $(SIM_DIR)
	$(IVERILOG) $(IVFLAGS) -s run_programs -o $(SIM_DIR)/run_programs.out \
	    $(RTL_SRCS) programs/run_programs.v
	@$(VVP) -n $(SIM_DIR)/run_programs.out +vcd=$(SIM_DIR)/run_programs.vcd \
	    | grep -v "Not enough words" \
	    | tee $(SIM_DIR)/run_programs.log
	@grep -q "^ALL PASS" $(SIM_DIR)/run_programs.log

# --- Synthesis (generic cells; external instructions, instruction ROM excluded)
SYNTH_TOP := simd_synth_top
SYNTH_SRC := $(SYNTH_DIR)/$(SYNTH_TOP).v
SYNTH_STAT := $(SIM_DIR)/synth_$(CFG).stat
SYNTH_REPORT := $(RES_DIR)/synth_$(CFG).txt
SYNTH_CMD := read_verilog -sv -I$(RTL_DIR) $(RTL_SRCS) $(SYNTH_SRC); chparam -set LANES $(LANES) -set WIDTH $(WIDTH) $(SYNTH_TOP); synth -top $(SYNTH_TOP); tee -o $(SYNTH_STAT) stat

synth: | $(SIM_DIR) $(RES_DIR)
	$(YOSYS) -q -p "$(SYNTH_CMD)"
	@{ \
	    printf '# LaneForge generic synthesis evidence\n'; \
	    printf '# Generated (UTC): '; date -u '+%Y-%m-%dT%H:%M:%SZ'; \
	    printf '# Tool: '; $(YOSYS) -V; \
	    printf '# Parameters: LANES=$(LANES), WIDTH=$(WIDTH), EXTERNAL_INSTR=1\n'; \
	    printf '# Scope: datapath, data memory, PC, counters; instruction ROM excluded\n'; \
	    printf '# Reproduce: make synth LANES=$(LANES) WIDTH=$(WIDTH)\n'; \
	    printf '# Yosys command: $(YOSYS) -q -p "%s"\n' '$(SYNTH_CMD)'; \
	    printf '# Source SHA-256 hashes:\n'; \
	    shasum -a 256 $(RTL_SRCS) $(RTL_DIR)/simd_defs.vh $(SYNTH_SRC); \
	    printf '\n'; sed 's/[[:space:]]*$$//' $(SYNTH_STAT); \
	} > $(SYNTH_REPORT).tmp
	@mv $(SYNTH_REPORT).tmp $(SYNTH_REPORT)
	@echo "Generic cell hierarchy (LANES=$(LANES), WIDTH=$(WIDTH), instruction ROM excluded):"
	@sed -n '/=== design hierarchy ===/,$$p' $(SYNTH_REPORT)
	@echo "Full report with reproduction details: $(SYNTH_REPORT)"

# --- Scaling studies (Phase II) ---------------------------------------------------
# Generic cells for 1, 4 and 8 lanes (same Yosys flow as `make synth`).
synth_scaling:
	for l in 1 4 8; do $(MAKE) --no-print-directory synth LANES=$$l WIDTH=8 || exit 1; done
	$(PYTHON) scripts/synth_scaling.py --lanes 1,4,8 --width 8

# Lattice ECP5-85F resource use and Fmax with the open-source flow (Yosys synth_ecp5 +
# nextpnr-ecp5). Needs the YosysHQ OSS CAD Suite; point OSS_CAD at its bin/ directory.
OSS_CAD    ?= $(HOME)/tools/oss-cad-suite/bin
FPGA_LANES ?= 1,4,8
FPGA_SEEDS ?= 1,2,3

fpga:
	$(PYTHON) scripts/fpga_flow.py --lanes $(FPGA_LANES) --width 8 --seeds $(FPGA_SEEDS) --tools "$(OSS_CAD)"

# --- IEEE report: every Results number is generated from results/ --------------------
TECTONIC ?= tectonic

report:
	$(PYTHON) scripts/report_tables.py
	cd docs/report && $(TECTONIC) main.tex

# --- Housekeeping -------------------------------------------------------------
$(SIM_DIR) $(RES_DIR):
	mkdir -p $@

clean:
	rm -f $(SIM_DIR)/*.out $(SIM_DIR)/*.vcd $(SIM_DIR)/*.log $(SIM_DIR)/*.stat
