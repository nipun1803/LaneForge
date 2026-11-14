// =============================================================================
//  run_programs.v - load the example programs from hex files and check them
// -----------------------------------------------------------------------------
//  Runs the three examples from docs/programming_guide.md on simd_top
//  (LANES=4, WIDTH=8), loading each through the IMEM_INIT / DMEM_INIT
//  parameters exactly as a user would.  For each program it prints the result
//  word, cycle count, lane operations and lane mask, and compares them with the
//  values documented in the guide.
//
//  Run from the repository root:   make demo
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module run_programs;
    localparam LANES = 4, WIDTH = 8;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    integer errors = 0;

    // One processor instance per program; they share clock and reset.
    `define PROG(name, imem, dmem)                                            \
        wire halted_``name;                                                   \
        wire [`IMEM_AW-1:0] pc_``name;                                        \
        wire [31:0] cyc_``name, ins_``name, ops_``name;                       \
        wire [LANES-1:0] mask_``name;                                         \
        reg  [`DMEM_AW-1:0] addr_``name = 0;                                  \
        wire [LANES*WIDTH-1:0] data_``name;                                   \
        simd_top #(.LANES(LANES), .WIDTH(WIDTH),                              \
                   .IMEM_INIT(imem), .DMEM_INIT(dmem)) u_``name (             \
            .clk(clk), .rst(rst), .halted(halted_``name), .pc_out(pc_``name), \
            .cycle_count(cyc_``name), .instr_count(ins_``name),               \
            .lane_ops(ops_``name), .mask_status(mask_``name),                 \
            .dbg_addr(addr_``name), .dbg_rdata(data_``name));

    `PROG(add,       "programs/add.hex",       "programs/add_data.hex")
    `PROG(add_const, "programs/add_const.hex", "programs/add_const_data.hex")
    `PROG(threshold, "programs/threshold.hex", "programs/threshold_data.hex")

    task check(input [8*12-1:0] name,
               input [31:0] got,  input [31:0] exp,
               input [31:0] cyc,  input [31:0] exp_cyc,
               input [31:0] ops,  input [LANES-1:0] mask);
        begin
            $display("%-10s result=%h (expected %h)  cycles=%0d  lane_ops=%0d  mask=%b  %s",
                     name, got, exp, cyc, ops, mask,
                     (got === exp && cyc == exp_cyc) ? "OK" : "MISMATCH");
            if (got !== exp || cyc != exp_cyc) errors = errors + 1;
        end
    endtask

    initial begin
        @(negedge clk); rst = 0;
        wait (halted_add && halted_add_const && halted_threshold);
        @(negedge clk);

        // Each program stores its result to the address named in the guide.
        addr_add = 2;  addr_add_const = 1;  addr_threshold = 1;  #1;

        $display("--- example programs (LANES=%0d, WIDTH=%0d) ---", LANES, WIDTH);
        check("add",       data_add,       32'h14131211, cyc_add,       5, ops_add,       mask_add);
        check("add_const", data_add_const, 32'h2D190F06, cyc_add_const, 5, ops_add_const, mask_add_const);
        check("threshold", data_threshold, 32'h00010003, cyc_threshold, 6, ops_threshold, mask_threshold);
        if (mask_threshold !== 4'b0101) begin
            $display("threshold  mask=%b, expected 0101  MISMATCH", mask_threshold);
            errors = errors + 1;
        end

        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED: %0d mismatch(es)", errors);
        $finish;
    end

    initial begin
        #10000 $display("TIMEOUT: a program did not halt"); $finish;
    end
endmodule
