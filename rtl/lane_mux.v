// =============================================================================
//  lane_mux.v - operand-B select: vector register OR broadcast scalar
// =============================================================================
//  R-type instructions carry an s bit (instr[`F_S]):
//      s = 0 :  vb = vs2                     e.g. VADD v3, v1, v2
//      s = 1 :  vb = {scalar, scalar, ...}   e.g. VADD v3, v1, #s  (add s to every lane)
//
//  Broadcasting is just WIRING: every lane's B input is connected to the same
//  WIDTH-bit scalar - no logic, no extra cycles. The only real hardware here
//  is one 2:1 mux per bit of the vector.
//
//  'bcast' (the replicated scalar) is also exported so VBCAST can write it to a
//  vector register through the result combiner without duplicating this logic.
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module lane_mux #(
    parameter LANES = 4,
    parameter WIDTH = 8
)(
    input  wire [LANES*WIDTH-1:0] vs2_data,     // from regfile read port 2
    input  wire [WIDTH-1:0]       scalar,       // from the scalar register
    input  wire                   sel_scalar,   // instr[`F_S] (from control unit)
    output wire [LANES*WIDTH-1:0] vb,           // operand B into the vector ALU
    output wire [LANES*WIDTH-1:0] bcast         // scalar replicated to all lanes
);
    assign bcast = {LANES{scalar}};
    assign vb    = sel_scalar ? bcast : vs2_data;
endmodule
