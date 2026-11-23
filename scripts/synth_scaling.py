#!/usr/bin/env python3
"""Summarise generic Yosys synthesis reports across lane counts.

Reads results/synth_L<lanes>_W<width>.txt (written by `make synth`) and writes
results/synth_scaling.csv with the generic cell count of every block:

    make synth_scaling            # runs make synth for LANES=1,4,8, then this script
    python3 scripts/synth_scaling.py --lanes 1,4,8 --width 8

Only the Python standard library is used.
"""

import argparse
import csv
import io
from pathlib import Path
import re
import sys

from verify_evidence import atomic_write

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ("lane_alu", "vector_regfile", "result_combiner", "lane_mux", "vector_control")


def short(name):
    """'$paramod$abc\\vector_regfile' or '$paramod\\lane_alu\\WIDTH=...' -> module name."""
    parts = name.split("\\")
    return parts[1] if len(parts) > 1 else parts[0]


def parse(path):
    text = path.read_text()
    modules = {}
    for match in re.finditer(r"^=== (.+?) ===\n(.*?)(?=^=== |\Z)", text, re.MULTILINE | re.DOTALL):
        name, body = short(match.group(1)), match.group(2)
        if name == "design hierarchy":
            cells = re.search(r"^\s+(\d+) cells$", body, re.MULTILINE)
            flops = sum(int(n) for n in re.findall(r"^\s+(\d+)\s+\$_\w*DFF\w*_$", body, re.MULTILINE))
            modules["__total__"] = {"cells": int(cells.group(1)) if cells else 0, "flops": flops}
            continue
        cells = re.search(r"^\s+(\d+) cells$", body, re.MULTILINE)
        subs = {}
        sub_block = re.search(r"^\s+\d+ submodules\n((?:\s+\d+\s+\S+\n?)+)", body, re.MULTILINE)
        if sub_block:
            for count, sub in re.findall(r"^\s+(\d+)\s+(\S+)$", sub_block.group(1), re.MULTILINE):
                subs[short(sub)] = int(count)
        modules[name] = {"cells": int(cells.group(1)) if cells else 0, "subs": subs}
    return modules


def summarise(lanes, width):
    path = ROOT / "results" / f"synth_L{lanes}_W{width}.txt"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; run make synth LANES={lanes} WIDTH={width}")
    m = parse(path)
    alu_instances = m["vector_alu"]["subs"].get("lane_alu", 0)
    row = {"lanes": lanes, "width": width, "lane_alu_each": m["lane_alu"]["cells"],
           "lane_alu_instances": alu_instances,
           "lane_alus_total": m["lane_alu"]["cells"] * alu_instances}
    for block in BLOCKS[1:]:
        row[block] = m[block]["cells"]
    row["simd_core"] = row["lane_alus_total"] + sum(row[b] for b in BLOCKS[1:])
    row["top_level_logic"] = m["simd_top"]["cells"]
    row["total_cells"] = m["__total__"]["cells"]
    row["total_flipflops"] = m["__total__"]["flops"]
    expected = row["simd_core"] + row["top_level_logic"] + m.get("simd_synth_top", {}).get("cells", 0)
    if expected != row["total_cells"]:
        raise ValueError(f"{path}: block sum {expected} != reported total {row['total_cells']}")
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lanes", default="1,4,8")
    parser.add_argument("--width", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        rows = [summarise(int(l), args.width) for l in args.lanes.split(",")]
    except (FileNotFoundError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(ROOT / "results" / "synth_scaling.csv", buffer.getvalue())
    print(f"{'block':<18}" + "".join(f"{'L=' + str(r['lanes']):>10}" for r in rows))
    for key in ("lane_alus_total", "vector_regfile", "result_combiner", "lane_mux", "vector_control",
                "simd_core", "top_level_logic", "total_cells", "total_flipflops"):
        print(f"{key:<18}" + "".join(f"{r[key]:>10}" for r in rows))
    print("-> results/synth_scaling.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
