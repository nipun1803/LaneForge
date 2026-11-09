// -----------------------------------------------------------------------------
// tb_vector_regfile.v - verification of the vector register file (Step 4)
//
//   A. Reset            : every vector register and the scalar read 0
//   B. Write / readback : unique pattern per register, read on BOTH ports
//   C. Port independence: all 8x8 (raddr1, raddr2) pairs in the same cycle
//   D. Read-during-write: old value before the clock edge, new value after
//   E. Write enable     : we = 0 / s_we = 0 must not change anything
//   F. Isolation        : vector writes never touch the scalar and vice versa
//   G. Random traffic   : random reads/writes checked against a shadow model
//   H. Reset mid-run    : clears everything again
//
//  Stimulus is applied on the falling edge; the DUT writes on the rising edge.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_vector_regfile #(
    parameter LANES    = 4,
    parameter WIDTH    = 8,
    parameter N_RANDOM = 5000
);
    localparam VW = LANES * WIDTH;
    localparam NR = `NUM_VREGS;

    // ---- DUT ------------------------------------------------------------------
    reg                  clk = 0, rst = 0;
    reg  [`VREG_AW-1:0]  raddr1 = 0, raddr2 = 0, waddr = 0;
    reg                  we = 0, s_we = 0;
    reg  [VW-1:0]        wdata = 0;
    reg  [WIDTH-1:0]     s_wdata = 0;
    wire [VW-1:0]        rdata1, rdata2;
    wire [WIDTH-1:0]     scalar;

    vector_regfile #(.LANES(LANES), .WIDTH(WIDTH)) dut (
        .clk(clk), .rst(rst),
        .raddr1(raddr1), .rdata1(rdata1),
        .raddr2(raddr2), .rdata2(rdata2),
        .we(we), .waddr(waddr), .wdata(wdata),
        .s_we(s_we), .s_wdata(s_wdata), .scalar(scalar)
    );

    always #5 clk = ~clk;

    // ---- Shadow model ---------------------------------------------------------
    logic [VW-1:0]    model   [NR];
    logic [WIDTH-1:0] model_s;

    // ---- Checking helpers -----------------------------------------------------
    integer errors = 0, checks = 0, printed = 0;
    // Per-section check counters (section index = letter: A=0 ... H=7)
    localparam NSEC = 8;
    localparam S_RESET = 0, S_READBACK = 1, S_PORTS = 2, S_RDW   = 3,
               S_WE0   = 4, S_ISOLATE  = 5, S_RANDOM = 6, S_RESET2 = 7;
    integer sec_checks [NSEC];
    string  sec_names  [NSEC];

    task automatic expect_eq(input logic [VW-1:0] got, input logic [VW-1:0] exp,
                             input int sec, input string what);
        checks++;
        sec_checks[sec]++;
        if (got !== exp) begin
            errors++;
            if (printed++ < 12)
                $display("FAIL [%s] %s : got %h, expected %h", sec_names[sec], what, got, exp);
        end
    endtask

    // Read both ports combinationally and compare with the shadow model
    task automatic read_check(input int r1, input int r2, input int sec);
        raddr1 = r1; raddr2 = r2;
        #1;
        expect_eq(rdata1, model[r1], sec, $sformatf("port1 v%0d", r1));
        expect_eq(rdata2, model[r2], sec, $sformatf("port2 v%0d", r2));
    endtask

    task automatic check_scalar(input int sec);
        expect_eq(VW'(scalar), VW'(model_s), sec, "scalar");
    endtask

    // One clock cycle with the given write controls (applied at negedge)
    task automatic cycle(input bit w, input int wa, input logic [VW-1:0] wd,
                         input bit sw, input logic [WIDTH-1:0] sd);
        @(negedge clk);
        we = w; waddr = wa; wdata = wd; s_we = sw; s_wdata = sd;
        @(posedge clk); #1;
        if (w)  model[wa] = wd;
        if (sw) model_s   = sd;
        @(negedge clk);
        we = 0; s_we = 0;
    endtask

    task automatic do_reset();
        @(negedge clk); rst = 1;
        @(posedge clk); #1;
        @(negedge clk); rst = 0;
        foreach (model[i]) model[i] = '0;
        model_s = '0;
    endtask

    function automatic logic [VW-1:0] rand_vec();
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = $urandom;
        return x;
    endfunction

    // Pattern that is unique per register AND per lane: lane i of vR = {R, i}
    function automatic logic [VW-1:0] tag_vec(input int r);
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = (r << 4) | i;
        return x;
    endfunction

    // ---- Test sequence --------------------------------------------------------
    initial begin
        string vcd_file;
        logic [VW-1:0] old_v, new_v;
        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_vector_regfile.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_vector_regfile);
        sec_names = '{"A reset", "B readback", "C ports", "D rdw",
                      "E we=0", "F isolate", "G random", "H reset"};
        foreach (sec_checks[i]) sec_checks[i] = 0;

        // A. Reset ---------------------------------------------------------------
        // Dirty the registers first so reset has something to clear
        for (int r = 0; r < NR; r++) cycle(1, r, rand_vec(), 0, 0);
        cycle(0, 0, 0, 1, 8'hA5);
        do_reset();
        for (int r = 0; r < NR; r++) read_check(r, r, S_RESET);
        check_scalar(S_RESET);

        // B. Write unique tag to each register, read back on both ports --------
        for (int r = 0; r < NR; r++) cycle(1, r, tag_vec(r), 0, 0);
        for (int r = 0; r < NR; r++) read_check(r, NR - 1 - r, S_READBACK);

        // C. Every (raddr1, raddr2) pair in the same cycle ---------------------
        for (int r1 = 0; r1 < NR; r1++)
            for (int r2 = 0; r2 < NR; r2++)
                read_check(r1, r2, S_PORTS);

        // D. Read-during-write: old before edge, new after ----------------------
        for (int r = 0; r < NR; r++) begin
            old_v = model[r];
            new_v = ~old_v;
            @(negedge clk);
            we = 1; waddr = r; wdata = new_v;
            raddr1 = r; raddr2 = r;
            #1;  // write is pending; edge has not arrived
            expect_eq(rdata1, old_v, S_RDW, $sformatf("v%0d before edge (port1)", r));
            expect_eq(rdata2, old_v, S_RDW, $sformatf("v%0d before edge (port2)", r));
            @(posedge clk); #1;
            model[r] = new_v;
            expect_eq(rdata1, new_v, S_RDW, $sformatf("v%0d after edge (port1)", r));
            expect_eq(rdata2, new_v, S_RDW, $sformatf("v%0d after edge (port2)", r));
            @(negedge clk); we = 0;
        end

        // E. Write enables low -> nothing changes -------------------------------
        for (int r = 0; r < NR; r++) begin
            @(negedge clk);
            we = 0; waddr = r; wdata = rand_vec();
            s_we = 0; s_wdata = $urandom;
            @(posedge clk); #1;
        end
        for (int r = 0; r < NR; r++) read_check(r, r, S_WE0);
        check_scalar(S_WE0);

        // F. Vector / scalar isolation ------------------------------------------
        cycle(0, 0, 0, 1, 8'h3C);                       // scalar write only
        for (int r = 0; r < NR; r++) read_check(r, r, S_ISOLATE);
        check_scalar(S_ISOLATE);
        for (int r = 0; r < NR; r++) cycle(1, r, rand_vec(), 0, 0);  // vector writes only
        check_scalar(S_ISOLATE);
        cycle(1, 3, rand_vec(), 1, 8'hC3);              // both in the same cycle
        read_check(3, 3, S_ISOLATE);
        check_scalar(S_ISOLATE);

        // G. Random traffic vs shadow model --------------------------------------
        $dumpoff;
        repeat (N_RANDOM) begin
            cycle($urandom % 2, $urandom % NR, rand_vec(), ($urandom % 4) == 0, $urandom);
            read_check($urandom % NR, $urandom % NR, S_RANDOM);
            check_scalar(S_RANDOM);
        end
        $dumpon;

        // H. Reset mid-run -------------------------------------------------------
        do_reset();
        for (int r = 0; r < NR; r++) read_check(r, r, S_RESET2);
        check_scalar(S_RESET2);

        // ---- Report --------------------------------------------------------------
        $display("");
        $display("  vector_regfile  LANES=%0d  WIDTH=%0d  (%0d x %0d-bit + %0d-bit scalar)",
                 LANES, WIDTH, NR, VW, WIDTH);
        for (int i = 0; i < NSEC; i++)
            $display("  %-12s %6d checks", sec_names[i], sec_checks[i]);
        $display("  total        %6d checks, %0d errors", checks, errors);
        $display("");
        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
