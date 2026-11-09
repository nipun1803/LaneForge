// -----------------------------------------------------------------------------
// tb_defs.v - consistency checks for simd_defs.vh (Step 1)
//   1. all 16 opcodes are distinct and fit in 4 bits
//   2. instruction fields tile the 16-bit word without overlap
//   3. encode macros place each field where the decode slices read it
//   4. memory/address sizes agree with their widths
// -----------------------------------------------------------------------------
`timescale 1ns/1ps
`include "simd_defs.vh"

module tb_defs #(
    parameter LANES = 4,
    parameter WIDTH = 8
);
    integer errors = 0;

    task automatic check(input bit cond, input string msg);
        if (!cond) begin
            $display("FAIL: %s", msg);
            errors++;
        end
    endtask

    // ---- 1. opcode uniqueness ------------------------------------------------
    logic [3:0] ops [16];
    initial begin
        ops = '{`OP_NOP, `OP_VADD, `OP_VSUB, `OP_VAND, `OP_VOR, `OP_VXOR,
                `OP_VCMPEQ, `OP_VCMPLT, `OP_VSLL, `OP_VSRL, `OP_VMUL,
                `OP_SLI, `OP_VLD, `OP_VST, `OP_VBCAST, `OP_HALT};
    end

    logic [`INSTR_W-1:0] instr, used;

    initial begin
        #1;
        for (int i = 0; i < 16; i++)
            for (int j = i + 1; j < 16; j++)
                check(ops[i] !== ops[j], $sformatf("opcode %0d == opcode %0d", i, j));

        // ---- 2. field tiling: set each field to all-1s, OR into 'used' -------
        // R-type must cover op|vd|vs1|vs2|s exactly once (bits [1:0] reserved)
        used = '0;
        instr = '0; instr[`F_OP]  = '1; check((used & instr) == 0, "OP overlaps");  used |= instr;
        instr = '0; instr[`F_VD]  = '1; check((used & instr) == 0, "VD overlaps");  used |= instr;
        instr = '0; instr[`F_VS1] = '1; check((used & instr) == 0, "VS1 overlaps"); used |= instr;
        instr = '0; instr[`F_VS2] = '1; check((used & instr) == 0, "VS2 overlaps"); used |= instr;
        instr = '0; instr[`F_S]   = '1; check((used & instr) == 0, "S overlaps");   used |= instr;
        check(used == 16'hFFFC, $sformatf("R-type coverage = %h, expected FFFC", used));

        // M-type: op|vreg|addr must cover all 16 bits
        used = '0;
        instr = '0; instr[`F_OP]   = '1; used |= instr;
        instr = '0; instr[`F_VREG] = '1; check((used & instr) == 0, "VREG overlaps"); used |= instr;
        instr = '0; instr[`F_ADDR] = '1; check((used & instr) == 0, "ADDR overlaps"); used |= instr;
        check(used == 16'hFFFF, $sformatf("M-type coverage = %h, expected FFFF", used));

        // ---- 3. encode -> decode round trip -----------------------------------
        instr = `ENC_R(`OP_VSUB, 5, 3, 6, 1);       // VSUB v5, v3, v6 (scalar)
        check(instr[`F_OP]  == `OP_VSUB, "ENC_R op");
        check(instr[`F_VD]  == 5,        "ENC_R vd");
        check(instr[`F_VS1] == 3,        "ENC_R vs1");
        check(instr[`F_VS2] == 6,        "ENC_R vs2");
        check(instr[`F_S]   == 1,        "ENC_R s");

        instr = `ENC_M(`OP_VLD, 7, 9'd300);         // VLD v7, [300]
        check(instr[`F_OP]   == `OP_VLD, "ENC_M op");
        check(instr[`F_VREG] == 7,       "ENC_M vreg");
        check(instr[`F_ADDR] == 300,     "ENC_M addr");

        instr = `ENC_S(`OP_SLI, 8'hA5);             // SLI 0xA5
        check(instr[`F_OP]   == `OP_SLI, "ENC_S op");
        check(instr[`F_IMM8] == 8'hA5,   "ENC_S imm");

        // ---- 4. write-back select codes distinct and fit WB_SEL_W --------------
        check(`WB_ALU != `WB_MEM && `WB_ALU != `WB_BCAST && `WB_MEM != `WB_BCAST,
              "WB_* select codes must be distinct");
        check(`WB_BCAST < (1 << `WB_SEL_W), "WB_* codes must fit in WB_SEL_W bits");

        // ---- 5. size / width agreement ----------------------------------------
        check((1 << `VREG_AW) == `NUM_VREGS,  "VREG_AW vs NUM_VREGS");
        check((1 << `IMEM_AW) == `IMEM_DEPTH, "IMEM_AW vs IMEM_DEPTH");
        check((1 << `DMEM_AW) == `DMEM_DEPTH, "DMEM_AW vs DMEM_DEPTH");
        check(WIDTH >= 2 && (WIDTH & (WIDTH - 1)) == 0,
              "WIDTH must be a power of two (shift amount uses log2)");

        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
