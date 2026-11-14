// -----------------------------------------------------------------------------
// tb_vector_alu.v - verification of the LANES-wide vector ALU (Step 3)
//
//  Lane arithmetic itself is proven exhaustively in tb_lane_alu. This bench
//  targets what is NEW at the vector level - wiring and lane independence:
//
//   A. Boundary isolation : carries / borrows / shifted-out bits must NOT
//                           leak into the neighbouring lane
//   B. Walking lane       : data in exactly one lane -> only that lane reacts
//                           (catches swapped or mis-sliced lanes)
//   C. Mask patterns      : every 2^LANES compare pattern maps to mask[i]
//   D. Random vectors     : independent random data in every lane, all ops
//   E. Non-ALU opcodes    : whole vector and mask stay zero
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_vector_alu #(
    parameter LANES    = 4,
    parameter WIDTH    = 8,
    parameter N_RANDOM = 5000      // random vectors per opcode
);
    localparam longint MOD  = longint'(1) << WIDTH;
    localparam longint HALF = MOD >> 1;
    localparam VW = LANES * WIDTH;

    // ---- DUT ------------------------------------------------------------------
    reg  [VW-1:0]    va, vb;
    reg  [3:0]       op;
    wire [VW-1:0]    vy;
    wire [LANES-1:0] mask;

    vector_alu #(.LANES(LANES), .WIDTH(WIDTH)) dut (
        .va(va), .vb(vb), .op(op), .vy(vy), .mask(mask)
    );

    `include "ref_alu.vh"

    // ---- Bookkeeping ----------------------------------------------------------
    integer errors = 0, printed = 0, total_checks = 0;
    integer checks [16];
    integer fails  [16];
    string  names  [16];
    bit     mask_seen0 [LANES];      // coverage: each lane's flag seen low...
    bit     mask_seen1 [LANES];      // ...and high

    // Apply one vector op and compare EVERY lane against the reference
    task automatic vcheck(input logic [3:0] o, input logic [VW-1:0] xa, input logic [VW-1:0] xb,
                          input string tag);
        logic [VW-1:0]    exp_v;
        logic [LANES-1:0] exp_m;
        longint r;
        op = o; va = xa; vb = xb;
        #1;
        for (int i = 0; i < LANES; i++) begin
            r = ref_alu(o, longint'(xa[i*WIDTH +: WIDTH]), longint'(xb[i*WIDTH +: WIDTH]));
            exp_v[i*WIDTH +: WIDTH] = r[WIDTH-1:0];
            exp_m[i]                = r[WIDTH];
            if (mask[i]) mask_seen1[i] = 1; else mask_seen0[i] = 1;
        end
        checks[o]++;
        if (vy !== exp_v || mask !== exp_m) begin
            fails[o]++;
            errors++;
            if (printed++ < 12)
                $display("FAIL [%s] %-6s va=%h vb=%h : vy=%h mask=%b, expected vy=%h mask=%b",
                         tag, names[o], xa, xb, vy, mask, exp_v, exp_m);
        end
    endtask

    // Replicate one lane value across all lanes
    function automatic logic [VW-1:0] splat(input longint v);
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = v[WIDTH-1:0];
        return x;
    endfunction

    function automatic logic [VW-1:0] rand_vec();
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = {$urandom, $urandom} % MOD;
        return x;
    endfunction

    logic [3:0] alu_ops   [10];
    logic [3:0] other_ops [6];

    initial begin
        string vcd_file;
        logic [VW-1:0] xa, xb;
        int n_patterns;

        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_vector_alu.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_vector_alu);

        names = '{"NOP","VADD","VSUB","VAND","VOR","VXOR","VCMPEQ","VCMPLT",
                  "VSLL","VSRL","VMUL","SLI","VLD","VST","VBCAST","HALT"};
        foreach (checks[i]) begin checks[i] = 0; fails[i] = 0; end
        foreach (mask_seen0[i]) begin mask_seen0[i] = 0; mask_seen1[i] = 0; end
        alu_ops   = '{`OP_VADD, `OP_VSUB, `OP_VAND, `OP_VOR, `OP_VXOR,
                      `OP_VCMPEQ, `OP_VCMPLT, `OP_VSLL, `OP_VSRL, `OP_VMUL};
        other_ops = '{`OP_NOP, `OP_SLI, `OP_VLD, `OP_VST, `OP_VBCAST, `OP_HALT};

        // ---- A. Lane-boundary isolation ----------------------------------------
        // In a plain LANES*WIDTH-bit adder these would ripple across lanes.
        vcheck(`OP_VADD, splat(MOD - 1), splat(1),     "A carry");   // FF+01 -> 00 per lane
        vcheck(`OP_VSUB, splat(0),       splat(1),     "A borrow");  // 00-01 -> FF per lane
        vcheck(`OP_VSLL, splat(HALF),    splat(1),     "A sll");     // MSB shifted out, not into next lane
        vcheck(`OP_VSRL, splat(1),       splat(1),     "A srl");     // LSB shifted out, not into prev lane
        vcheck(`OP_VMUL, splat(MOD - 1), splat(MOD-1), "A mul");     // FF*FF -> 01, high half dropped

        // ---- B. Walking lane ---------------------------------------------------
        // Lane k holds data, every other lane holds 0. Distinct per-lane values
        // (k+1) so a swapped lane shows up as a wrong value in the wrong place.
        for (int k = 0; k < LANES; k++) begin
            xa = '0; xb = '0;
            xa[k*WIDTH +: WIDTH] = (k + 1) * 3;
            xb[k*WIDTH +: WIDTH] = k + 1;
            foreach (alu_ops[j]) vcheck(alu_ops[j], xa, xb, "B walk");
        end

        // ---- C. Every compare-mask pattern ------------------------------------
        n_patterns = (LANES <= 8) ? (1 << LANES) : 256;
        for (int p = 0; p < n_patterns; p++) begin
            // VCMPEQ: lane i equal iff bit i of p is set
            for (int i = 0; i < LANES; i++) begin
                xa[i*WIDTH +: WIDTH] = i + 5;
                xb[i*WIDTH +: WIDTH] = p[i] ? i + 5 : i + 6;
            end
            vcheck(`OP_VCMPEQ, xa, xb, "C eq");
            // VCMPLT: lane i is (-1 < 1) when set, (1 < -1) when clear  -> signed test
            for (int i = 0; i < LANES; i++) begin
                xa[i*WIDTH +: WIDTH] = p[i] ? MOD - 1 : 1;
                xb[i*WIDTH +: WIDTH] = p[i] ? 1       : MOD - 1;
            end
            vcheck(`OP_VCMPLT, xa, xb, "C lt");
        end

        // ---- D. Random vectors -------------------------------------------------
        $dumpoff;
        foreach (alu_ops[j])
            repeat (N_RANDOM) vcheck(alu_ops[j], rand_vec(), rand_vec(), "D rand");

        // ---- E. Non-ALU opcodes produce nothing --------------------------------
        foreach (other_ops[j])
            repeat (200) vcheck(other_ops[j], rand_vec(), rand_vec(), "E inert");

        // ---- Report --------------------------------------------------------------
        $display("");
        $display("  vector_alu  LANES=%0d  WIDTH=%0d  (%0d-bit vectors)", LANES, WIDTH, VW);
        $display("  opcode   checks  fails");
        $display("  -------  ------  -----");
        for (int i = 0; i < 16; i++) begin
            total_checks += checks[i];
            if (checks[i] == 0) begin
                $display("  coverage hole: opcode %s never exercised", names[i]);
                errors++;
            end
            if (checks[i] > 0)
                $display("  %-7s  %6d  %5d  %s", names[i], checks[i], fails[i],
                         fails[i] == 0 ? "ok" : "<-- FAIL");
        end
        $write("  mask coverage (lane: seen 0/1): ");
        for (int i = 0; i < LANES; i++) begin
            if (mask_seen0[i] && mask_seen1[i]) $write("L%0d:ok ", i);
            else begin $write("L%0d:MISSING ", i); errors++; end
        end
        $display("\n");
        $display("  total checks: %0d", total_checks);
        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
