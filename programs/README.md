# Example programs

Ready-to-run programs for `simd_top` (LANES=4, WIDTH=8). These are the three examples from [docs/programming_guide.md](../docs/programming_guide.md), stored as hex files and loaded through the `IMEM_INIT` / `DMEM_INIT` parameters.

```sh
make demo
```

| Program | Files | What it does | Result | Cycles |
|---|---|---|---|---|
| Vector add | `add.hex`, `add_data.hex` | `mem[2] = mem[0] + mem[1]` in 4 lanes | `mem[2] = 14131211` | 5 |
| Add constant | `add_const.hex`, `add_const_data.hex` | add scalar 5 to every lane | `mem[1] = 2D190F06` | 5 |
| Threshold | `threshold.hex`, `threshold_data.hex` | keep elements < 5, zero the rest | `mem[1] = 00010003`, mask `0101` | 6 |

`run_programs.v` runs all three, prints each result next to the expected value, and ends with `ALL PASS`.

## Writing your own

One 16-bit instruction per line in `*.hex`, one `LANES*WIDTH`-bit data word per line in `*_data.hex`. Encodings are listed in [docs/instruction_set.md](../docs/instruction_set.md). Unused instruction slots are `HALT` and unused data words are 0, so Icarus's `Not enough words in the file` warning is harmless (`make demo` hides it).
