; Example 2 (docs/programming_guide.md): add a constant to every element
; mem[1] = mem[0] + 5  ->  0x2D190F06 after 5 cycles

        SLI   5                ; scalar = 5
        VLD   v1, [0]          ; v1 = memory[0]
        VADD  v2, v1, s        ; v2 = v1 + 5 in every lane (s = broadcast scalar)
        VST   v2, [1]          ; memory[1] = v2
        HALT

        .org  0
        .vec  1, 10, 20, 40    ; memory[0] = 0x28140A01
