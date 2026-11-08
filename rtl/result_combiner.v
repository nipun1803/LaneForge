// =============================================================================
//  result_combiner.v - write-back select + compare-mask reduction
// =============================================================================
//  1) Write-back select: chooses the vector written to vd this cycle
//        wb_sel = `WB_ALU   -> vector ALU result (lanes already concatenated)
//        wb_sel = `WB_MEM   -> data memory word        (VLD)
//        wb_sel = `WB_BCAST -> scalar in every lane    (VBCAST)
//        reserved code      -> zero (safe default, never X)
//
//  2) Mask reduction: turns the per-lane compare flags into status signals
//        mask_out   : the LANES-bit mask itself (bit i = lane i true)
//        any_true   : OR  of all lanes   ("did any element match?")
//        all_true   : AND of all lanes   ("did every element match?")
//        mask_count : number of true lanes (popcount), 0..LANES
//     The lane ALU drives flag = 0 for non-compare ops, so these read as
//     "nothing true" outside VCMPEQ / VCMPLT.
//
//  Purely combinational. The decision of WHICH source to use is made in the
//  control unit; this block only implements the selection.
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module result_combiner #(
    parameter LANES = 4,
    parameter WIDTH = 8
)(
    input  wire [`WB_SEL_W-1:0]       wb_sel,
    input  wire [LANES*WIDTH-1:0]     alu_y,       // from vector_alu.vy
    input  wire [LANES-1:0]           alu_mask,    // from vector_alu.mask
    input  wire [LANES*WIDTH-1:0]     mem_rdata,   // from data memory
    input  wire [LANES*WIDTH-1:0]     bcast,       // from lane_mux.bcast

    output reg  [LANES*WIDTH-1:0]     wb_data,     // to regfile write port
    output wire [LANES-1:0]           mask_out,
    output wire                       any_true,
    output wire                       all_true,
    output reg  [$clog2(LANES+1)-1:0] mask_count
);
    // ---- Write-back select ----------------------------------------------------
    always @(*) begin
        case (wb_sel)
            `WB_ALU:   wb_data = alu_y;
            `WB_MEM:   wb_data = mem_rdata;
            `WB_BCAST: wb_data = bcast;
            default:   wb_data = {LANES*WIDTH{1'b0}};
        endcase
    end

    // ---- Mask reduction -------------------------------------------------------
    assign mask_out = alu_mask;
    assign any_true = |alu_mask;     // reduction OR
    assign all_true = &alu_mask;     // reduction AND

    integer i;
    always @(*) begin
        mask_count = 0;
        for (i = 0; i < LANES; i = i + 1)
            mask_count = mask_count + alu_mask[i];
    end
endmodule
