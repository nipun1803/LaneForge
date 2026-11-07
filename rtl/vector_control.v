// =============================================================================
//  vector_control.v - instruction decoder (Vector Control Unit)
// =============================================================================
//  Purely combinational: one 16-bit instruction in, every datapath control
//  signal out. This is the ONLY module that interprets opcodes for control;
//  the datapath blocks just obey these signals.
//
//  Field wiring (see simd_defs.vh):
//    R-type | op | vd | vs1 | vs2 | s | -- |
//    M-type | op | vreg |   addr[8:0]     |
//    S-type | op | ---- |   imm8[7:0]     |
//  vd and vreg share bits [11:9], so the write address is always instr[11:9].
//  Register addresses are "don't care" unless their enable is asserted; the
//  decoder still drives them deterministically (never X).
//
//  Decode table (1 = asserted):
//    opcode        rf_we  wb_sel   mem_we  s_we  mask_we  halt  alu_op   raddr2
//    VADD..VMUL      1    WB_ALU     0      0    cmp?      0    op       vs2
//    VLD             1    WB_MEM     0      0     0        0    NOP      vs2
//    VST             0    WB_ALU     1      0     0        0    NOP      vreg
//    VBCAST          1    WB_BCAST   0      0     0        0    NOP      vs2
//    SLI             0    WB_ALU     0      1     0        0    NOP      vs2
//    NOP             0    WB_ALU     0      0     0        0    NOP      vs2
//    HALT            0    WB_ALU     0      0     0        1    NOP      vs2
//  (cmp? = 1 only for VCMPEQ / VCMPLT: latch the lane mask into a status reg)
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module vector_control (
    input  wire [`INSTR_W-1:0]  instr,

    // Vector ALU / operand select
    output reg  [3:0]           alu_op,      // opcode for every lane (NOP when not ALU)
    output reg                  sel_scalar,  // operand B = broadcast scalar

    // Register file
    output wire [`VREG_AW-1:0]  raddr1,      // vs1
    output reg  [`VREG_AW-1:0]  raddr2,      // vs2, or VST source register
    output wire [`VREG_AW-1:0]  waddr,       // vd / VLD destination
    output reg                  rf_we,
    output reg  [`WB_SEL_W-1:0] wb_sel,

    // Scalar register
    output reg                  s_we,
    output wire [7:0]           s_imm,

    // Data memory
    output reg                  mem_we,
    output wire [`DMEM_AW-1:0]  mem_addr,

    // Status / sequencing
    output reg                  mask_we,     // compare: update mask status register
    output reg                  halt
);
    wire [3:0] op = instr[`F_OP];

    // ---- Pure field wiring ----------------------------------------------------
    assign raddr1   = instr[`F_VS1];
    assign waddr    = instr[`F_VD];      // == F_VREG for VLD
    assign s_imm    = instr[`F_IMM8];
    assign mem_addr = instr[`F_ADDR];

    // ---- Opcode-dependent enables ------------------------------------------------
    always @(*) begin
        // Safe defaults = NOP behaviour: nothing is written anywhere
        alu_op     = `OP_NOP;
        sel_scalar = 1'b0;
        raddr2     = instr[`F_VS2];
        rf_we      = 1'b0;
        wb_sel     = `WB_ALU;
        s_we       = 1'b0;
        mem_we     = 1'b0;
        mask_we    = 1'b0;
        halt       = 1'b0;

        case (op)
            `OP_VADD, `OP_VSUB, `OP_VAND, `OP_VOR, `OP_VXOR,
            `OP_VSLL, `OP_VSRL, `OP_VMUL: begin
                alu_op     = op;
                sel_scalar = instr[`F_S];
                rf_we      = 1'b1;
            end

            `OP_VCMPEQ, `OP_VCMPLT: begin
                alu_op     = op;
                sel_scalar = instr[`F_S];
                rf_we      = 1'b1;
                mask_we    = 1'b1;
            end

            `OP_VLD: begin
                rf_we  = 1'b1;
                wb_sel = `WB_MEM;
            end

            `OP_VST: begin
                raddr2 = instr[`F_VREG];   // store data comes out of read port 2
                mem_we = 1'b1;
            end

            `OP_VBCAST: begin
                rf_we  = 1'b1;
                wb_sel = `WB_BCAST;
            end

            `OP_SLI:  s_we = 1'b1;
            `OP_HALT: halt = 1'b1;

            default: ;   // OP_NOP -> defaults
        endcase
    end
endmodule
