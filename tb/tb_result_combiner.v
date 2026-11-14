// -----------------------------------------------------------------------------
// tb_result_combiner.v - verification of the result combiner (Step 6)
//
//   A. Select       : each wb_sel picks exactly its source; the three sources
//                     always hold DIFFERENT random data so a wrong pick shows
//   B. Reserved sel : wb_sel = 3 writes zero (never X)
//   C. Mask, every  : all 2^LANES mask patterns -> mask_out / any / all / count
//      pattern        checked against a reference loop
//   D. Mask isolation: mask outputs depend only on alu_mask, never on wb_sel
//                     or data inputs
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_result_combiner #(
    parameter LANES    = 4,
    parameter WIDTH    = 8,
    parameter N_RANDOM = 5000
);
    localparam VW = LANES * WIDTH;
    localparam CW = $clog2(LANES + 1);
    localparam S_SEL = 0, S_RSVD = 1, S_MASK = 2, S_ISO = 3, NSEC = 4;

    // ---- DUT ------------------------------------------------------------------
    reg  [`WB_SEL_W-1:0] wb_sel;
    reg  [VW-1:0]        alu_y, mem_rdata, bcast;
    reg  [LANES-1:0]     alu_mask;
    wire [VW-1:0]        wb_data;
    wire [LANES-1:0]     mask_out;
    wire                 any_true, all_true;
    wire [CW-1:0]        mask_count;

    result_combiner #(.LANES(LANES), .WIDTH(WIDTH)) dut (
        .wb_sel(wb_sel), .alu_y(alu_y), .alu_mask(alu_mask),
        .mem_rdata(mem_rdata), .bcast(bcast),
        .wb_data(wb_data), .mask_out(mask_out),
        .any_true(any_true), .all_true(all_true), .mask_count(mask_count)
    );

    // ---- Checking -------------------------------------------------------------
    integer errors = 0, printed = 0, total_checks = 0;
    integer sec_checks [NSEC];
    string  sec_names  [NSEC];
    integer sel_hits   [4];          // coverage: each wb_sel value exercised

    function automatic logic [VW-1:0] rand_vec();
        logic [VW-1:0] x;
        for (int i = 0; i < LANES; i++) x[i*WIDTH +: WIDTH] = $urandom;
        return x;
    endfunction

    // Three sources guaranteed pairwise different
    task automatic distinct_sources();
        alu_y = rand_vec();
        do mem_rdata = rand_vec(); while (mem_rdata == alu_y);
        do bcast     = rand_vec(); while (bcast == alu_y || bcast == mem_rdata);
    endtask

    task automatic fail(input int sec, input string msg);
        errors++;
        if (printed++ < 12) $display("FAIL [%s] %s", sec_names[sec], msg);
    endtask

    // Check the write-back mux for the current inputs
    task automatic check_wb(input int sec);
        logic [VW-1:0] exp;
        #1;
        case (wb_sel)
            `WB_ALU:   exp = alu_y;
            `WB_MEM:   exp = mem_rdata;
            `WB_BCAST: exp = bcast;
            default:   exp = '0;
        endcase
        sec_checks[sec]++;
        sel_hits[wb_sel]++;
        if (wb_data !== exp)
            fail(sec, $sformatf("wb_sel=%0d : wb_data=%h expected %h (alu=%h mem=%h bc=%h)",
                                wb_sel, wb_data, exp, alu_y, mem_rdata, bcast));
    endtask

    // Check mask outputs against an independent reference
    task automatic check_mask(input int sec);
        int  cnt;
        bit  any, all;
        #1;
        cnt = 0; any = 0; all = 1;
        for (int i = 0; i < LANES; i++) begin
            cnt += alu_mask[i];
            any |= alu_mask[i];
            all &= alu_mask[i];
        end
        sec_checks[sec]++;
        if (mask_out !== alu_mask || any_true !== any || all_true !== all || mask_count !== CW'(cnt))
            fail(sec, $sformatf("mask=%b : out=%b any=%b all=%b count=%0d, expected any=%b all=%b count=%0d",
                                alu_mask, mask_out, any_true, all_true, mask_count, any, all, cnt));
    endtask

    initial begin
        string vcd_file;
        int n_patterns;
        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_result_combiner.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_result_combiner);

        sec_names = '{"A select", "B reserved", "C mask", "D isolation"};
        foreach (sec_checks[i]) sec_checks[i] = 0;
        foreach (sel_hits[i])   sel_hits[i]   = 0;
        alu_mask = '0;

        // A. Each select picks its own source -----------------------------------
        repeat (N_RANDOM) begin
            distinct_sources();
            wb_sel = `WB_ALU;   check_wb(S_SEL);
            wb_sel = `WB_MEM;   check_wb(S_SEL);
            wb_sel = `WB_BCAST; check_wb(S_SEL);
        end

        // B. Reserved code writes zero ---------------------------------------------
        repeat (200) begin
            distinct_sources();
            wb_sel = 2'd3; check_wb(S_RSVD);
        end

        // C. Every mask pattern -----------------------------------------------------
        n_patterns = (LANES <= 8) ? (1 << LANES) : 256;
        for (int p = 0; p < n_patterns; p++) begin
            alu_mask = p;
            check_mask(S_MASK);
        end

        // D. Mask outputs unaffected by select and data ---------------------------
        repeat (2000) begin
            alu_mask = $urandom;
            distinct_sources();
            wb_sel = $urandom % 4;
            check_mask(S_ISO);
            check_wb(S_ISO);
        end

        // ---- Report --------------------------------------------------------------
        $display("");
        $display("  result_combiner  LANES=%0d  WIDTH=%0d", LANES, WIDTH);
        for (int i = 0; i < NSEC; i++) begin
            $display("  %-12s %6d checks", sec_names[i], sec_checks[i]);
            total_checks += sec_checks[i];
            if (sec_checks[i] == 0) begin
                $display("  coverage hole: section %s never exercised", sec_names[i]);
                errors++;
            end
        end
        $display("  wb_sel coverage: ALU=%0d MEM=%0d BCAST=%0d RSVD=%0d",
                 sel_hits[0], sel_hits[1], sel_hits[2], sel_hits[3]);
        foreach (sel_hits[i]) if (sel_hits[i] == 0) begin
            $display("  coverage hole: wb_sel=%0d never exercised", i);
            errors++;
        end
        $display("");
        $display("  total checks: %0d", total_checks);
        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
