// -----------------------------------------------------------------------------
// tb_smoke.v - toolchain sanity check (Step 0)
// Verifies: compile flags, parameter override from Makefile, VCD dumping,
// and the PASS/FAIL reporting convention every testbench will follow.
// -----------------------------------------------------------------------------
`timescale 1ns/1ps

module tb_smoke #(
    parameter LANES = 4,
    parameter WIDTH = 8
);
    reg clk = 0;
    always #5 clk = ~clk;              // 100 MHz simulation clock

    reg [LANES*WIDTH-1:0] vec;
    integer errors = 0;
    string  vcd_file;

    initial begin
        // VCD path comes from the Makefile: +vcd=sim/tb_smoke.vcd
        if (!$value$plusargs("vcd=%s", vcd_file)) vcd_file = "sim/tb_smoke.vcd";
        $dumpfile(vcd_file);
        $dumpvars(0, tb_smoke);

        // Fill each lane with its own index, then check the slicing
        for (int i = 0; i < LANES; i++)
            vec[i*WIDTH +: WIDTH] = i;
        @(posedge clk);

        for (int i = 0; i < LANES; i++)
            if (vec[i*WIDTH +: WIDTH] !== i) begin
                $display("FAIL: lane %0d = %0d", i, vec[i*WIDTH +: WIDTH]);
                errors++;
            end

        $display("LANES=%0d WIDTH=%0d vector=%h", LANES, WIDTH, vec);
        if (errors == 0) $display("ALL PASS");
        else             $display("FAILED with %0d errors", errors);
        $finish;
    end
endmodule
