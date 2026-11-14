// -----------------------------------------------------------------------------
// tb_lane_mux.v - verification of the operand-B lane MUX (Step 5)
//
//   A. Pass-through   : sel = 0 -> vb == vs2 exactly (scalar ignored)
//   B. Broadcast      : sel = 1 -> EVERY lane of vb == scalar, vs2 ignored
//                       (exhaustive over all scalar values when WIDTH <= 8)
//   C. bcast output   : always the replicated scalar, regardless of sel
//   D. Lane uniqueness: per-lane check with distinct vs2 lanes, so a lane
//                       stuck on vs2 while others broadcast is caught
//   E. Toggling       : sel flips every step with random data
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_lane_mux #(
    parameter LANES    = 4,
    parameter WIDTH    = 8,
    parameter N_RANDOM = 5000
);
    localparam VW = LANES * WIDTH;
    localparam longint MOD = longint'(1) << WIDTH;
    localparam S_PASS = 0, S_BCAST = 1, S_BOUT = 2, S_LANE = 3, S_TOGGLE = 4, NSEC = 5;

    // ---- DUT ------------------------------------------------------------------
    reg  [VW-1:0]    vs2_data;
    reg  [WIDTH-1:0] scalar;
    reg              sel_scalar;
    wire [VW-1:0]    vb, bcast;

    lane_mux #(.LANES(LANES), .WIDTH(WIDTH)) dut (
        .vs2_data(vs2_data), .scalar(scalar), .sel_scalar(sel_scalar),
        .vb(vb), .bcast(bcast)
    );

    // ---- Checking -------------------------------------------------------------
    integer errors = 0, printed = 0, total_checks = 0;
    integer sec_checks [NSEC];
    string  sec_names  [NSEC];

    function automatic logic [VW-1:0] splat(input logic [WIDTH-1:0] s);
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = s;
        return x;
    endfunction

    function automatic logic [VW-1:0] rand_vec();
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = $urandom;
        return x;
    endfunction

    // Apply inputs, then check vb and bcast lane-by-lane
    task automatic check(input logic [VW-1:0] v, input logic [WIDTH-1:0] s,
                         input bit sel, input int sec);
        logic [WIDTH-1:0] exp_lane;
        vs2_data = v; scalar = s; sel_scalar = sel;
        #1;
        sec_checks[sec]++;
        for (int i = 0; i < LANES; i++) begin
            exp_lane = sel ? s : v[i*WIDTH +: WIDTH];
            if (vb[i*WIDTH +: WIDTH] !== exp_lane || bcast[i*WIDTH +: WIDTH] !== s) begin
                errors++;
                if (printed++ < 12)
                    $display("FAIL [%s] lane %0d sel=%b vs2=%h s=%h : vb=%h bcast=%h",
                             sec_names[sec], i, sel, v, s, vb, bcast);
            end
        end
    endtask

    initial begin
        string vcd_file;
        logic [VW-1:0] v;
        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_lane_mux.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_lane_mux);

        sec_names = '{"A pass", "B bcast", "C bcast-out", "D lanes", "E toggle"};
        foreach (sec_checks[i]) sec_checks[i] = 0;

        // A. sel = 0: vb is vs2, whatever the scalar is ---------------------------
        check('0, '1, 0, S_PASS);
        check('1, '0, 0, S_PASS);
        repeat (N_RANDOM) check(rand_vec(), $urandom, 0, S_PASS);

        // B. sel = 1: every lane is the scalar, whatever vs2 is ------------------
        if (WIDTH <= 8) begin
            for (longint s = 0; s < MOD; s++) begin
                check(rand_vec(), s, 1, S_BCAST);
                check('0,         s, 1, S_BCAST);
                check('1,         s, 1, S_BCAST);
            end
        end else
            repeat (N_RANDOM) check(rand_vec(), $urandom, 1, S_BCAST);

        // C. bcast output is independent of sel (checked inside every call;
        //    here with sel both ways on the same data)
        repeat (1000) begin
            v = rand_vec();
            check(v, $urandom, 0, S_BOUT);
            check(v, $urandom, 1, S_BOUT);
        end

        // D. Distinct vs2 lanes, scalar differs from all of them ---------------
        for (int i = 0; i < LANES; i++) v[i*WIDTH +: WIDTH] = i + 1;
        check(v, 8'hEE, 0, S_LANE);
        check(v, 8'hEE, 1, S_LANE);

        // E. Rapid toggling --------------------------------------------------------
        repeat (N_RANDOM) check(rand_vec(), $urandom, $urandom % 2, S_TOGGLE);

        // ---- Report --------------------------------------------------------------
        $display("");
        $display("  lane_mux  LANES=%0d  WIDTH=%0d", LANES, WIDTH);
        for (int i = 0; i < NSEC; i++) begin
            $display("  %-12s %6d checks", sec_names[i], sec_checks[i]);
            total_checks += sec_checks[i];
            if (sec_checks[i] == 0) begin
                $display("  coverage hole: section %s never exercised", sec_names[i]);
                errors++;
            end
        end
        $display("  total checks: %0d", total_checks);
        $display("");
        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
