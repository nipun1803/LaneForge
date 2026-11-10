# Architecture

How the parts of the processor connect, and what happens in one clock cycle.

## Block diagram

Solid arrows carry data, dotted arrows carry control signals from the decoder.

```mermaid
flowchart LR
    PC["Program counter<br/>+ HALT + counters"] -->|pc| IMEM["Instruction memory<br/>256 x 16 bits"]
    IMEM -->|instr| CTRL["Vector Control Unit<br/>(decoder)"]

    RF["Vector Register File<br/>8 vector registers<br/>+ scalar register"]
    MUX["Lane MUX<br/>vs2 or broadcast scalar"]
    subgraph VALU["Vector ALU"]
        direction TB
        L0["Lane ALU 0"]
        L1["Lane ALU 1"]
        L2["Lane ALU 2"]
        L3["Lane ALU 3"]
    end
    RC["Result Combiner<br/>write-back select"]
    DMEM["Data memory<br/>512 vectors"]

    RF -->|vs1| VALU
    RF -->|vs2, scalar| MUX
    MUX -->|operand B| VALU
    VALU -->|result, mask| RC
    MUX -->|broadcast| RC
    DMEM -->|load data| RC
    RC -->|write-back| RF
    RF -->|store data| DMEM

    CTRL -.->|register addresses, write enable| RF
    CTRL -.->|sel_scalar| MUX
    CTRL -.->|alu_op, same for all lanes| VALU
    CTRL -.->|wb_sel| RC
    CTRL -.->|mem_we, address| DMEM
```

| Block | File | Job |
|---|---|---|
| Program counter, HALT, counters | `rtl/simd_top.v` | chooses the next instruction, stops at HALT, counts cycles |
| Instruction memory | `rtl/simd_top.v` | holds the program (256 instructions) |
| Vector Control Unit | `rtl/vector_control.v` | decodes the instruction into control signals |
| Vector Register File | `rtl/vector_regfile.v` | 8 vector registers and 1 scalar register |
| Lane MUX | `rtl/lane_mux.v` | picks operand B: a vector register, or the scalar copied to every lane |
| Vector ALU | `rtl/vector_alu.v`, `rtl/lane_alu.v` | 4 lane ALUs working in parallel |
| Result Combiner | `rtl/result_combiner.v` | picks what is written back: ALU result, loaded data, or broadcast scalar |
| Data memory | `rtl/simd_top.v` | 512 words, each one whole vector |

## What happens in one clock cycle

Every instruction finishes in a single clock cycle.

```mermaid
flowchart TB
    F["1. Fetch<br/>read the instruction at pc"] --> D["2. Decode<br/>control unit produces control signals"]
    D --> R["3. Read<br/>register file outputs vs1 and vs2;<br/>lane MUX picks operand B"]
    R --> E["4. Execute<br/>all lane ALUs compute at once"]
    E --> S["5. Select<br/>result combiner picks the write-back value"]
    S --> W["6. Write (rising clock edge)<br/>register / scalar / memory updated, pc + 1"]
    W --> F
```

Steps 1 to 5 are combinational: they settle within the clock period. Step 6 happens on the clock edge, so all state changes at the same moment.

## Example: one `VADD v3, v1, v2`

```mermaid
sequenceDiagram
    participant IM as Instruction memory
    participant CU as Control unit
    participant RF as Register file
    participant ALU as Vector ALU (4 lanes)
    participant RC as Result combiner
    IM->>CU: instr = 0x1650
    CU->>RF: read v1 (port 1), v2 (port 2)
    CU->>ALU: alu_op = VADD (to all lanes)
    RF->>ALU: v1 = 04030201, v2 = 10101010
    ALU->>RC: result = 14131211
    CU->>RC: wb_sel = ALU
    RC->>RF: write 14131211 into v3 at the clock edge
```

## Main design decisions

| Decision | Choice | Why |
|---|---|---|
| Lane count and width | parameters `LANES`, `WIDTH` on every module | one design gives the scalar, 4-lane and 8-lane versions |
| Execution | single-cycle (one instruction per cycle) | cycle counts equal instruction counts, so results are easy to check by hand |
| Where constants live | one header file, `rtl/simd_defs.vh` | every file uses the same opcodes and field positions |
| Memories | inside the processor top level | the design is self-contained; testbenches load programs directly |
| Decoding | only in the control unit | datapath blocks just follow control signals, so each block stays simple |
| Register reads during a write | return the old value | correct for a single-cycle design: `VADD v1, v1, v2` uses the old `v1` |
| After HALT | every write is blocked in one place | nothing can change once the program has stopped |

**Next:** [instruction_set.md](instruction_set.md)
