// =============================================================================
//  prog_runner.v - run many programs on one simd_top build and dump final state
// =============================================================================
//  Not a self-checking testbench (hence no tb_ prefix, so `make all_tests`
//  does not pick it up). scripts/lfrun.py drives it: the Python side writes
//  the programs, runs this simulation once per LANES/WIDTH build, and compares
//  every dumped state with the reference model in scripts/lfmodel.py.
//
//  Plusargs:
//    +prefix=<path>   programs are <path>_<i>.hex and <path>_<i>_data.hex
//    +count=<n>       number of programs, i = 0 .. n-1
//    +out=<file>      where the final states are written
//    +maxcyc=<n>      cycle limit per program (default 1000)
//
//  For each program: hold reset, reload both memories (instruction slots not
//  in the file stay HALT, data words stay 0), release reset, run until halted
//  or the cycle limit, then write
//    P <i>
//    S <cycles run> <cycle_count> <instr_count> <lane_ops> <mask> <scalar> <pc> <halted>
//    V <r> <vreg r>                 for r = 0..7
//    M <addr> <word>                for every non-zero data-memory word
//    E
//  Numbers after the first S field are hexadecimal; unknown bits print as x / X.
// =============================================================================
`timescale 1ns/1ps
`include "simd_defs.vh"

module prog_runner;
    parameter LANES = 4;
    parameter WIDTH = 8;
    localparam VW = LANES * WIDTH;

    reg clk = 1'b0, rst = 1'b1;
    always #5 clk = ~clk;

    wire                 halted;
    wire [`IMEM_AW-1:0]  pc;
    wire [31:0]          cycle_count, instr_count, lane_ops;
    wire [LANES-1:0]     mask_status;
    wire [VW-1:0]        dbg_rdata;

    simd_top #(.LANES(LANES), .WIDTH(WIDTH)) dut (
        .clk(clk), .rst(rst), .halted(halted), .pc_out(pc),
        .cycle_count(cycle_count), .instr_count(instr_count), .lane_ops(lane_ops),
        .mask_status(mask_status),
        .dbg_addr({`DMEM_AW{1'b0}}), .dbg_rdata(dbg_rdata),
        .instr_in({`INSTR_W{1'b0}})
    );

    reg [8*512-1:0] prefix, out_name, imem_file, dmem_file;
    integer count, maxcyc, i, k, r, a, fd, cycles;

    initial begin
        if (!$value$plusargs("prefix=%s", prefix) || !$value$plusargs("count=%d", count) ||
            !$value$plusargs("out=%s", out_name)) begin
            $display("RUNNER ERROR: need +prefix=, +count= and +out=");
            $finish;
        end
        if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 1000;
        fd = $fopen(out_name, "w");
        if (fd == 0) begin
            $display("RUNNER ERROR: cannot open %0s", out_name);
            $finish;
        end

        for (i = 0; i < count; i = i + 1) begin
            // ---- reset and load (reset never writes memory) -----------------
            @(negedge clk);
            rst = 1'b1;
            for (k = 0; k < `IMEM_DEPTH; k = k + 1) dut.imem[k] = {`OP_HALT, 12'h000};
            for (k = 0; k < `DMEM_DEPTH; k = k + 1) dut.dmem[k] = {VW{1'b0}};
            $sformat(imem_file, "%0s_%0d.hex", prefix, i);
            $sformat(dmem_file, "%0s_%0d_data.hex", prefix, i);
            $readmemh(imem_file, dut.imem);
            $readmemh(dmem_file, dut.dmem);
            @(posedge clk);                 // synchronous reset takes effect
            @(negedge clk);
            rst = 1'b0;

            // ---- run: one instruction per rising edge, checked at the falling edge
            cycles = 0;
            while (!halted && cycles < maxcyc) begin
                @(posedge clk);
                @(negedge clk);
                cycles = cycles + 1;
            end

            // ---- dump ---------------------------------------------------------
            $fdisplay(fd, "P %0d", i);
            $fdisplay(fd, "S %0d %h %h %h %h %h %h %h", cycles, cycle_count, instr_count,
                      lane_ops, mask_status, dut.u_rf.sreg, pc, halted);
            for (r = 0; r < `NUM_VREGS; r = r + 1)
                $fdisplay(fd, "V %0d %h", r, dut.u_rf.vregs[r]);
            for (a = 0; a < `DMEM_DEPTH; a = a + 1)
                if (dut.dmem[a] !== {VW{1'b0}})      // !== so words holding X are dumped too
                    $fdisplay(fd, "M %0d %h", a, dut.dmem[a]);
            $fdisplay(fd, "E");
        end
        $fclose(fd);
        $display("RUNNER DONE %0d", count);
        $finish;
    end
endmodule
