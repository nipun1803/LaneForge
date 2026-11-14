// =============================================================================
//  simd_synth_top.v - generic resource estimation with variable instructions
// =============================================================================
//  instr_in is a top-level input, so synthesis can retain every opcode and
//  register/memory address instead of specialising the core for a fixed program.
//  The original simd_top supplies the datapath, data memory, PC and counters.
//  Instruction ROM is bypassed and excluded from this estimate. This is not an
//  FPGA board top: generic cell counts do not measure FPGA LUT use or Fmax.
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module simd_synth_top #(
    parameter LANES = 4,
    parameter WIDTH = 8
)(
    input  wire                     clk,
    input  wire                     rst,
    input  wire [`INSTR_W-1:0]       instr_in,
    input  wire [`DMEM_AW-1:0]       dbg_addr,
    output wire [LANES*WIDTH-1:0]    dbg_rdata,
    output wire                     halted,
    output wire [`IMEM_AW-1:0]       pc_out,
    output wire [31:0]              cycle_count,
    output wire [31:0]              instr_count,
    output wire [31:0]              lane_ops,
    output wire [LANES-1:0]          mask_status
);
    simd_top #(
        .LANES(LANES), .WIDTH(WIDTH), .EXTERNAL_INSTR(1)
    ) u_processor (
        .clk(clk), .rst(rst), .instr_in(instr_in),
        .dbg_addr(dbg_addr), .dbg_rdata(dbg_rdata),
        .halted(halted), .pc_out(pc_out),
        .cycle_count(cycle_count), .instr_count(instr_count),
        .lane_ops(lane_ops), .mask_status(mask_status)
    );
endmodule
