#!/usr/bin/env python3
"""Generate the report's result tables and numbers from the evidence in results/.

Writes docs/report/gen_results.tex, which main.tex \\input{}s. Every number in
the Results section therefore comes from a committed evidence file:

    results/bench.csv                 scalar-vs-SIMD cycles and speedups
    results/synth_scaling.csv         generic Yosys cells for 1, 4, 8 lanes
    results/fpga_summary.csv          ECP5 resources and Fmax
    results/random_verification.txt   random-program verification totals
    results/random_mutations.txt      random programs against the planted bugs
    results/verification_matrix.txt   module-testbench check totals
    results/mutations.txt             module-testbench mutation score

    make report      # regenerates this file, then builds docs/report/main.pdf
Only the Python standard library is used.
"""

import csv
from pathlib import Path
import re

from verify_evidence import atomic_write

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OUT = ROOT / "docs" / "report" / "gen_results.tex"
N = 32


def rows(name):
    return list(csv.DictReader((RESULTS / name).read_text().splitlines()))


def num(value, digits=0):
    """LaTeX number with thin-space thousands separators: 12{,}000."""
    text = f"{value:,.{digits}f}"
    return text.replace(",", "{,}")


def find(pattern, text, what):
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError(f"could not find {what}")
    return match


def main():
    macros, out = {}, []

    # ---- verification -------------------------------------------------------
    random_text = (RESULTS / "random_verification.txt").read_text()
    total = find(r"^Total: (\d+) programs, (\d+) instructions executed, (\d+) state values compared.*result: (.+)$",
                 random_text, "random-verification total")
    if total.group(4).strip() != "ALL PASS":
        raise ValueError("random verification did not pass")
    goals = len(re.findall(r"^\s+\d+  \S", random_text.split("## Functional coverage", 1)[1], re.MULTILINE))
    builds = len(re.findall(r"^L\d+_W\d+: PASS", random_text, re.MULTILINE))
    macros.update(RandPrograms=num(int(total.group(1))), RandInstr=num(int(total.group(2))),
                  RandValues=num(int(total.group(3))), RandGoals=str(goals), RandBuilds=str(builds),
                  RandPerBuild=num(int(total.group(1)) // builds))
    mut_text = (RESULTS / "random_mutations.txt").read_text()
    m = find(r"^## Random programs alone: (\d+)/(\d+) detected by a state mismatch, (\d+) hang, (\d+) not detected",
             mut_text, "random mutation score")
    macros.update(RandMutDetected=m.group(1), MutTotal=m.group(2), RandMutHang=m.group(3), RandMutMissed=m.group(4))
    unit = find(r"^## RTL mutants: (\d+)/(\d+) detected", (RESULTS / "mutations.txt").read_text(), "unit mutation score")
    macros.update(UnitMutDetected=unit.group(1))
    matrix = (RESULTS / "verification_matrix.txt").read_text()
    l4 = find(r"^L4_W8: PASS, (\d+) checks", matrix, "L4_W8 matrix total")
    configs = len(re.findall(r"^L\d+_W\d+: PASS", matrix, re.MULTILINE))
    macros.update(ModuleChecks=num(int(l4.group(1))), MatrixConfigs=str(configs))

    # ---- benchmarks -----------------------------------------------------------
    bench = [r for r in rows("bench.csv") if int(r["n"]) == N]
    if any(r["result"] != "PASS" for r in bench):
        raise ValueError("a benchmark row did not pass")
    by = {(r["workload"], int(r["lanes"])): r for r in bench}
    names = {"W1": "W1 $C=A+B$", "W2": "W2 count$(A<T)$", "W3": r"W3 $(A\wedge M)\vee B$", "W4": r"W4 $aX+b$"}
    lines = []
    for w in ("W1", "W2", "W3", "W4"):
        c = [int(by[(w, l)]["cycles"]) for l in (1, 4, 8)]
        s4, s8 = float(by[(w, 4)]["speedup"]), float(by[(w, 8)]["speedup"])
        e1, e8 = float(by[(w, 1)]["elements_per_cycle"]), float(by[(w, 8)]["elements_per_cycle"])
        extra = ""
        if w == "W2":
            extra = (f" \\\\ \\quad with lane sum & & & & ({float(by[(w, 4)]['speedup_with_reduction']):.2f}) & "
                     f"({float(by[(w, 8)]['speedup_with_reduction']):.2f}) & ")
        lines.append(f"{names[w]} & {c[0]} & {c[1]} & {c[2]} & {s4:.2f} & {s8:.2f} & {e1:.2f}\\,$\\to$\\,{e8:.2f}{extra} \\\\")
    out.append("\\newcommand{\\BenchRows}{%\n" + "\n".join(lines) + "}")
    coords4 = " ".join(f"({w},{float(by[(w, 4)]['speedup']):.2f})" for w in ("W1", "W2", "W3", "W4"))
    coords8 = " ".join(f"({w},{float(by[(w, 8)]['speedup']):.2f})" for w in ("W1", "W2", "W3", "W4"))
    out.append(f"\\newcommand{{\\SpeedupFourCoords}}{{{coords4}}}")
    out.append(f"\\newcommand{{\\SpeedupEightCoords}}{{{coords8}}}")
    for w, key in (("W1", "One"), ("W2", "Two"), ("W3", "Three"), ("W4", "Four")):
        macros[f"Speedup{key}Four"] = f"{float(by[(w, 4)]['speedup']):.2f}"
        macros[f"Speedup{key}Eight"] = f"{float(by[(w, 8)]['speedup']):.2f}"
    macros["SpeedupTwoEightRed"] = f"{float(by[('W2', 8)]['speedup_with_reduction']):.2f}"
    small = {r["workload"]: r for r in rows("bench.csv") if int(r["n"]) == 8 and int(r["lanes"]) == 8}
    macros["SpeedupTwoEightSmall"] = f"{float(small['W2']['speedup']):.2f}"

    # ---- generic synthesis -------------------------------------------------------
    synth = {int(r["lanes"]): r for r in rows("synth_scaling.csv")}
    labels = [("lane_alus_total", "Lane ALUs"), ("vector_regfile", "Vector register file"),
              ("result_combiner", "Result combiner"), ("lane_mux", "Lane MUX"),
              ("vector_control", "Control unit"), ("simd_core", r"\textbf{SIMD core}"),
              ("top_level_logic", "Top level (data memory, PC, counters)"),
              ("total_cells", r"\textbf{Total}"), ("total_flipflops", "of which flip-flops")]
    lines = []
    for key, label in labels:
        if key == "simd_core" or key == "total_cells":
            lines.append("\\midrule")
        lines.append(f"{label} & " + " & ".join(num(int(synth[l][key])) for l in (1, 4, 8)) + " \\\\")
    out.append("\\newcommand{\\SynthRows}{%\n" + "\n".join(lines) + "}")
    macros["CoreGrowthFour"] = f"{int(synth[4]['simd_core']) / int(synth[1]['simd_core']):.1f}"
    macros["CoreGrowthEight"] = f"{int(synth[8]['simd_core']) / int(synth[1]['simd_core']):.1f}"
    macros["GenericFlopsFour"] = num(int(synth[4]["total_flipflops"]))

    # ---- FPGA --------------------------------------------------------------------
    fpga = {int(r["lanes"]): r for r in rows("fpga_summary.csv")}
    spec = [("comb_cells", "Logic cells (TRELLIS\\_COMB)", 0), ("flipflops", "Flip-flops", 0),
            ("dpr16x4", "Distributed RAM (DPR16X4)", 0), ("dsp_mult18", "DSP multipliers", 0),
            ("bram_dp16kd", "Block RAM", 0), ("core_lut4", "SIMD core LUT4", 0),
            ("fmax_median_mhz", r"\textbf{$F_{\max}$ median (MHz)}", 2),
            ("fmax_range", "$F_{\\max}$ range (MHz)", None), ("critical_path_ns", "Critical path (ns)", 2)]
    lines = []
    for key, label, digits in spec:
        if key == "fmax_median_mhz":
            lines.append("\\midrule")
        if key == "fmax_range":
            cells = [f"{float(fpga[l]['fmax_worst_mhz']):.1f}--{float(fpga[l]['fmax_best_mhz']):.1f}" for l in (1, 4, 8)]
        else:
            cells = [num(float(fpga[l][key]), digits) for l in (1, 4, 8)]
        lines.append(f"{label} & " + " & ".join(cells) + " \\\\")
    out.append("\\newcommand{\\FpgaRows}{%\n" + "\n".join(lines) + "}")
    f = {l: float(fpga[l]["fmax_median_mhz"]) for l in (1, 4, 8)}
    macros.update(FmaxOne=f"{f[1]:.2f}", FmaxFour=f"{f[4]:.2f}", FmaxEight=f"{f[8]:.2f}",
                  ClockRatioFour=f"{f[4] / f[1]:.3f}", ClockRatioEight=f"{f[8] / f[1]:.3f}",
                  ClockLossEight=f"{100 * (1 - f[8] / f[1]):.0f}",
                  CombOne=num(int(fpga[1]["comb_cells"])), CombEight=num(int(fpga[8]["comb_cells"])),
                  CombRatioEight=f"{int(fpga[8]['comb_cells']) / int(fpga[1]['comb_cells']):.1f}",
                  FlopsFour=num(int(fpga[4]["flipflops"])),
                  PathOne=f"{float(fpga[1]['critical_path_ns']):.1f}",
                  PathEight=f"{float(fpga[8]['critical_path_ns']):.1f}")
    lines = []
    for w in ("W1", "W2", "W3", "W4"):
        key = "speedup_with_reduction" if w == "W2" else "speedup"
        e4 = float(by[(w, 4)][key]) * f[4] / f[1]
        e8 = float(by[(w, 8)][key]) * f[8] / f[1]
        if w == "W1":
            macros.update(EffOneFour=f"{e4:.2f}", EffOneEight=f"{e8:.2f}")
        t1 = int(by[(w, 1)]["cycles"]) / f[1] * 1000
        t8 = (int(by[(w, 8)]["cycles"]) + (7 if w == "W2" else 0)) / f[8] * 1000   # W2: + L-1 lane-sum cycles
        lines.append(f"{names[w]}{'$^\\dagger$' if w == 'W2' else ''} & {num(t1)} & {num(t8)} & {e4:.2f} & {e8:.2f} \\\\")
    out.append("\\newcommand{\\EffRows}{%\n" + "\n".join(lines) + "}")

    text = ("% Generated by scripts/report_tables.py from results/ -- do not edit by hand.\n" +
            "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in sorted(macros.items())) +
            "\n".join(out) + "\n")
    atomic_write(OUT, text)
    print(f"-> {OUT.relative_to(ROOT)} ({len(macros)} numbers, 5 tables)")


if __name__ == "__main__":
    main()
