// =============================================================================
//  vector_regfile.v - 8 vector registers (2 read / 1 write) + 1 scalar register
// =============================================================================
//  Vector registers v0..v7, each LANES*WIDTH bits (lane 0 in the LSBs).
//
//  Timing (single-cycle datapath):
//    * Reads are COMBINATIONAL - rdata follows raddr within the same cycle.
//    * Writes happen on the RISING clock edge when we = 1.
//    => A read of the register being written returns the OLD value until the
//       edge. That is exactly what `VADD v1, v1, v2` needs: the instruction
//       reads the old v1 and its result replaces v1 at the end of the cycle.
//
//  Scalar register (WIDTH bits): written by SLI, read by VBCAST and by any
//  R-type instruction with s = 1 (broadcast operand). Independent write port.
//
//  Reset: synchronous, active-high, clears every vector and the scalar.
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module vector_regfile #(
    parameter LANES = 4,
    parameter WIDTH = 8
)(
    input  wire                     clk,
    input  wire                     rst,

    // Read port 1 (vs1)
    input  wire [`VREG_AW-1:0]      raddr1,
    output wire [LANES*WIDTH-1:0]   rdata1,

    // Read port 2 (vs2, or the VST source register)
    input  wire [`VREG_AW-1:0]      raddr2,
    output wire [LANES*WIDTH-1:0]   rdata2,

    // Write port (vd)
    input  wire                     we,
    input  wire [`VREG_AW-1:0]      waddr,
    input  wire [LANES*WIDTH-1:0]   wdata,

    // Scalar register
    input  wire                     s_we,
    input  wire [WIDTH-1:0]         s_wdata,
    output wire [WIDTH-1:0]         scalar
);
    localparam VW = LANES * WIDTH;

    reg [VW-1:0]    vregs [0:`NUM_VREGS-1];
    reg [WIDTH-1:0] sreg;

    // ---- Combinational reads ------------------------------------------------
    assign rdata1 = vregs[raddr1];
    assign rdata2 = vregs[raddr2];
    assign scalar = sreg;

    // ---- Clocked writes -------------------------------------------------------
    integer k;
    always @(posedge clk) begin
        if (rst) begin
            for (k = 0; k < `NUM_VREGS; k = k + 1)
                vregs[k] <= {VW{1'b0}};
            sreg <= {WIDTH{1'b0}};
        end else begin
            if (we)   vregs[waddr] <= wdata;
            if (s_we) sreg         <= s_wdata;
        end
    end
endmodule
