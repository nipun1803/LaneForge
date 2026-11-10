// -----------------------------------------------------------------------------
// tb_simd_top.v - integration test of the complete SIMD unit (Step 8)
//
//  Each test loads a program into IMEM and data into DMEM, resets the DUT,
//  runs until HALT, and compares the ENTIRE architectural state with an
//  instruction-set reference model (iss_run below) that executes the same
//  program:
//      all DMEM words, v0..v7, scalar, mask status, PC, halted,
//      cycle_count, instr_count, lane_ops
//
//  Programs
//    P1 basic       VLD, VLD, VADD, VST, HALT (the Step 8 "done when" program)
//    P2 all ops     every ALU op, vector-vector AND vector-scalar forms
//    P3 scalar      SLI / VBCAST, imm8 zero-extension
//    P4 hazards     back-to-back dependent writes (VADD v1,v1,v1 chain)
//    P5 mask        mask status follows compares only
//    P6 no HALT     IMEM default (HALT) stops a program that forgot its HALT
//    P7 freeze      after HALT, 50 more cycles change nothing
//    P8 reset       reset in the middle of a run, then a clean re-run
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_simd_top #(
    parameter LANES = 4,
    parameter WIDTH = 8
);
    localparam longint MOD  = longint'(1) << WIDTH;
    localparam longint HALF = MOD >> 1;
    localparam VW   = LANES * WIDTH;
    localparam NI   = `IMEM_DEPTH;
    localparam ND   = `DMEM_DEPTH;
    localparam MAXC = 2000;                       // run timeout (cycles)

    // ---- DUT ------------------------------------------------------------------
    reg                   clk = 0, rst = 1;
    reg  [`DMEM_AW-1:0]   dbg_addr = 0;
    wire [VW-1:0]         dbg_rdata;
    wire                  halted;
    wire [`IMEM_AW-1:0]   pc_out;
    wire [31:0]           cycle_count, instr_count, lane_ops;
    wire [LANES-1:0]      mask_status;

    simd_top #(.LANES(LANES), .WIDTH(WIDTH)) dut (
        .clk(clk), .rst(rst),
        .halted(halted), .pc_out(pc_out),
        .cycle_count(cycle_count), .instr_count(instr_count), .lane_ops(lane_ops),
        .mask_status(mask_status),
        .dbg_addr(dbg_addr), .dbg_rdata(dbg_rdata)
    );

    always #5 clk = ~clk;

    `include "ref_alu.vh"

    // ---- Program / data images ------------------------------------------------
    logic [15:0]   prog  [NI];
    logic [VW-1:0] dinit [ND];
    int            plen;

    task automatic prog_clear();
        foreach (prog[i]) prog[i] = {`OP_HALT, 12'h000};   // same default as the DUT
        plen = 0;
    endtask
    task automatic emit(input logic [15:0] w);
        prog[plen++] = w;
    endtask

    // ---- Instruction-set reference model --------------------------------------
    logic [VW-1:0]    m_v    [8];
    logic [WIDTH-1:0] m_s;
    logic [VW-1:0]    m_d    [ND];
    logic [LANES-1:0] m_mask;
    int               m_pc, m_cycles, m_laneops;

    task automatic iss_run(input int max_steps);
        logic [15:0] w;
        logic [3:0]  o;
        longint      r, bl;
        foreach (m_v[i]) m_v[i] = '0;
        m_s = '0; m_mask = '0;
        foreach (m_d[i]) m_d[i] = dinit[i];
        m_pc = 0; m_cycles = 0; m_laneops = 0;
        for (int step = 0; step < max_steps; step++) begin
            w = prog[m_pc];
            o = w[15:12];
            m_cycles++;
            if (o == `OP_HALT) return;
            if (o >= `OP_VADD && o <= `OP_VMUL) begin
                logic [VW-1:0] res;
                for (int i = 0; i < LANES; i++) begin
                    bl = w[2] ? longint'(m_s) : longint'(m_v[w[5:3]][i*WIDTH +: WIDTH]);
                    r  = ref_alu(o, longint'(m_v[w[8:6]][i*WIDTH +: WIDTH]), bl);
                    res[i*WIDTH +: WIDTH] = r[WIDTH-1:0];
                    if (o == `OP_VCMPEQ || o == `OP_VCMPLT) m_mask[i] = r[WIDTH];
                end
                m_v[w[11:9]] = res;
                m_laneops += LANES;
            end else case (o)
                `OP_SLI:    m_s = longint'(w[7:0]) % MOD;
                `OP_VLD:    m_v[w[11:9]] = m_d[w[8:0]];
                `OP_VST:    m_d[w[8:0]] = m_v[w[11:9]];
                `OP_VBCAST: for (int i = 0; i < LANES; i++) m_v[w[11:9]][i*WIDTH +: WIDTH] = m_s;
                default:    ;   // NOP
            endcase
            m_pc = (m_pc + 1) % NI;
        end
    endtask

    // ---- DUT control ----------------------------------------------------------
    task automatic load_dut();
        for (int i = 0; i < NI; i++) dut.imem[i] = prog[i];
        for (int i = 0; i < ND; i++) dut.dmem[i] = dinit[i];
    endtask

    task automatic reset_dut();
        @(negedge clk); rst = 1;
        @(negedge clk); rst = 0;
    endtask

    // Run until halted; returns the number of clock edges waited
    task automatic run_dut(output int edges);
        edges = 0;
        while (!halted && edges < MAXC) begin
            @(posedge clk); #1;
            edges++;
        end
    endtask

    // ---- Comparison -----------------------------------------------------------
    integer errors = 0, printed = 0, total_checks = 0;

    task automatic fail(input string name, input string msg);
        errors++;
        if (printed++ < 15) $display("FAIL [%s] %s", name, msg);
    endtask

    task automatic compare_state(input string name);
        int n0 = errors;
        // data memory, through the debug port
        for (int a = 0; a < ND; a++) begin
            dbg_addr = a; #0.1;
            total_checks++;
            if (dbg_rdata !== m_d[a])
                fail(name, $sformatf("DMEM[%0d] = %h, expected %h", a, dbg_rdata, m_d[a]));
        end
        // registers
        for (int r = 0; r < 8; r++) begin
            total_checks++;
            if (dut.u_rf.vregs[r] !== m_v[r])
                fail(name, $sformatf("v%0d = %h, expected %h", r, dut.u_rf.vregs[r], m_v[r]));
        end
        total_checks += 7;
        if (dut.u_rf.sreg  !== m_s)        fail(name, $sformatf("scalar = %h, expected %h", dut.u_rf.sreg, m_s));
        if (mask_status    !== m_mask)     fail(name, $sformatf("mask = %b, expected %b", mask_status, m_mask));
        if (halted         !== 1'b1)       fail(name, "did not halt");
        if (pc_out         !== m_pc[7:0])  fail(name, $sformatf("pc = %0d, expected %0d", pc_out, m_pc));
        if (cycle_count    !== m_cycles)   fail(name, $sformatf("cycles = %0d, expected %0d", cycle_count, m_cycles));
        if (instr_count    !== m_cycles)   fail(name, $sformatf("instr = %0d, expected %0d", instr_count, m_cycles));
        if (lane_ops       !== m_laneops)  fail(name, $sformatf("lane_ops = %0d, expected %0d", lane_ops, m_laneops));
        $display("  %-10s %4d instr  %4d cycles  %5d lane-ops  %s",
                 name, plen, cycle_count, lane_ops, errors == n0 ? "ok" : "<-- FAIL");
    endtask

    // Full flow for one program
    task automatic run_test(input string name);
        int edges;
        iss_run(MAXC);
        load_dut();
        reset_dut();
        run_dut(edges);
        compare_state(name);
    endtask

    // ---- Data helpers ---------------------------------------------------------
    function automatic logic [VW-1:0] rand_vec();
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = {$urandom, $urandom} % MOD;
        return x;
    endfunction

    task automatic data_init();
        logic [VW-1:0] edge_a, edge_b;
        foreach (dinit[i]) dinit[i] = '0;
        for (int i = 0; i < 16; i++) dinit[i] = rand_vec();
        // dinit[0]/[1]: overflow and signed boundaries in different lanes
        for (int i = 0; i < LANES; i++) begin
            case (i % 4)
                0: begin edge_a[i*WIDTH +: WIDTH] = MOD - 1;  edge_b[i*WIDTH +: WIDTH] = 1;        end
                1: begin edge_a[i*WIDTH +: WIDTH] = HALF;     edge_b[i*WIDTH +: WIDTH] = 1;        end
                2: begin edge_a[i*WIDTH +: WIDTH] = HALF - 1; edge_b[i*WIDTH +: WIDTH] = HALF;     end
                3: begin edge_a[i*WIDTH +: WIDTH] = 5;        edge_b[i*WIDTH +: WIDTH] = 5;        end
            endcase
        end
        dinit[0] = edge_a;
        dinit[1] = edge_b;
    endtask

    // ---- Programs ---------------------------------------------------------------
    task automatic build_all_ops();
        prog_clear();
        emit(`ENC_M(`OP_VLD, 1, 0));
        emit(`ENC_M(`OP_VLD, 2, 1));
        emit(`ENC_S(`OP_SLI, 8'h03));
        for (int o = `OP_VADD; o <= `OP_VMUL; o++) begin
            emit(`ENC_R(o[3:0], 3, 1, 2, 0));          // vector-vector
            emit(`ENC_M(`OP_VST, 3, 16 + o));
            emit(`ENC_R(o[3:0], 4, 1, 0, 1));          // vector-scalar
            emit(`ENC_M(`OP_VST, 4, 32 + o));
        end
        emit({`OP_HALT, 12'h000});
    endtask

    initial begin
        string vcd_file;
        int edges;
        logic [31:0] c0, i0, l0;
        logic [VW-1:0] d0 [ND];

        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_simd_top.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_simd_top);

        $display("");
        $display("  simd_top  LANES=%0d  WIDTH=%0d", LANES, WIDTH);
        $display("  program    instrs   cycles   lane-ops");
        data_init();

        // P1: basic -----------------------------------------------------------------
        prog_clear();
        emit(`ENC_M(`OP_VLD, 1, 0));
        emit(`ENC_M(`OP_VLD, 2, 1));
        emit(`ENC_R(`OP_VADD, 3, 1, 2, 0));
        emit(`ENC_M(`OP_VST, 3, 2));
        emit({`OP_HALT, 12'h000});
        run_test("P1 basic");
        $dumpoff;

        // P2: every ALU op, vector-vector and vector-scalar --------------------------
        build_all_ops();
        run_test("P2 all-ops");

        // P3: scalar path ---------------------------------------------------------------
        prog_clear();
        emit(`ENC_M(`OP_VLD, 1, 5));
        emit(`ENC_S(`OP_SLI, 8'h85));                 // top bit set: zero-extended
        emit(`ENC_R(`OP_VBCAST, 4, 0, 0, 0));
        emit(`ENC_M(`OP_VST, 4, 40));
        emit(`ENC_R(`OP_VADD, 5, 1, 0, 1));           // v5 = v1 + scalar
        emit(`ENC_M(`OP_VST, 5, 41));
        emit(`ENC_S(`OP_SLI, 8'hFF));
        emit(`ENC_R(`OP_VXOR, 6, 1, 0, 1));           // v6 = ~v1 (8-bit lanes)
        emit(`ENC_M(`OP_VST, 6, 42));
        emit({`OP_HALT, 12'h000});
        run_test("P3 scalar");

        // P4: back-to-back dependencies ---------------------------------------------
        prog_clear();
        emit(`ENC_M(`OP_VLD, 1, 6));
        repeat (6) emit(`ENC_R(`OP_VADD, 1, 1, 1, 0));   // v1 = v1 * 2, six times
        emit(`ENC_M(`OP_VST, 1, 50));
        emit(`ENC_R(`OP_VSUB, 2, 1, 1, 0));             // v2 = v1 - v1 = 0
        emit(`ENC_R(`OP_VOR,  3, 2, 1, 0));             // v3 = v1 (reads the just-written v2)
        emit(`ENC_M(`OP_VST, 3, 51));
        emit(`ENC_M(`OP_VLD, 7, 51));                   // load what was just stored
        emit(`ENC_M(`OP_VST, 7, 52));
        emit({`OP_HALT, 12'h000});
        run_test("P4 hazards");

        // P5: mask status ---------------------------------------------------------------
        prog_clear();
        emit(`ENC_M(`OP_VLD, 1, 0));
        emit(`ENC_M(`OP_VLD, 2, 1));
        emit(`ENC_R(`OP_VCMPEQ, 3, 1, 2, 0));           // mask <- lanes equal
        emit(`ENC_R(`OP_VADD,   4, 1, 2, 0));           // non-compare: mask unchanged
        emit(`ENC_M(`OP_VST, 3, 60));
        emit(`ENC_R(`OP_VCMPLT, 5, 1, 2, 0));           // mask <- signed less-than
        emit(`ENC_R(`OP_VAND,   6, 5, 3, 0));
        emit(`ENC_M(`OP_VST, 5, 61));
        emit({`OP_HALT, 12'h000});
        run_test("P5 mask");

        // P6: program without HALT stops at the IMEM default ---------------------------
        prog_clear();
        emit(`ENC_M(`OP_VLD, 1, 7));
        emit(`ENC_R(`OP_VSUB, 2, 1, 1, 0));
        emit(`ENC_M(`OP_VST, 1, 70));
        run_test("P6 no-halt");

        // P7: frozen after HALT ------------------------------------------------------
        build_all_ops();
        run_test("P7 freeze");
        c0 = cycle_count; i0 = instr_count; l0 = lane_ops;
        for (int a = 0; a < ND; a++) d0[a] = dut.dmem[a];
        repeat (50) @(posedge clk);
        #1;
        total_checks += 4;
        if (cycle_count !== c0 || instr_count !== i0 || lane_ops !== l0)
            fail("P7 freeze", "counters moved after HALT");
        if (pc_out !== m_pc[7:0]) fail("P7 freeze", "PC moved after HALT");
        for (int a = 0; a < ND; a++)
            if (dut.dmem[a] !== d0[a]) fail("P7 freeze", $sformatf("DMEM[%0d] changed after HALT", a));
        compare_state("P7 +50cyc");

        // P8: reset in the middle of a run -------------------------------------------
        // P2 only loads from 0/1 and stores to 16+, so a partial run followed by a
        // full re-run must end in exactly the same state as one clean run.
        build_all_ops();
        iss_run(MAXC);
        load_dut();
        reset_dut();
        repeat (17) @(posedge clk);
        #1;
        @(negedge clk); rst = 1;
        @(posedge clk); #1;
        total_checks += 5;
        if (pc_out !== 0 || halted !== 0 || cycle_count !== 0 || lane_ops !== 0)
            fail("P8 reset", $sformatf("after reset pc=%0d halted=%b cycles=%0d", pc_out, halted, cycle_count));
        for (int r = 0; r < 8; r++)
            if (dut.u_rf.vregs[r] !== '0) fail("P8 reset", $sformatf("v%0d not cleared", r));
        @(negedge clk); rst = 0;
        run_dut(edges);
        compare_state("P8 reset");

        // ---- Report --------------------------------------------------------------
        $display("  total checks: %0d", total_checks);
        $display("");
        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
