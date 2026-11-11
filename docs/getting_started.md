# Getting Started

How to install the tools, run the tests, look at waveforms and run synthesis.

## 1. Install the tools

| Tool | Used for | Install |
|---|---|---|
| Icarus Verilog | compiling and simulating Verilog | macOS: `brew install icarus-verilog` · Ubuntu/WSL: `sudo apt install iverilog` |
| Yosys | synthesis (hardware cost estimate) | macOS: `brew install yosys` · Ubuntu/WSL: `sudo apt install yosys` |
| WaveTrace (VS Code extension) or GTKWave | viewing waveforms | VS Code Extensions panel → "WaveTrace" · Linux: `sudo apt install gtkwave` |
| Tectonic (optional) | building the report PDF | macOS: `brew install tectonic` |

Check the install:

```bash
iverilog -V      # Icarus Verilog version 12 or 13
yosys -V
```

## 2. Run the tests

All commands are run from the project root folder.

```bash
make all_tests              # every testbench, 4 lanes x 8 bits
make test_simd_top          # one testbench (the whole processor)
make help                   # list all targets and testbenches
```

Run the same tests on other sizes of the design:

```bash
make all_tests LANES=1      # scalar baseline
make all_tests LANES=8      # 8 lanes
make all_tests WIDTH=16     # 16-bit lanes
```

Every testbench ends with one of these lines:

```
ALL PASS
FAILED with 3 errors
```

`make` stops with an error as soon as a test fails, so `make all_tests` finishing means everything passed.

## 3. What happens when you run a test

```mermaid
flowchart LR
    A["make test_vector_alu"] --> B["iverilog compiles<br/>rtl/*.v + tb/tb_vector_alu.v"]
    B --> C["sim/tb_vector_alu_L4_W8.out"]
    C --> D["vvp runs the simulation"]
    D --> E["log: sim/..._L4_W8.log"]
    D --> F["waveform: sim/..._L4_W8.vcd"]
    E --> G{"log contains<br/>ALL PASS?"}
    G -- yes --> H["make succeeds"]
    G -- no --> I["make fails"]
```

Output files are named after the configuration (`L4_W8` = 4 lanes, 8 bits), so results from different sizes never overwrite each other.

## 4. View a waveform

1. Run a test, e.g. `make test_simd_top`.
2. Open `sim/tb_simd_top_L4_W8.vcd` in VS Code (WaveTrace) or GTKWave.
3. Add signals such as `dut.pc`, `dut.instr` and `dut.u_rf.vregs` to see the program run.

Large exhaustive tests only record the first, easy-to-read part of the run, to keep the files small.

## 5. Run synthesis

```bash
make synth LANES=4
```

This prints a cell count for the whole design and for each module, and saves the full report to `results/synth_L4_W8.txt`. See [synthesis_and_performance.md](synthesis_and_performance.md) for what the numbers mean.

## 6. Build the report

```bash
cd docs/report
tectonic main.tex           # produces main.pdf
```

Or upload `main.tex` and `references.bib` to Overleaf.

## 7. Troubleshooting

| Problem | Cause and fix |
|---|---|
| A test prints `FAIL` lines | Read the first `FAIL` line: it shows the inputs, the wrong output and the expected output. The full log is in `sim/`. |
| Not sure a rebuild happened after changing `LANES` | It always does, because build files are named per configuration. `make clean` removes all build outputs if you want a fresh start. |
| `tb_defs` fails with `WIDTH=6` | `WIDTH` must be a power of two (2, 4, 8, 16 ...), because shift amounts use `log2(WIDTH)` bits. |
| A new testbench does not compile | The Makefile already passes `-g2012`. Icarus does **not** support associative arrays, `string` methods like `getc()`, `$isunknown`, or unpacked structs. Use fixed-size arrays and `struct packed`. |
| `Not enough words in the file` warning | Normal when a hex program is shorter than the memory. The rest of memory keeps its default value. |
| GTKWave is not available on macOS | Use the WaveTrace extension in VS Code. |

**Next:** [architecture.md](architecture.md)
