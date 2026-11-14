"""Reproducible RTL mutations and separate negative coverage probes.

Each replacement has an expected occurrence count. Source drift is an error,
not a detected mutation. Only temporary source copies are edited by the runner.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    bench: str
    description: str
    path: str
    edits: tuple = ()
    lanes: int = 4
    width: int = 8
    overrides: tuple = ()


def rtl(name, module, description, edits, width=8):
    return Case(name, module, description, f"rtl/{module}.v", tuple(edits), width=width)


MUTATIONS = (
    rtl("M01", "vector_alu", "ADD uses one whole-vector adder, leaking carries between lanes", (
        ("    genvar i;", "    wire [LANES*WIDTH-1:0] lane_y;\n"
         "    assign vy = (op == `OP_VADD) ? va + vb : lane_y;\n    genvar i;", 1),
        (".y    (vy[i*WIDTH +: WIDTH])", ".y    (lane_y[i*WIDTH +: WIDTH])", 1),
    )),
    rtl("M02", "vector_alu", "Write-back lanes 0 and 1 are swapped", (
        (".y    (vy[i*WIDTH +: WIDTH])",
         ".y    (vy[((i == 0) ? 1 : (i == 1) ? 0 : i)*WIDTH +: WIDTH])", 1),
    )),
    rtl("M03", "vector_regfile", "Read port 1 forwards a pending write before the clock edge", (
        ("assign rdata1 = vregs[raddr1];",
         "assign rdata1 = (we && waddr == raddr1) ? wdata : vregs[raddr1];", 1),
    )),
    rtl("M04", "vector_regfile", "Read port 2 uses port 1's address", (
        ("assign rdata2 = vregs[raddr2];", "assign rdata2 = vregs[raddr1];", 1),
    )),
    rtl("M05", "vector_regfile", "Reset leaves the scalar register uncleared", (
        ("sreg <= {WIDTH{1'b0}};", "// mutation: scalar retains its value on reset", 1),
    )),
    rtl("M06", "vector_regfile", "Vector writes ignore their write enable", (
        ("if (we)   vregs[waddr] <= wdata;", "vregs[waddr] <= wdata;", 1),
    )),
    rtl("M07", "lane_mux", "Scalar broadcast drives only lane 0", (
        ("assign bcast = {LANES{scalar}};",
         "assign bcast = {{((LANES-1)*WIDTH){1'b0}}, scalar};", 1),
    )),
    rtl("M08", "lane_mux", "Operand selection is inverted", (
        ("sel_scalar ? bcast : vs2_data", "sel_scalar ? vs2_data : bcast", 1),
    )),
    rtl("M09", "lane_mux", "Scalar broadcast drives only the top lane", (
        ("assign bcast = {LANES{scalar}};",
         "assign bcast = {scalar, {((LANES-1)*WIDTH){1'b0}}};", 1),
    )),
    rtl("M10", "result_combiner", "Memory and broadcast write-back sources are swapped", (
        ("`WB_MEM:   wb_data = mem_rdata;", "`WB_MEM:   wb_data = bcast;", 1),
        ("`WB_BCAST: wb_data = bcast;", "`WB_BCAST: wb_data = mem_rdata;", 1),
    )),
    rtl("M11", "result_combiner", "any_true observes lane 0 only", (
        ("assign any_true = |alu_mask;", "assign any_true = alu_mask[0];", 1),
    )),
    rtl("M12", "result_combiner", "Population count skips the top lane", (
        ("i < LANES; i = i + 1", "i < LANES-1; i = i + 1", 1),
    )),
    rtl("M13", "result_combiner", "Reserved select passes the ALU result instead of zero", (
        ("default:   wb_data = {LANES*WIDTH{1'b0}};", "default:   wb_data = alu_y;", 1),
    )),
    rtl("M14", "vector_control", "VST also enables a vector-register write", (
        ("mem_we = 1'b1;", "mem_we = 1'b1;\n                rf_we = 1'b1;", 1),
    )),
    rtl("M15", "vector_control", "ALU instructions ignore the scalar-selection bit", (
        ("sel_scalar = instr[`F_S];", "sel_scalar = 1'b0;", 2),
    )),
    rtl("M16", "vector_control", "VST reads vs2 instead of the encoded store register", (
        ("raddr2 = instr[`F_VREG];", "raddr2 = instr[`F_VS2];", 1),
    )),
    rtl("M17", "vector_control", "VBCAST selects the memory write-back source", (
        ("wb_sel = `WB_BCAST;", "wb_sel = `WB_MEM;", 1),
    )),
    rtl("M18", "vector_control", "Missing halt default infers retained combinational state", (
        ("halt       = 1'b0;", "// mutation: no default assignment to halt", 1),
    )),
    rtl("M19", "simd_top", "Stores take data from read port 1 instead of port 2", (
        ("if (mem_we_g) dmem[mem_addr] <= rdata2;",
         "if (mem_we_g) dmem[mem_addr] <= rdata1;", 1),
    )),
    rtl("M20", "simd_top", "SLI sign-extends its immediate instead of zero-extending", (
        ("{{(WIDTH-8){1'b0}}, s_imm}", "{{(WIDTH-8){s_imm[7]}}, s_imm}", 1),
    ), width=16),
    rtl("M21", "simd_top", "lane_ops increments for every instruction", (
        ("if (is_alu)    lane_ops    <= lane_ops + LANES;",
         "lane_ops    <= lane_ops + LANES;", 1),
    )),
    rtl("M22", "simd_top", "All ALU instructions update compare-mask status", (
        ("wire mask_we_g = mask_we & run;",
         "wire mask_we_g = (alu_op != `OP_NOP) & run;", 1),
    )),
    rtl("M23", "simd_top", "Vector ALU bypasses the scalar operand multiplexer", (
        (".va(rdata1), .vb(vb), .op(alu_op)",
         ".va(rdata1), .vb(rdata2), .op(alu_op)", 1),
    )),
)


# Healthy RTL, deliberately incomplete stimulus. These are not RTL mutants.
COVERAGE_PROBES = (
    Case("C01", "lane_alu", "HALT is omitted from the non-ALU stimulus",
         "tb/tb_lane_alu.v", (("`OP_VBCAST, `OP_HALT};", "`OP_VBCAST, `OP_NOP};", 1),), width=2),
    Case("C02", "vector_alu", "VMUL stimulus is skipped",
         "tb/tb_vector_alu.v", (("op = o; va = xa; vb = xb;",
         "if (o == `OP_VMUL) return;\n        op = o; va = xa; vb = xb;", 1),)),
    Case("C03", "vector_control", "One instruction word is omitted from the exhaustive sweep",
         "tb/tb_vector_control.v", (("w < 65536; w++", "w < 65535; w++", 1),)),
    Case("C04", "vector_regfile", "Random-traffic section is empty",
         "tb/tb_vector_regfile.v", overrides=(("N_RANDOM", 0),)),
    Case("C05", "lane_mux", "Toggling section is empty",
         "tb/tb_lane_mux.v", overrides=(("N_RANDOM", 0),)),
    Case("C06", "result_combiner", "Directed source-selection section is empty",
         "tb/tb_result_combiner.v", overrides=(("N_RANDOM", 0),)),
)
