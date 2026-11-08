// =============================================================================
//  simd_top.v - single-cycle SIMD vector processing unit (top level)
// =============================================================================
//  Every cycle (CPI = 1):
//    fetch   instr = IMEM[pc]
//    decode  vector_control -> control signals
//    read    vs1, vs2 / scalar from the register file
//    execute LANES lane ALUs in parallel under one opcode
//    select  result_combiner picks ALU / memory / broadcast
//    write   regfile, scalar, data memory, mask status on the rising edge
//
//  Memories (inside the top, preloaded by the testbench):
//    IMEM  `IMEM_DEPTH x 16          initialised to HALT, so running off the end of
//                                    a program stops cleanly instead of looping
//    DMEM  `DMEM_DEPTH x LANES*WIDTH  one word = one full vector
//                                    combinational read, synchronous write
//
//  HALT: when the HALT instruction executes, 'halted' is set at the clock edge
//  and the PC stays pointing AT the HALT (so pc_out shows where it stopped).
//  From then on the PC, every counter and ALL architectural writes are frozen
//  (the decoder's write enables are gated with !halted here, in one place).
//
//  Counters (for the scalar-vs-SIMD study):
//    cycle_count : clock cycles spent executing, including the HALT itself
//    instr_count : instructions retired (== cycle_count, because CPI = 1)
//    lane_ops    : element operations done by the vector ALU
//                  (+LANES per VADD..VMUL) -> throughput = lane_ops / cycle_count
//
//  Debug port: dbg_addr / dbg_rdata read DMEM asynchronously. Testbenches use it
//  to check results, and it keeps synthesis from optimising the datapath away
//  (results would otherwise have no path to an output).
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module simd_top #(
    parameter LANES     = 4,
    parameter WIDTH     = 8,
    parameter IMEM_INIT = "",          // optional $readmemh file for IMEM
    parameter DMEM_INIT = ""           // optional $readmemh file for DMEM
)(
    input  wire                     clk,
    input  wire                     rst,           // synchronous, active-high

    // Status
    output reg                      halted,
    output wire [`IMEM_AW-1:0]      pc_out,
    output reg  [31:0]              cycle_count,
    output reg  [31:0]              instr_count,
    output reg  [31:0]              lane_ops,
    output reg  [LANES-1:0]         mask_status,   // lane mask of the last compare

    // Debug read port into data memory
    input  wire [`DMEM_AW-1:0]      dbg_addr,
    output wire [LANES*WIDTH-1:0]   dbg_rdata
);
    localparam VW = LANES * WIDTH;

    // =========================================================================
    //  Memories and program counter
    // =========================================================================
    reg [`INSTR_W-1:0] imem [0:`IMEM_DEPTH-1];
    reg [VW-1:0]       dmem [0:`DMEM_DEPTH-1];
    reg [`IMEM_AW-1:0] pc;

    integer k;
    initial begin
        for (k = 0; k < `IMEM_DEPTH; k = k + 1) imem[k] = {`OP_HALT, 12'h000};
        for (k = 0; k < `DMEM_DEPTH; k = k + 1) dmem[k] = {VW{1'b0}};
        if (IMEM_INIT != "") $readmemh(IMEM_INIT, imem);
        if (DMEM_INIT != "") $readmemh(DMEM_INIT, dmem);
    end

    wire [`INSTR_W-1:0] instr = imem[pc];
    assign pc_out = pc;

    // =========================================================================
    //  Decode
    // =========================================================================
    wire [3:0]           alu_op;
    wire                 sel_scalar;
    wire [`VREG_AW-1:0]  raddr1, raddr2, waddr;
    wire                 rf_we, s_we, mem_we, mask_we, halt;
    wire [`WB_SEL_W-1:0] wb_sel;
    wire [7:0]           s_imm;
    wire [`DMEM_AW-1:0]  mem_addr;

    vector_control u_ctrl (
        .instr(instr),
        .alu_op(alu_op), .sel_scalar(sel_scalar),
        .raddr1(raddr1), .raddr2(raddr2), .waddr(waddr),
        .rf_we(rf_we), .wb_sel(wb_sel),
        .s_we(s_we), .s_imm(s_imm),
        .mem_we(mem_we), .mem_addr(mem_addr),
        .mask_we(mask_we), .halt(halt)
    );

    // Global write gating: nothing architectural changes once halted
    wire run       = ~halted;
    wire rf_we_g   = rf_we   & run;
    wire s_we_g    = s_we    & run;
    wire mem_we_g  = mem_we  & run;
    wire mask_we_g = mask_we & run;

    // imm8 -> WIDTH bits (zero-extend if WIDTH > 8, truncate if WIDTH < 8)
    wire [WIDTH-1:0] s_wdata;
    generate
        if (WIDTH >= 8) begin : g_imm_ext
            assign s_wdata = {{(WIDTH-8){1'b0}}, s_imm};
        end else begin : g_imm_trunc
            assign s_wdata = s_imm[WIDTH-1:0];
        end
    endgenerate

    // =========================================================================
    //  Datapath
    // =========================================================================
    wire [VW-1:0]    rdata1, rdata2, vb, bcast, vy, wb_data, mem_rdata;
    wire [WIDTH-1:0] scalar;
    wire [LANES-1:0] alu_mask, mask_out;
    wire             any_true, all_true;
    wire [$clog2(LANES+1)-1:0] mask_count;

    vector_regfile #(.LANES(LANES), .WIDTH(WIDTH)) u_rf (
        .clk(clk), .rst(rst),
        .raddr1(raddr1), .rdata1(rdata1),
        .raddr2(raddr2), .rdata2(rdata2),
        .we(rf_we_g), .waddr(waddr), .wdata(wb_data),
        .s_we(s_we_g), .s_wdata(s_wdata), .scalar(scalar)
    );

    lane_mux #(.LANES(LANES), .WIDTH(WIDTH)) u_mux (
        .vs2_data(rdata2), .scalar(scalar), .sel_scalar(sel_scalar),
        .vb(vb), .bcast(bcast)
    );

    vector_alu #(.LANES(LANES), .WIDTH(WIDTH)) u_valu (
        .va(rdata1), .vb(vb), .op(alu_op), .vy(vy), .mask(alu_mask)
    );

    result_combiner #(.LANES(LANES), .WIDTH(WIDTH)) u_rc (
        .wb_sel(wb_sel), .alu_y(vy), .alu_mask(alu_mask),
        .mem_rdata(mem_rdata), .bcast(bcast),
        .wb_data(wb_data), .mask_out(mask_out),
        .any_true(any_true), .all_true(all_true), .mask_count(mask_count)
    );

    // =========================================================================
    //  Data memory: combinational read, write on the rising edge (VST)
    //  Store data comes from read port 2 (the decoder sets raddr2 = vreg).
    // =========================================================================
    assign mem_rdata = dmem[mem_addr];
    assign dbg_rdata = dmem[dbg_addr];

    always @(posedge clk) begin
        if (mem_we_g) dmem[mem_addr] <= rdata2;
    end

    // =========================================================================
    //  Sequencing, status and performance counters
    // =========================================================================
    wire is_alu = (alu_op != `OP_NOP);

    always @(posedge clk) begin
        if (rst) begin
            pc          <= {`IMEM_AW{1'b0}};
            halted      <= 1'b0;
            cycle_count <= 32'd0;
            instr_count <= 32'd0;
            lane_ops    <= 32'd0;
            mask_status <= {LANES{1'b0}};
        end else if (run) begin
            if (!halt)     pc          <= pc + 1'b1;   // PC stays ON the HALT
            cycle_count <= cycle_count + 32'd1;
            instr_count <= instr_count + 32'd1;
            if (is_alu)    lane_ops    <= lane_ops + LANES;
            if (mask_we_g) mask_status <= mask_out;
            if (halt)      halted      <= 1'b1;
        end
    end
endmodule
