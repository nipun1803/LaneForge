// =============================================================================
//  fpga_top.v - minimal-pin wrapper for FPGA resource and Fmax measurement
// =============================================================================
//  The processor has ~150-250 ports (instruction input, debug port, three
//  32-bit counters, ...), more than is sensible to route to package pins. This
//  wrapper keeps the whole design alive with three pins:
//
//    clk      the clock whose maximum frequency is reported
//    rst_in   reset, registered before use
//    out      XOR of every processor output, registered
//
//  * A free-running 32-bit LFSR supplies the instruction word (EXTERNAL_INSTR=1,
//    as in synthesis/simd_synth_top.v) and the debug read address, so every
//    opcode, register address and memory address stays variable and nothing
//    is optimised away. The instruction ROM is excluded, as in `make synth`.
//  * All processor outputs are folded into one registered pin, so they are all
//    observable.
//  * Inputs come from flip-flops and the output goes to a flip-flop, so the
//    reported Fmax is set by the processor's internal register-to-register
//    paths, not by I/O timing.
//
//  The LFSR stream is only a way to keep logic variable for synthesis; it is
//  not a meaningful program. Functional behaviour is verified in simulation.
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module fpga_top #(
    parameter LANES = 4,
    parameter WIDTH = 8
)(
    input  wire clk,
    input  wire rst_in,
    output reg  out
);
    localparam VW = LANES * WIDTH;

    reg rst = 1'b1;
    always @(posedge clk) rst <= rst_in;

    // 32-bit Galois LFSR, taps 32,22,2,1 (maximal length)
    reg [31:0] lfsr = 32'hACE1_2468;
    always @(posedge clk)
        lfsr <= {1'b0, lfsr[31:1]} ^ (lfsr[0] ? 32'h8020_0003 : 32'h0);

    wire                halted;
    wire [`IMEM_AW-1:0] pc;
    wire [31:0]         cycle_count, instr_count, lane_ops;
    wire [LANES-1:0]    mask_status;
    wire [VW-1:0]       dbg_rdata;

    simd_top #(.LANES(LANES), .WIDTH(WIDTH), .EXTERNAL_INSTR(1)) u_cpu (
        .clk(clk), .rst(rst),
        .halted(halted), .pc_out(pc),
        .cycle_count(cycle_count), .instr_count(instr_count), .lane_ops(lane_ops),
        .mask_status(mask_status),
        .dbg_addr(lfsr[`DMEM_AW+15:16]), .dbg_rdata(dbg_rdata),
        .instr_in(lfsr[`INSTR_W-1:0])
    );

    always @(posedge clk)
        out <= ^{halted, pc, cycle_count, instr_count, lane_ops, mask_status, dbg_rdata};
endmodule
