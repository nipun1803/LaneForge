// -----------------------------------------------------------------------------
// tb_lane_alu.v - verification of one SIMD lane ALU (Step 2)
//
//   Phase A: directed edge cases (overflow, signed boundaries, shift limits)
//   Phase B: EXHAUSTIVE over all (a, b) when WIDTH <= 8, else N random pairs
//   Phase C: non-ALU opcodes must produce y = 0, flag = 0
//
//   The reference model uses integer arithmetic (mod 2^WIDTH, explicit
//   two's-complement conversion) rather than Verilog truncation / $signed,
//   so it doesn't share the same bugs as the RTL.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_lane_alu #(
    parameter LANES = 4,          // unused here; accepted so the Makefile -P works
    parameter WIDTH = 8,
    parameter N_RANDOM = 20000    // per-opcode random count when WIDTH > 8
);
    localparam longint MOD  = longint'(1) << WIDTH;
    localparam longint HALF = MOD >> 1;
    localparam SHW = $clog2(WIDTH);

    // ---- DUT ------------------------------------------------------------------
    reg  [WIDTH-1:0] a, b;
    reg  [3:0]       op;
    wire [WIDTH-1:0] y;
    wire             flag;

    lane_alu #(.WIDTH(WIDTH)) dut (.a(a), .b(b), .op(op), .y(y), .flag(flag));

    // ---- Reference model (shared with tb_vector_alu) ---------------------------
    `include "ref_alu.vh"

    // ---- Checking -------------------------------------------------------------
    integer errors = 0, total_checks = 0;
    integer checks [16];
    integer fails  [16];
    string  names  [16];

    task automatic apply_and_check(input logic [3:0] o, input longint ua, input longint ub);
        longint exp;
        logic [WIDTH-1:0] exp_y;
        logic             exp_f;
        op = o; a = ua[WIDTH-1:0]; b = ub[WIDTH-1:0];
        #1;
        exp   = ref_alu(o, ua, ub);
        exp_y = exp[WIDTH-1:0];
        exp_f = exp[WIDTH];
        checks[o]++;
        if (y !== exp_y || flag !== exp_f) begin
            fails[o]++;
            errors++;
            if (fails[o] <= 3)   // print first few mismatches per opcode only
                $display("FAIL %-7s a=%h b=%h : got y=%h f=%b, expected y=%h f=%b",
                         names[o], a, b, y, flag, exp_y, exp_f);
        end
    endtask

    logic [3:0] alu_ops [10];
    logic [3:0] other_ops [6];

    initial begin
        string vcd_file;
        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_lane_alu.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_lane_alu);

        names = '{"NOP","VADD","VSUB","VAND","VOR","VXOR","VCMPEQ","VCMPLT",
                  "VSLL","VSRL","VMUL","SLI","VLD","VST","VBCAST","HALT"};
        foreach (checks[i]) begin checks[i] = 0; fails[i] = 0; end
        alu_ops   = '{`OP_VADD, `OP_VSUB, `OP_VAND, `OP_VOR, `OP_VXOR,
                      `OP_VCMPEQ, `OP_VCMPLT, `OP_VSLL, `OP_VSRL, `OP_VMUL};
        other_ops = '{`OP_NOP, `OP_SLI, `OP_VLD, `OP_VST, `OP_VBCAST, `OP_HALT};

        // ---- Phase A: directed edge cases --------------------------------------
        foreach (alu_ops[k]) begin
            apply_and_check(alu_ops[k], MOD - 1, 1);         // max + 1  -> wrap to 0
            apply_and_check(alu_ops[k], 0, 1);               // 0 - 1    -> all ones
            apply_and_check(alu_ops[k], HALF - 1, 1);        // +max + 1 -> signed overflow
            apply_and_check(alu_ops[k], HALF, 1);            // -min vs +1 (signed LT true)
            apply_and_check(alu_ops[k], 1, HALF);            // +1 vs -min (signed LT false)
            apply_and_check(alu_ops[k], HALF, HALF - 1);     // most negative vs most positive
            apply_and_check(alu_ops[k], 8'h5A, 0);           // shift by 0
            apply_and_check(alu_ops[k], MOD - 1, WIDTH - 1); // shift by max
            apply_and_check(alu_ops[k], 8'h3C, 8'h3C);       // equal operands
        end

        // ---- Phase B: exhaustive (WIDTH <= 8) or random -------------------------
        $dumpoff;   // waveform keeps only the readable directed cases above
        if (WIDTH <= 8) begin
            foreach (alu_ops[k])
                for (longint ua = 0; ua < MOD; ua++)
                    for (longint ub = 0; ub < MOD; ub++)
                        apply_and_check(alu_ops[k], ua, ub);
        end else begin
            foreach (alu_ops[k])
                repeat (N_RANDOM)
                    apply_and_check(alu_ops[k],
                                    {$urandom, $urandom} % MOD,
                                    {$urandom, $urandom} % MOD);
        end

        // ---- Phase C: non-ALU opcodes must be inert ----------------------------
        foreach (other_ops[k])
            repeat (200)
                apply_and_check(other_ops[k], $urandom % MOD, $urandom % MOD);

        // ---- Report --------------------------------------------------------------
        $display("");
        $display("  opcode   checks     fails   (WIDTH=%0d, %s)", WIDTH,
                 WIDTH <= 8 ? "exhaustive" : "random");
        $display("  -------  ---------  ------");
        for (int i = 0; i < 16; i++) begin
            total_checks += checks[i];
            if (checks[i] == 0) begin
                $display("  coverage hole: opcode %s never exercised", names[i]);
                errors++;
            end
            if (checks[i] > 0)
                $display("  %-7s  %9d  %6d  %s", names[i], checks[i], fails[i],
                         fails[i] == 0 ? "ok" : "<-- FAIL");
        end
        $display("  total checks: %0d", total_checks);
        $display("");
        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
