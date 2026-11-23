#!/usr/bin/env python3
"""Scalar-versus-SIMD benchmarks on the same RTL (LANES = 1, 4, 8; WIDTH = 8).

Each workload is generated in assembly for a lane count L and an element
count N, assembled with scripts/lfasm.py, run on the RTL (tb/prog_runner.v)
and checked three ways: the output arrays against a plain Python computation,
the complete final state against the ISA model, and the cycle count against
the closed-form prediction. The LANES=1 build is the scalar baseline: same
instruction set, same control unit, same clock.

    make bench                           # -> results/bench.csv, results/bench.txt
    python3 scripts/bench.py --n 8,16,32 --lanes 1,4,8

Workloads (the ISA has no branches, so every loop is fully unrolled):
  W1  C = A + B            per vector: VLD, VLD, VADD, VST                 4N/L + 1 cycles
  W2  count(A < T)         per vector: VLD, VCMPLT s, VSUB (acc -= mask)   3N/L + 3 cycles
      A compare writes -1 (all ones) to true lanes, so subtracting it adds 1.
      The result is one partial count per lane; the L partial counts still
      have to be added, which this ISA cannot do across lanes. The column
      "est. +sum" is an ESTIMATE that charges L-1 extra cycles for that sum;
      those cycles are assumed, not executed or measured.
  W3  C = (A AND M) OR B   per vector: VLD x3, VAND, VOR, VST              6N/L + 1 cycles
  W4  Y = a*X + b          per vector: VLD, VMUL s, VADD, VST              4N/L + 4 cycles
Only the Python standard library is used; needs Icarus Verilog.
"""

import argparse
import csv
from datetime import datetime, timezone
import io
from pathlib import Path
import platform
import random
import subprocess
import sys
import tempfile

import lfasm
import lfrun
from verify_evidence import atomic_write, source_hashes

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 8
MASK = (1 << WIDTH) - 1
A_BASE, B_BASE, M_BASE, OUT_BASE = 0, 64, 128, 256
THRESHOLD, SCALE, OFFSET = 64, 3, 7


# -----------------------------------------------------------------------------
#  Workload generators: return (assembly source, expected output elements)
# -----------------------------------------------------------------------------
def vec_lines(base, values, lanes):
    lines = [f"        .org {base}"]
    for i in range(0, len(values), lanes):
        lines.append("        .vec " + ", ".join(str(v) for v in values[i:i + lanes]))
    return lines


def w1_add(lanes, n, rng):
    a = [rng.randrange(256) for _ in range(n)]
    b = [rng.randrange(256) for _ in range(n)]
    body = []
    for blk in range(n // lanes):
        body += [f"        VLD  v1, [{A_BASE + blk}]", f"        VLD  v2, [{B_BASE + blk}]",
                 "        VADD v3, v1, v2", f"        VST  v3, [{OUT_BASE + blk}]"]
    expected = [(x + y) & MASK for x, y in zip(a, b)]
    return ["; W1: C = A + B"] + body + ["        HALT"] + vec_lines(A_BASE, a, lanes) + vec_lines(B_BASE, b, lanes), expected


def w2_count(lanes, n, rng):
    a = [rng.randrange(128) for _ in range(n)]         # 0..127: signed and unsigned order agree
    body = [f"        SLI  {THRESHOLD}               ; scalar = threshold"]
    for blk in range(n // lanes):
        body += [f"        VLD  v1, [{A_BASE + blk}]",
                 "        VCMPLT v2, v1, s          ; -1 where a < T",
                 "        VSUB v6, v6, v2           ; count += 1 in those lanes"]
    body += [f"        VST  v6, [{OUT_BASE}]          ; one partial count per lane"]
    partial = [sum(1 for i in range(lane, n, lanes) if a[i] < THRESHOLD) for lane in range(lanes)]
    return ["; W2: count elements below a threshold"] + body + ["        HALT"] + vec_lines(A_BASE, a, lanes), partial


def w3_mask(lanes, n, rng):
    a = [rng.randrange(256) for _ in range(n)]
    m = [rng.randrange(256) for _ in range(n)]
    b = [rng.randrange(256) for _ in range(n)]
    body = []
    for blk in range(n // lanes):
        body += [f"        VLD  v1, [{A_BASE + blk}]", f"        VLD  v2, [{M_BASE + blk}]",
                 f"        VLD  v3, [{B_BASE + blk}]", "        VAND v4, v1, v2", "        VOR  v4, v4, v3",
                 f"        VST  v4, [{OUT_BASE + blk}]"]
    expected = [(x & y) | z for x, y, z in zip(a, m, b)]
    return (["; W3: C = (A AND M) OR B"] + body + ["        HALT"] + vec_lines(A_BASE, a, lanes) +
            vec_lines(M_BASE, m, lanes) + vec_lines(B_BASE, b, lanes)), expected


def w4_axpb(lanes, n, rng):
    x = [rng.randrange(256) for _ in range(n)]
    body = [f"        SLI  {OFFSET}", "        VBCAST v7               ; v7 = b in every lane",
            f"        SLI  {SCALE}                ; scalar = a"]
    for blk in range(n // lanes):
        body += [f"        VLD  v1, [{A_BASE + blk}]", "        VMUL v2, v1, s", "        VADD v2, v2, v7",
                 f"        VST  v2, [{OUT_BASE + blk}]"]
    expected = [(SCALE * v + OFFSET) & MASK for v in x]
    return ["; W4: Y = a*X + b (8-bit wrap-around)"] + body + ["        HALT"] + vec_lines(A_BASE, x, lanes), expected


WORKLOADS = {
    "W1": ("C = A + B", w1_add, lambda n, l: 4 * n // l + 1),
    "W2": ("count(A < T)", w2_count, lambda n, l: 3 * n // l + 3),
    "W3": ("C = (A AND M) OR B", w3_mask, lambda n, l: 6 * n // l + 1),
    "W4": ("Y = a*X + b", w4_axpb, lambda n, l: 4 * n // l + 4),
}


def outputs(name, state, lanes, n):
    """Read the result elements out of the final data memory."""
    words = state["dmem"]
    if name == "W2":
        word = words.get(OUT_BASE, 0)
        return [(word >> (lane * WIDTH)) & MASK for lane in range(lanes)]
    elements = []
    for blk in range(n // lanes):
        word = words.get(OUT_BASE + blk, 0)
        elements += [(word >> (lane * WIDTH)) & MASK for lane in range(lanes)]
    return elements


# -----------------------------------------------------------------------------
#  Running
# -----------------------------------------------------------------------------
def run(args):
    lane_counts = [int(x) for x in args.lanes.split(",")]
    sizes = [int(x) for x in args.n.split(",")]
    if 1 not in lane_counts:
        raise SystemExit("--lanes must include 1 (the scalar baseline)")
    rows, problems, sources = [], [], {}
    with tempfile.TemporaryDirectory(prefix="laneforge-bench-") as tmp:
        for lanes in lane_counts:
            jobs, programs = [], []
            for name, (_, generate, _) in WORKLOADS.items():
                for n in sizes:
                    if n % lanes:
                        raise SystemExit(f"N={n} is not a multiple of LANES={lanes}")
                    rng = random.Random(f"bench-{args.seed}-{name}-{n}")   # same data for every L
                    lines, expected = generate(lanes, n, rng)
                    source = "\n".join(lines) + "\n"
                    program = lfasm.assemble(source, lanes, WIDTH)
                    jobs.append((name, n, expected, program))
                    programs.append((program.words, program.data))
                    sources[(name, n, lanes)] = source
            work = Path(tmp) / f"L{lanes}"
            binary, _ = lfrun.compile_runner(lanes, WIDTH, work, args.iverilog)
            states = lfrun.run_rtl(binary, programs, lanes, WIDTH, work, args.vvp)
            for (name, n, expected, program), rtl in zip(jobs, states):
                model = lfrun.run_model(program.words, program.data, lanes, WIDTH)
                diffs = lfrun.compare(rtl, model)
                got = outputs(name, rtl, lanes, n)
                predicted = WORKLOADS[name][2](n, lanes)
                if diffs:
                    problems.append(f"{name} N={n} L={lanes}: RTL differs from model: {diffs[:3]}")
                if got != expected:
                    problems.append(f"{name} N={n} L={lanes}: wrong result {got} != {expected}")
                if rtl["cycle_count"] != predicted or not rtl["halted"]:
                    problems.append(f"{name} N={n} L={lanes}: {rtl['cycle_count']} cycles, predicted {predicted}")
                rows.append({"workload": name, "task": WORKLOADS[name][0], "n": n, "lanes": lanes,
                             "instructions": len(program.words), "cycles": rtl["cycle_count"],
                             "predicted_cycles": predicted, "lane_ops": rtl["lane_ops"],
                             "result": "PASS" if not diffs and got == expected and rtl["cycle_count"] == predicted
                             else "FAIL"})
    base = {(r["workload"], r["n"]): r["cycles"] for r in rows if r["lanes"] == 1}
    for r in rows:
        r["speedup"] = base[(r["workload"], r["n"])] / r["cycles"]
        r["elements_per_cycle"] = r["n"] / r["cycles"]
        r["lane_ops_per_cycle"] = r["lane_ops"] / r["cycles"]
        # W2 leaves L partial counts. Adding them would need L-1 more additions, which this
        # ISA cannot do; this column is an estimate that assumes L-1 extra cycles (not executed).
        r["speedup_est_with_lane_sum"] = (base[(r["workload"], r["n"])] / (r["cycles"] + r["lanes"] - 1)
                                       if r["workload"] == "W2" else r["speedup"])
    rows.sort(key=lambda r: (r["workload"], r["n"], r["lanes"]))
    return rows, problems, sources


def tool_line(command):
    try:
        return subprocess.run(command, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT).stdout.splitlines()[0]
    except (OSError, IndexError):
        return "unknown"


def write_results(rows, problems, sources, args):
    columns = ["workload", "task", "n", "lanes", "instructions", "cycles", "predicted_cycles", "lane_ops",
               "speedup", "speedup_est_with_lane_sum", "elements_per_cycle", "lane_ops_per_cycle", "result"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, columns, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (f"{r[k]:.3f}" if isinstance(r[k], float) else r[k]) for k in columns})
    atomic_write(ROOT / "results" / "bench.csv", buffer.getvalue())

    text = ("# LaneForge scalar-versus-SIMD benchmarks\n"
            f"# Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            f"# Tools: {tool_line([args.iverilog, '-V'])}; Python {platform.python_version()}\n"
            f"# Build: WIDTH={WIDTH}; LANES={args.lanes}; N={args.n}; data seed {args.seed}\n"
            "# Reproduce: make bench\n# Source SHA-256 hashes:\n" +
            "".join(f"{v}  {k}\n" for k, v in source_hashes().items()) + "\n")
    text += __doc__.split("\n\n")[3] + "\n\n"
    text += ("Every row is checked three ways: outputs against Python, full final state against the ISA model,\n"
             "and cycle count against the formula. Speedup = cycles(L=1) / cycles(L); same N, same data.\n"
             "'est. +sum' (W2 only) is an estimate: it adds L-1 assumed cycles for summing the per-lane\n"
             "partial counts, which this ISA cannot do; those cycles are not executed or measured.\n\n")
    text += (f"{'wl':<3} {'N':>3} {'L':>2} {'instr':>5} {'cycles':>6} {'pred':>5} {'lane_ops':>8} "
             f"{'speedup':>7} {'est. +sum':>9} {'elem/cyc':>8} {'ops/cyc':>7}  result\n")
    for r in rows:
        text += (f"{r['workload']:<3} {r['n']:>3} {r['lanes']:>2} {r['instructions']:>5} {r['cycles']:>6} "
                 f"{r['predicted_cycles']:>5} {r['lane_ops']:>8} {r['speedup']:>7.2f} "
                 f"{r['speedup_est_with_lane_sum']:>9.2f} {r['elements_per_cycle']:>8.3f} "
                 f"{r['lane_ops_per_cycle']:>7.3f}  {r['result']}\n")
    passed = not problems and all(r["result"] == "PASS" for r in rows)
    text += "\n" + ("ALL PASS\n" if passed else "FAILED\n" + "".join(f"  {p}\n" for p in problems))
    atomic_write(ROOT / "results" / "bench.txt", text)

    # Keep the largest-N programs as readable artefacts.
    largest = max(int(x) for x in args.n.split(","))
    names = {"W1": "w1_add", "W2": "w2_count", "W3": "w3_mask", "W4": "w4_axpb"}
    for (name, n, lanes), source in sources.items():
        if n == largest:
            header = f"; Generated by scripts/bench.py (N={n}, LANES={lanes}, WIDTH={WIDTH}); assemble with --lanes {lanes}\n"
            atomic_write(ROOT / "programs" / "bench" / f"{names[name]}_L{lanes}_N{n}.s", header + source)
    return passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lanes", default="1,4,8")
    parser.add_argument("--n", default="8,16,32", help="element counts (multiples of every lane count)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    args = parser.parse_args(argv)
    try:
        rows, problems, sources = run(args)
    except (lfrun.RunError, lfasm.AsmError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    passed = write_results(rows, problems, sources, args)
    for r in rows:
        if r["n"] == max(int(x) for x in args.n.split(",")):
            print(f"{r['workload']} N={r['n']:>2} L={r['lanes']}: {r['cycles']:>3} cycles, "
                  f"speedup {r['speedup']:.2f}x, {r['elements_per_cycle']:.2f} elements/cycle  {r['result']}")
    print("ALL PASS -> results/bench.csv, results/bench.txt" if passed else "FAILED (see results/bench.txt)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
