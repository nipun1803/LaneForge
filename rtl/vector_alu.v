// =============================================================================
//  vector_alu.v - LANES parallel lane ALUs sharing ONE opcode (the SIMD core)
// =============================================================================
//  Vector layout (lane 0 in the least-significant bits):
//
//    bit:  [LANES*WIDTH-1 ......................................... 0]
//          | lane LANES-1 | ... | lane 2 | lane 1 | lane 0 |
//    lane i occupies bits [i*WIDTH +: WIDTH]
//
//  Each lane is a completely independent lane_alu instance: no carry, borrow
//  or shift bits ever cross a lane boundary. The ONLY signal shared by all
//  lanes is 'op' -> single instruction, multiple data.
//
//  mask[i] = compare flag of lane i (meaningful for VCMPEQ / VCMPLT).
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module vector_alu #(
    parameter LANES = 4,
    parameter WIDTH = 8
)(
    input  wire [LANES*WIDTH-1:0] va,     // vector operand A (vs1)
    input  wire [LANES*WIDTH-1:0] vb,     // vector operand B (vs2 or broadcast)
    input  wire [3:0]             op,     // one opcode, broadcast to every lane
    output wire [LANES*WIDTH-1:0] vy,     // vector result
    output wire [LANES-1:0]       mask    // per-lane compare flags
);
    genvar i;
    generate
        for (i = 0; i < LANES; i = i + 1) begin : g_lane
            lane_alu #(.WIDTH(WIDTH)) u_lane (
                .a    (va[i*WIDTH +: WIDTH]),
                .b    (vb[i*WIDTH +: WIDTH]),
                .op   (op),
                .y    (vy[i*WIDTH +: WIDTH]),
                .flag (mask[i])
            );
        end
    endgenerate
endmodule
