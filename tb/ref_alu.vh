// -----------------------------------------------------------------------------
// ref_alu.vh - golden reference model for ONE lane (shared by testbenches)
// -----------------------------------------------------------------------------
//  `include this INSIDE a testbench module that defines:
//      parameter WIDTH (supported project widths: 2, 4, 8, 16)
//      localparam longint MOD  = longint'(1) << WIDTH
//      localparam longint HALF = MOD >> 1
//
//  Written with integer arithmetic (mod 2^WIDTH, explicit two's-complement
//  conversion) instead of Verilog truncation / $signed, so it does not share
//  the RTL's failure modes.
// -----------------------------------------------------------------------------

function automatic longint ref_to_signed(input longint u);
    return (u >= HALF) ? u - MOD : u;
endfunction

// Returns {flag, y}: flag in bit WIDTH, y in bits [WIDTH-1:0]
function automatic longint ref_alu(input logic [3:0] o, input longint ua, input longint ub);
    longint r, sh;
    bit     f;
    // Match the WIDTH-bit input ports before arithmetic and signed conversion.
    // Directed tests may supply wider constants (or signed integer values).
    ua = ((ua % MOD) + MOD) % MOD;
    ub = ((ub % MOD) + MOD) % MOD;
    f  = 0;
    sh = ub % WIDTH;                             // low log2(WIDTH) bits of b
    case (o)
        `OP_VADD:   r = (ua + ub) % MOD;
        `OP_VSUB:   r = (ua - ub + MOD) % MOD;
        `OP_VAND:   r = ua & ub;
        `OP_VOR:    r = ua | ub;
        `OP_VXOR:   r = ua ^ ub;
        `OP_VCMPEQ: begin f = (ua == ub);                              r = f ? MOD - 1 : 0; end
        `OP_VCMPLT: begin f = (ref_to_signed(ua) < ref_to_signed(ub)); r = f ? MOD - 1 : 0; end
        `OP_VSLL:   r = (ua * (longint'(1) << sh)) % MOD;
        `OP_VSRL:   r = ua / (longint'(1) << sh);
        `OP_VMUL:   r = (ua * ub) % MOD;
        default:    r = 0;
    endcase
    return (longint'(f) << WIDTH) | r;
endfunction
