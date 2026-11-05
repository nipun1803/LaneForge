// =============================================================================
//  simd_defs.vh - single source of truth for the SIMD Vector Processing Unit
// =============================================================================
//  Every module and testbench `includes this file. Never hard-code an opcode
//  or a field position anywhere else.
//
//  Width-dependent values (e.g. shift-amount bits = $clog2(WIDTH)) are NOT
//  here, because LANES / WIDTH are module parameters; compute those locally.
// =============================================================================
`ifndef SIMD_DEFS_VH
`define SIMD_DEFS_VH

// -----------------------------------------------------------------------------
//  Architectural sizes
// -----------------------------------------------------------------------------
`define INSTR_W       16      // instruction width (bits)
`define NUM_VREGS     8       // vector registers v0..v7
`define VREG_AW       3       // vector register address width
`define IMEM_DEPTH    256     // instruction ROM entries
`define IMEM_AW       8       // PC width
`define DMEM_DEPTH    512     // data memory entries (each LANES*WIDTH bits)
`define DMEM_AW       9       // data memory address width

// -----------------------------------------------------------------------------
//  Opcodes  instr[15:12]
// -----------------------------------------------------------------------------
//  ALU (R-type)                         per lane i:
`define OP_NOP     4'b0000  //  -
`define OP_VADD    4'b0001  //  vd[i] = vs1[i] + vs2[i]          (wraps)
`define OP_VSUB    4'b0010  //  vd[i] = vs1[i] - vs2[i]          (wraps)
`define OP_VAND    4'b0011  //  vd[i] = vs1[i] & vs2[i]
`define OP_VOR     4'b0100  //  vd[i] = vs1[i] | vs2[i]
`define OP_VXOR    4'b0101  //  vd[i] = vs1[i] ^ vs2[i]
`define OP_VCMPEQ  4'b0110  //  vd[i] = (vs1[i] == vs2[i]) ? '1 : '0
`define OP_VCMPLT  4'b0111  //  vd[i] = signed(vs1[i]) < signed(vs2[i]) ? '1 : '0
`define OP_VSLL    4'b1000  //  vd[i] = vs1[i] << vs2[i][log2(WIDTH)-1:0]
`define OP_VSRL    4'b1001  //  vd[i] = vs1[i] >> vs2[i][log2(WIDTH)-1:0] (logical)
`define OP_VMUL    4'b1010  //  vd[i] = low WIDTH bits of vs1[i] * vs2[i] (optional)
//  Scalar / memory / control
`define OP_SLI     4'b1011  //  S-type: scalar = imm8 (zero-extended / truncated to WIDTH)
`define OP_VLD     4'b1100  //  M-type: vreg = DMEM[addr]
`define OP_VST     4'b1101  //  M-type: DMEM[addr] = vreg
`define OP_VBCAST  4'b1110  //  R-type (vd only): vd[i] = scalar, for every lane
`define OP_HALT    4'b1111  //  stop fetching, freeze cycle counter

// -----------------------------------------------------------------------------
//  Write-back source select (control unit -> result combiner)
// -----------------------------------------------------------------------------
`define WB_SEL_W   2
`define WB_ALU     2'd0     // vector ALU result      (VADD ... VMUL)
`define WB_MEM     2'd1     // data memory read data  (VLD)
`define WB_BCAST   2'd2     // replicated scalar      (VBCAST)
                            // 2'd3 reserved -> writes zero

// -----------------------------------------------------------------------------
//  Instruction field positions
// -----------------------------------------------------------------------------
//  R-type : | op[15:12] | vd[11:9] | vs1[8:6] | vs2[5:3] | s[2] | rsvd[1:0] |
//           s = 1 -> operand B is the broadcast scalar instead of vs2
//  M-type : | op[15:12] | vreg[11:9] | addr[8:0] |
//  S-type : | op[15:12] | rsvd[11:8] | imm8[7:0] |
`define F_OP      15:12
`define F_VD      11:9
`define F_VS1     8:6
`define F_VS2     5:3
`define F_S       2
`define F_VREG    11:9     // M-type register (dest for VLD, source for VST)
`define F_ADDR    8:0
`define F_IMM8    7:0

// -----------------------------------------------------------------------------
//  Encoding helpers (used by testbenches; the assembler mirrors these)
// -----------------------------------------------------------------------------
`define ENC_R(op, vd, vs1, vs2, s) {op, 3'(vd), 3'(vs1), 3'(vs2), 1'(s), 2'b00}
`define ENC_M(op, vreg, addr)      {op, 3'(vreg), 9'(addr)}
`define ENC_S(op, imm)             {op, 4'b0000, 8'(imm)}

`endif // SIMD_DEFS_VH
