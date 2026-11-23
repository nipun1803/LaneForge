#!/usr/bin/env python3
"""FPGA resource use and maximum frequency on a Lattice ECP5 (open-source flow).

For each lane count: Yosys `synth_ecp5` maps synthesis/fpga_top.v (the
processor plus an LFSR instruction source and one XOR-folded output, see that
file) to ECP5 primitives, then nextpnr-ecp5 places and routes it with several
placement seeds and reports the maximum clock frequency.

    make fpga                                  # LANES=1,4,8, seeds 1-3
    python3 scripts/fpga_flow.py --lanes 4 --seeds 1,2,3,4,5
    python3 scripts/fpga_flow.py --tools ~/tools/oss-cad-suite/bin

Outputs: results/fpga/L<lanes>_W<width>.txt (full evidence per build) and
results/fpga_summary.csv. Tools come from the YosysHQ OSS CAD Suite
(https://github.com/YosysHQ/oss-cad-suite-build); pass its bin/ with --tools.
Only the Python standard library is used.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys
import tempfile

from verify_evidence import atomic_write

ROOT = Path(__file__).resolve().parents[1]
RTL = ["lane_alu.v", "lane_mux.v", "result_combiner.v", "simd_top.v",
       "vector_alu.v", "vector_control.v", "vector_regfile.v"]
WRAPPER = ROOT / "synthesis" / "fpga_top.v"
PRIMITIVES = ("LUT4", "CCU2C", "PFUMX", "L6MUX21", "TRELLIS_FF", "TRELLIS_DPR16X4", "MULT18X18D", "DP16KD")
DEVICE_RESOURCES = ("TRELLIS_COMB", "TRELLIS_FF", "TRELLIS_RAMW", "MULT18X18D", "DP16KD", "TRELLIS_IO")


def run(command, cwd, timeout=3600):
    result = subprocess.run(command, cwd=cwd, text=True, timeout=timeout,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"{shlex.join(command)} failed:\n{result.stdout[-4000:]}")
    return result.stdout


def version(tool):
    try:
        return subprocess.run([tool, "--version" if "nextpnr" in tool else "-V"], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip().splitlines()[0]
    except (OSError, IndexError):
        return "unknown"


def short(name):
    parts = name.split("\\")
    return parts[1] if len(parts) > 1 else parts[0]


def parse_stat(text):
    """Yosys stat -> {module: {primitive: count, 'subs': {module: n}}}."""
    modules = {}
    for match in re.finditer(r"^=== (.+?) ===\n(.*?)(?=^=== |\Z)", text, re.MULTILINE | re.DOTALL):
        name, body = short(match.group(1)), match.group(2)
        if name == "design hierarchy":
            continue
        entry = {p: 0 for p in PRIMITIVES}
        for count, cell in re.findall(r"^\s+(\d+)\s+(\S+)$", body, re.MULTILINE):
            if cell in entry:
                entry[cell] = int(count)
        entry["subs"] = {}
        sub_block = re.search(r"^\s+\d+ submodules\n((?:\s+\d+\s+\S+\n?)+)", body, re.MULTILINE)
        if sub_block:
            for count, sub in re.findall(r"^\s+(\d+)\s+(\S+)$", sub_block.group(1), re.MULTILINE):
                entry["subs"][short(sub)] = int(count)
        modules[name] = entry
    return modules


def yosys_script(lanes, width, json_path, flatten):
    sources = " ".join(f"rtl/{f}" for f in RTL) + " synthesis/fpga_top.v"
    synth = f"synth_ecp5 -top fpga_top" + ("" if flatten else " -noflatten")
    if json_path:
        synth += f" -json {json_path}"
    return (f"read_verilog -Irtl {sources}; chparam -set LANES {lanes} -set WIDTH {width} fpga_top; "
            f"{synth}; tee -o {{stat}} stat")


def critical_path(report):
    path = report["critical_paths"][0]["path"]
    logic = sum(p["delay"] for p in path if p["type"] == "logic")
    routing = sum(p["delay"] for p in path if p["type"] == "routing")
    stages = []
    for p in path:
        cell = p["from"]["cell"]
        parts = cell.split(".")
        label = ".".join(parts[:2]) if parts[0] == "u_cpu" and len(parts) > 2 else parts[0].split("_TRELLIS")[0]
        label = re.sub(r"_(LUT4|TRELLIS|PFUMX|L6MUX21|CCU2C|MULT18X18D).*$", "", label)
        if not stages or stages[-1] != label:
            stages.append(label)
    return {"logic_ns": logic, "routing_ns": routing, "levels": sum(p["type"] == "logic" for p in path),
            "start": path[0]["from"]["cell"], "end": path[-1]["to"]["cell"], "stages": stages}


def build(lanes, args, work):
    work.mkdir(parents=True, exist_ok=True)
    yosys = str(Path(args.tools) / "yosys")
    nextpnr = str(Path(args.tools) / "nextpnr-ecp5")
    json_path = work / "netlist.json"
    flat_stat, hier_stat = work / "flat.stat", work / "hier.stat"
    flat_cmd = [yosys, "-q", "-p", yosys_script(lanes, args.width, json_path, True).format(stat=flat_stat)]
    hier_cmd = [yosys, "-q", "-p", yosys_script(lanes, args.width, None, False).format(stat=hier_stat)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(run, flat_cmd, ROOT), pool.submit(run, hier_cmd, ROOT)]:
            future.result()
    flat = parse_stat(flat_stat.read_text())["fpga_top"]
    hier = parse_stat(hier_stat.read_text())

    def place(seed):
        report = work / f"report_seed{seed}.json"
        log = work / f"pnr_seed{seed}.log"
        command = [nextpnr, f"--{args.device}", "--package", args.package, "--speed", str(args.speed),
                   "--json", str(json_path), "--freq", str(args.freq), "--seed", str(seed),
                   "--timing-allow-fail", "--report", str(report), "-l", str(log), "-q"]
        run(command, ROOT)
        data = json.loads(report.read_text())
        clocks = [v for k, v in data["fmax"].items() if "clk" in k]
        return {"seed": seed, "fmax": clocks[0]["achieved"], "util": data["utilization"],
                "path": critical_path(data), "command": command}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        placements = list(pool.map(place, args.seed_list))
    return {"lanes": lanes, "flat": flat, "hier": hier, "placements": placements,
            "yosys_flat": flat_cmd, "yosys_hier": hier_cmd, "work": str(work)}


def hier_rows(hier, lanes):
    """Per-block primitive counts, with instance multiplicities applied."""
    alu_instances = hier.get("vector_alu", {}).get("subs", {}).get("lane_alu", 0)
    rows = [("lane ALUs", hier["lane_alu"], alu_instances)]
    for name, label in (("vector_regfile", "vector register file"), ("result_combiner", "result combiner"),
                        ("lane_mux", "lane MUX"), ("vector_control", "control unit"),
                        ("simd_top", "simd_top own logic (data memory, PC, counters)"),
                        ("fpga_top", "measurement wrapper (LFSR, XOR fold)")):
        if name in hier:
            rows.append((label, hier[name], 1))
    return rows


def report_text(result, args, hashes, tools):
    lanes = result["lanes"]
    lines = ["# LaneForge FPGA implementation evidence (Lattice ECP5, open-source flow)",
             f"# Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             f"# Tools: {tools['yosys']}; {tools['nextpnr']}",
             f"# Device: LFE5U-{args.device.upper()}F, package {args.package}, speed grade {args.speed}",
             f"# Design: synthesis/fpga_top.v, LANES={lanes}, WIDTH={args.width}, instruction ROM excluded",
             f"# Reproduce: make fpga FPGA_LANES={lanes}",
             "# Yosys (flat, used for place and route): " + shlex.join(result["yosys_flat"]).replace(result["work"], "<work>"),
             "# Yosys (hierarchical, per-block counts): " + shlex.join(result["yosys_hier"]).replace(result["work"], "<work>"),
             "# Source SHA-256 hashes:"]
    lines += [f"{v}  {k}" for k, v in hashes.items()]
    flat = result["flat"]
    lines += ["", "## Mapped primitives (flat netlist)"]
    lines += [f"{flat[p]:>8}  {p}" for p in PRIMITIVES]
    lines += ["", "## Per-block primitives (hierarchical synthesis; lane ALUs multiplied by instance count)",
              f"{'block':<48}{'LUT4':>8}{'CCU2C':>7}{'PFUMX':>7}{'L6MUX':>7}{'FF':>7}{'DPR16X4':>9}{'MULT':>6}"]
    for label, entry, count in hier_rows(result["hier"], lanes):
        name = f"{label} (x{count})" if count != 1 else label
        lines.append(f"{name:<48}{entry['LUT4'] * count:>8}{entry['CCU2C'] * count:>7}{entry['PFUMX'] * count:>7}"
                     f"{entry['L6MUX21'] * count:>7}{entry['TRELLIS_FF'] * count:>7}"
                     f"{entry['TRELLIS_DPR16X4'] * count:>9}{entry['MULT18X18D'] * count:>6}")
    best = max(result["placements"], key=lambda p: p["fmax"])
    lines += ["", "## Device utilisation after place and route (seed %d)" % best["seed"]]
    for res in DEVICE_RESOURCES:
        u = best["util"].get(res, {"used": 0, "available": 0})
        pct = 100 * u["used"] / u["available"] if u["available"] else 0
        lines.append(f"{res:<14}{u['used']:>8} / {u['available']:<8}{pct:6.2f}%")
    lines += ["", f"## Maximum frequency of clk by placement seed (target {args.freq} MHz)"]
    lines += [f"seed {p['seed']}: {p['fmax']:.2f} MHz" for p in result["placements"]]
    fmaxes = [p["fmax"] for p in result["placements"]]
    lines += [f"best {max(fmaxes):.2f} MHz, median {statistics.median(fmaxes):.2f} MHz, worst {min(fmaxes):.2f} MHz"]
    path = best["path"]
    lines += ["", f"## Critical path (seed {best['seed']})",
              f"{path['logic_ns'] + path['routing_ns']:.2f} ns = {path['logic_ns']:.2f} ns logic "
              f"({path['levels']} logic stages) + {path['routing_ns']:.2f} ns routing",
              f"from {path['start']}", f"to   {path['end']}",
              "through: " + " -> ".join(path["stages"]),
              "", "## nextpnr command (best seed; <work> is a temporary directory)",
              shlex.join(best["command"]).replace(result["work"], "<work>")]
    return ("\n".join(lines) + "\n").replace(str(Path(args.tools)), "<oss-cad-suite>/bin")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lanes", default="1,4,8")
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--tools", default=os.path.expanduser("~/tools/oss-cad-suite/bin"))
    parser.add_argument("--device", default="85k", help="nextpnr-ecp5 device flag without dashes (default 85k)")
    parser.add_argument("--package", default="CABGA381")
    parser.add_argument("--speed", type=int, default=6)
    parser.add_argument("--freq", type=float, default=50, help="target clock in MHz (default 50)")
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args(argv)
    args.seed_list = [int(s) for s in args.seeds.split(",")]
    for tool in ("yosys", "nextpnr-ecp5"):
        if not (Path(args.tools) / tool).exists():
            print(f"error: {tool} not found in {args.tools} (install the OSS CAD Suite or pass --tools)",
                  file=sys.stderr)
            return 2
    tools = {"yosys": version(str(Path(args.tools) / "yosys")),
             "nextpnr": version(str(Path(args.tools) / "nextpnr-ecp5"))}
    paths = [ROOT / "rtl" / f for f in RTL] + [ROOT / "rtl" / "simd_defs.vh", WRAPPER, Path(__file__)]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

    rows = []
    with tempfile.TemporaryDirectory(prefix="laneforge-fpga-") as tmp:
        for lanes in (int(l) for l in args.lanes.split(",")):
            try:
                result = build(lanes, args, Path(tmp) / f"L{lanes}")
            except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as error:
                print(f"error: LANES={lanes}: {error}", file=sys.stderr)
                return 1
            atomic_write(ROOT / "results" / "fpga" / f"L{lanes}_W{args.width}.txt",
                         report_text(result, args, hashes, tools))
            best = max(result["placements"], key=lambda p: p["fmax"])
            fmaxes = [p["fmax"] for p in result["placements"]]
            core_luts = sum(e["LUT4"] * n for (label, e, n) in hier_rows(result["hier"], lanes)
                            if not label.startswith(("simd_top", "measurement")))
            core_ffs = sum(e["TRELLIS_FF"] * n for (label, e, n) in hier_rows(result["hier"], lanes)
                           if not label.startswith(("simd_top", "measurement")))
            row = {"lanes": lanes, "width": args.width,
                   "comb_cells": best["util"]["TRELLIS_COMB"]["used"],
                   "flipflops": best["util"]["TRELLIS_FF"]["used"],
                   "ram_slices": best["util"].get("TRELLIS_RAMW", {}).get("used", 0),
                   "dsp_mult18": best["util"].get("MULT18X18D", {}).get("used", 0),
                   "bram_dp16kd": best["util"].get("DP16KD", {}).get("used", 0),
                   "lut4": result["flat"]["LUT4"], "dpr16x4": result["flat"]["TRELLIS_DPR16X4"],
                   "core_lut4": core_luts, "core_ff": core_ffs,
                   "fmax_best_mhz": round(max(fmaxes), 2), "fmax_median_mhz": round(statistics.median(fmaxes), 2),
                   "fmax_worst_mhz": round(min(fmaxes), 2), "seeds": len(fmaxes),
                   "critical_path_ns": round(best["path"]["logic_ns"] + best["path"]["routing_ns"], 2)}
            rows.append(row)
            print(f"L={lanes}: {row['comb_cells']} comb, {row['flipflops']} FF, {row['dpr16x4']} DPR16X4, "
                  f"{row['dsp_mult18']} MULT18, Fmax best {row['fmax_best_mhz']} / median "
                  f"{row['fmax_median_mhz']} MHz", flush=True)

    summary = ROOT / "results" / "fpga_summary.csv"
    existing = {}
    if summary.exists():
        for old in csv.DictReader(summary.read_text().splitlines()):
            existing[(int(old["lanes"]), int(old["width"]))] = old
    for row in rows:
        existing[(row["lanes"], row["width"])] = row
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, list(rows[0]), lineterminator="\n")
    writer.writeheader()
    for key in sorted(existing):
        writer.writerow(existing[key])
    atomic_write(summary, buffer.getvalue())
    print("-> results/fpga_summary.csv, results/fpga/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
