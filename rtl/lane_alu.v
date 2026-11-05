// =============================================================================
//  lane_alu.v - one SIMD lane: a purely combinational WIDTH-bit ALU
// =============================================================================
//  The vector ALU (Step 3) instantiates LANES copies of this module, all
//  driven by the same 'op'. That shared opcode is what makes the unit SIMD.
//
//  Outputs
//    y    : lane result
//    flag : 1 when a compare (VCMPEQ / VCMPLT) is true, else 0.
//           Gathered across lanes into the per-lane mask.
//
//  Compare results are all-ones / all-zeros in y (e.g. 8'hFF / 8'h00) so the
//  result can be used directly as a bit-mask with VAND / VOR.
//  Non-ALU opcodes (NOP, SLI, VLD, VST, VBCAST, HALT) must give y = 0, flag = 0.
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module lane_alu #(
    parameter WIDTH = 8
)(
    input  wire [WIDTH-1:0] a,      // operand A (from vs1)
    input  wire [WIDTH-1:0] b,      // operand B (from vs2, or broadcast scalar)
    input  wire [3:0]       op,     // opcode from instr[`F_OP]
    output reg  [WIDTH-1:0] y,      // lane result
    output reg              flag    // compare-true flag
);
    // Shift amount = low log2(WIDTH) bits of b (WIDTH=8 -> b[2:0], 0..7)
    localparam SHW = $clog2(WIDTH);
    wire [SHW-1:0] shamt = b[SHW-1:0];

    always @(*) begin
        // Defaults first: every path assigns y and flag -> no inferred latches
        y    = {WIDTH{1'b0}};
        flag = 1'b0;

        case (op)
            // Arithmetic: results wrap modulo 2^WIDTH (carry/borrow dropped)
            `OP_VADD:   y = a + b;
            `OP_VSUB:   y = a - b;

            // Bitwise logic
            `OP_VAND:   y = a & b;
            `OP_VOR:    y = a | b;
            `OP_VXOR:   y = a ^ b;

            // Compares: flag feeds the lane mask; y = all-ones / all-zeros
            // so the result can be used directly as a bit-mask
            `OP_VCMPEQ: begin
                flag = (a == b);
                y    = {WIDTH{flag}};
            end
            `OP_VCMPLT: begin
                flag = ($signed(a) < $signed(b));   // signed: 8'hFF (-1) < 8'h01
                y    = {WIDTH{flag}};
            end

            // Logical shifts by the low log2(WIDTH) bits of b
            `OP_VSLL:   y = a << shamt;
            `OP_VSRL:   y = a >> shamt;

            // Multiply: y is WIDTH bits, so the upper half of the product is dropped
            `OP_VMUL:   y = a * b;

            default: ;  // NOP, SLI, VLD, VST, VBCAST, HALT -> defaults (0)
        endcase
    end
endmodule
