// -----------------------------------------------------------------------------
// tb_vector_control.v - verification of the instruction decoder (Step 7)
//
//   EXHAUSTIVE: all 2^16 = 65,536 instruction words are decoded and compared
//   with an independent reference table. For each word it checks:
//     0. no X/Z on any output
//     1. enables       : rf_we, mem_we, s_we, mask_we, halt, wb_sel
//     2. ALU control   : alu_op (NOP for non-ALU), sel_scalar (= s bit for ALU)
//     3. addresses     : raddr1/raddr2/waddr/mem_addr/s_imm where they matter
//     4. invariant     : at most one data destination (rf_we, mem_we, s_we)
//                        compares additionally update mask status via mask_we
//
//   The reference is a per-opcode table (data), not a case statement (code),
//   so it is written differently from the RTL.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_vector_control #(
    parameter LANES = 4,      // unused; accepted so the Makefile -P works
    parameter WIDTH = 8
);
    // ---- DUT ------------------------------------------------------------------
    reg  [`INSTR_W-1:0]  instr;
    wire [3:0]           alu_op;
    wire                 sel_scalar, rf_we, s_we, mem_we, mask_we, halt;
    wire [`VREG_AW-1:0]  raddr1, raddr2, waddr;
    wire [`WB_SEL_W-1:0] wb_sel;
    wire [7:0]           s_imm;
    wire [`DMEM_AW-1:0]  mem_addr;

    vector_control dut (
        .instr(instr), .alu_op(alu_op), .sel_scalar(sel_scalar),
        .raddr1(raddr1), .raddr2(raddr2), .waddr(waddr),
        .rf_we(rf_we), .wb_sel(wb_sel), .s_we(s_we), .s_imm(s_imm),
        .mem_we(mem_we), .mem_addr(mem_addr), .mask_we(mask_we), .halt(halt)
    );

    // ---- Reference decode table, indexed by opcode ---------------------------
    bit       t_alu  [16];
    bit       t_rfwe [16];
    bit [1:0] t_wb   [16];
    bit       t_memw [16];
    bit       t_swe  [16];
    bit       t_mask [16];
    bit       t_halt [16];
    string    names  [16];

    task automatic row(input int o, input bit alu, rfwe, input bit [1:0] wb,
                       input bit memw, swe, mask, hlt);
        t_alu[o] = alu; t_rfwe[o] = rfwe; t_wb[o] = wb;
        t_memw[o] = memw; t_swe[o] = swe; t_mask[o] = mask; t_halt[o] = hlt;
    endtask

    // ---- Bookkeeping ----------------------------------------------------------
    integer errors = 0, printed = 0, total_checks = 0;
    integer op_checks [16];
    integer op_fails  [16];

    task automatic fail(input string msg);
        errors++;
        op_fails[instr[`F_OP]]++;
        if (printed++ < 15)
            $display("FAIL instr=%h (%s): %s", instr, names[instr[`F_OP]], msg);
    endtask

    initial begin
        string vcd_file;
        int o, writes;
        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_vector_control.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_vector_control);

        names = '{"NOP","VADD","VSUB","VAND","VOR","VXOR","VCMPEQ","VCMPLT",
                  "VSLL","VSRL","VMUL","SLI","VLD","VST","VBCAST","HALT"};
        foreach (op_checks[i]) begin op_checks[i] = 0; op_fails[i] = 0; end

        //        opcode      alu rfwe  wb_sel     memw swe mask halt
        row(`OP_NOP,      0,  0,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VADD,     1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VSUB,     1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VAND,     1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VOR,      1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VXOR,     1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VCMPEQ,   1,  1,  `WB_ALU,   0,   0,  1,   0);
        row(`OP_VCMPLT,   1,  1,  `WB_ALU,   0,   0,  1,   0);
        row(`OP_VSLL,     1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VSRL,     1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_VMUL,     1,  1,  `WB_ALU,   0,   0,  0,   0);
        row(`OP_SLI,      0,  0,  `WB_ALU,   0,   1,  0,   0);
        row(`OP_VLD,      0,  1,  `WB_MEM,   0,   0,  0,   0);
        row(`OP_VST,      0,  0,  `WB_ALU,   1,   0,  0,   0);
        row(`OP_VBCAST,   0,  1,  `WB_BCAST, 0,   0,  0,   0);
        row(`OP_HALT,     0,  0,  `WB_ALU,   0,   0,  0,   1);

        // Waveform preview: one readable instruction per opcode (not checked here;
        // every one of these words is checked again in the exhaustive loop)
        for (int k = 0; k < 16; k++) begin
            instr = {k[3:0], 12'h5A5};
            #1;
        end
        $dumpoff;

        // Exhaustive: every 16-bit word exactly once
        for (int w = 0; w < 65536; w++) begin
            instr = w[15:0];
            #1;
            o = instr[`F_OP];
            op_checks[o]++;

            // 0. No X/Z anywhere
            // (XOR-reduce is X iff any bit is X/Z; Icarus' $isunknown is unreliable)
            if ((^{alu_op, sel_scalar, raddr1, raddr2, waddr, rf_we, wb_sel,
                   s_we, s_imm, mem_we, mem_addr, mask_we, halt}) === 1'bx)
                fail("X/Z on an output");

            // 1. Enables
            if (rf_we   !== t_rfwe[o]) fail($sformatf("rf_we=%b expected %b", rf_we, t_rfwe[o]));
            if (mem_we  !== t_memw[o]) fail($sformatf("mem_we=%b expected %b", mem_we, t_memw[o]));
            if (s_we    !== t_swe[o])  fail($sformatf("s_we=%b expected %b", s_we, t_swe[o]));
            if (mask_we !== t_mask[o]) fail($sformatf("mask_we=%b expected %b", mask_we, t_mask[o]));
            if (halt    !== t_halt[o]) fail($sformatf("halt=%b expected %b", halt, t_halt[o]));
            if (rf_we && wb_sel !== t_wb[o])
                fail($sformatf("wb_sel=%0d expected %0d", wb_sel, t_wb[o]));

            // 2. ALU control
            if (alu_op !== (t_alu[o] ? o[3:0] : `OP_NOP))
                fail($sformatf("alu_op=%h expected %h", alu_op, t_alu[o] ? o : `OP_NOP));
            if (sel_scalar !== (t_alu[o] ? instr[`F_S] : 1'b0))
                fail($sformatf("sel_scalar=%b", sel_scalar));

            // 3. Addresses, where they are used
            if (t_alu[o] && raddr1 !== instr[`F_VS1]) fail("raddr1 != vs1");
            if (t_alu[o] && raddr2 !== instr[`F_VS2]) fail("raddr2 != vs2");
            if (o == `OP_VST && raddr2 !== instr[`F_VREG]) fail("VST raddr2 != vreg");
            if (rf_we && waddr !== instr[`F_VD]) fail("waddr != vd/vreg");
            if ((o == `OP_VLD || o == `OP_VST) && mem_addr !== instr[`F_ADDR]) fail("mem_addr != addr");
            if (o == `OP_SLI && s_imm !== instr[`F_IMM8]) fail("s_imm != imm8");

            // 4. At most one data destination; mask_we is checked separately above
            writes = rf_we + mem_we + s_we;
            if (writes > 1) fail($sformatf("%0d simultaneous writes", writes));
        end

        // ---- Report --------------------------------------------------------------
        $display("");
        $display("  vector_control  (exhaustive: all 65536 instruction words)");
        $display("  opcode   decoded  fails");
        $display("  -------  -------  -----");
        for (int i = 0; i < 16; i++) begin
            total_checks += op_checks[i];
            $display("  %-7s  %7d  %5d  %s", names[i], op_checks[i], op_fails[i],
                     op_fails[i] == 0 ? "ok" : "<-- FAIL");
            if (op_checks[i] != 4096) begin
                $display("  coverage hole: opcode %s decoded %0d times, expected 4096", names[i], op_checks[i]);
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
