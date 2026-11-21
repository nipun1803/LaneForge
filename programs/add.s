; Example 1 (docs/programming_guide.md): add two vectors, 4 lanes x 8 bits
; mem[2] = mem[0] + mem[1]  ->  0x14131211 after 5 cycles, lane_ops = 4

        VLD   v1, [0]          ; v1 = memory[0]
        VLD   v2, [1]          ; v2 = memory[1]
        VADD  v3, v1, v2       ; v3 = v1 + v2 in all 4 lanes at once
        VST   v3, [2]          ; memory[2] = v3
        HALT

; data (lane 0 first)
        .org  0
        .vec  1, 2, 3, 4       ; memory[0] = 0x04030201
        .vec  16, 16, 16, 16   ; memory[1] = 0x10101010
