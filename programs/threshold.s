; Example 3 (docs/programming_guide.md): keep only the elements below a threshold
; mem[1] = mem[0] where mem[0] < 5, else 0  ->  0x00010003, mask 0101, 6 cycles

        .equ  THRESHOLD, 5

        SLI    THRESHOLD       ; scalar = 5
        VLD    v1, [0]         ; v1 = memory[0]
        VCMPLT v2, v1, s       ; v2 = 0xFF where v1 < 5 (signed), else 0x00
        VAND   v3, v1, v2      ; v3 = v1 where v1 < 5, else 0
        VST    v3, [1]         ; memory[1] = v3
        HALT

        .org  0
        .vec  3, 9, 1, 7       ; memory[0] = 0x07010903
