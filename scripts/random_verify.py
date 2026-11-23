#!/usr/bin/env python3
"""Random-program verification: RTL against the independent ISA model.

For every LANES x WIDTH build, random programs are generated from a seed, run
on the RTL (tb/prog_runner.v) and on scripts/lfmodel.py, and the complete final
state is compared: counters, mask, scalar, PC, HALT flag, v0-v7 and all 512
data-memory words. Functional coverage is collected from the model, and every
coverage goal must be hit or the run fails.

    make random_tests                     # 1000 programs x 12 builds, seed 1
    python3 scripts/random_verify.py --n 500 --seed 7 --configs 4x8,8x16
    python3 scripts/random_verify.py --mutations    # score against the 23 planted RTL bugs

Results: results/random_verification.txt (and results/random_mutations.txt).
Only the Python standard library is used; needs Icarus Verilog.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import tempfile

import lfasm
import lfrun
from lfmodel import ALU_OPCODES, HALT, Machine, NOP, SLI, VBCAST, VCMPEQ, VCMPLT, VLD, VMUL, VSLL, VSRL, VST, VADD, VSUB
from mutation_cases import MUTATIONS
from verify_evidence import atomic_write, source_hashes

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = tuple((lanes, width) for width in (8, 16, 2, 4) for lanes in (1, 4, 8))
MAX_CYCLES = 300
STATE_VALUES = len(lfrun.FIELDS) + 8 + 512   # compared per program
OPCODE_NAMES = {v: k for k, v in lfasm.Isa().opcodes.items()}


# -----------------------------------------------------------------------------
#  Program generator
# -----------------------------------------------------------------------------
def edge_values(width):
    full = (1 << width) - 1
    top = 1 << (width - 1)
    return [0, 1, full, top, top - 1, full - 1]


def random_lane(rng, width):
    return rng.choice(edge_values(width)) if rng.random() < 0.35 else rng.getrandbits(width)


def random_program(rng, lanes, width, no_halt=False):
    """Returns (instruction words, {address: data word}).

    no_halt=True gives a full 256-word image without HALT, which runs into the
    cycle limit and wraps the PC."""
    pool = rng.sample(range(512), 6) + [0, 511]
    address = lambda: rng.choice(pool) if rng.random() < 0.8 else rng.randrange(512)
    reg = lambda: rng.randrange(8)
    junk = lambda bits: rng.getrandbits(bits) if rng.random() < 0.3 else 0

    shape = rng.random()
    if no_halt:
        length = 256
    elif shape < 0.15:
        length = rng.randint(0, 8)
    elif shape < 0.75:
        length = rng.randint(9, 60)
    else:
        length = rng.randint(61, 255)

    kinds = [op for op in ALU_OPCODES] * 3 + [SLI] * 3 + [VLD] * 4 + [VST] * 4 + [VBCAST] * 2 + [NOP]
    words = [(VLD << 12) | (reg() << 9) | address() for _ in range(min(length, rng.randint(1, 4)))]
    while len(words) < length:
        op = HALT if not no_halt and rng.random() < 0.006 else rng.choice(kinds)
        if op in ALU_OPCODES:
            scalar = int(rng.random() < 0.3)
            word = (op << 12) | (reg() << 9) | (reg() << 6) | (reg() << 3) | (scalar << 2) | junk(2)
        elif op == SLI:
            imm = rng.choice(edge_values(8) + [width - 1, width, width + 1, 2 * width - 1]) \
                if rng.random() < 0.4 else rng.getrandbits(8)
            word = (SLI << 12) | (junk(4) << 8) | (imm & 0xFF)
        elif op in (VLD, VST):
            word = (op << 12) | (reg() << 9) | address()
        elif op == VBCAST:
            word = (VBCAST << 12) | (reg() << 9) | junk(9)
        else:  # NOP, HALT (a mid-program HALT leaves unexecuted instructions after it)
            word = (op << 12) | junk(12)
        words.append(word)
    if not no_halt:
        words.append((HALT << 12) | junk(12))

    data = {}
    for a in pool + [rng.randrange(512) for _ in range(16)]:
        if rng.random() < 0.9:
            value = 0
            for lane in range(lanes):
                value |= random_lane(rng, width) << (lane * width)
            data[a] = value
    return words[:256], data


# -----------------------------------------------------------------------------
#  Functional coverage, observed on the model
# -----------------------------------------------------------------------------
class CoverageMachine(Machine):
    def __init__(self, lanes, width, imem, dmem, coverage):
        super().__init__(lanes, width, imem, dmem)
        self.cov = coverage
        self.stored = set()
        self.last_written = None

    def step(self):
        if self.halted:
            return
        instr = self.imem[self.pc]
        op = instr >> 12
        vd, vs1, vs2, s = (instr >> 9) & 7, (instr >> 6) & 7, (instr >> 3) & 7, (instr >> 2) & 1
        c = self.cov
        c[f"op {OPCODE_NAMES[op]}"] += 1
        written = None
        if op in ALU_OPCODES:
            a = self.split(self.vregs[vs1])
            b = [self.scalar] * self.lanes if s else self.split(self.vregs[vs2])
            if s:
                c[f"scalar operand {OPCODE_NAMES[op]}"] += 1
            if instr & 3:
                c["ignored bits set (R-type)"] += 1
            sources = (vs1,) if s else (vs1, vs2)          # with s = 1, vs2 is not read
            if self.last_written is not None and self.last_written in sources:
                c["reads register written by previous instruction"] += 1
            if vd in sources:
                c["destination is also a source"] += 1
            for x, y in zip(a, b):
                if op == VADD and x + y > self.lane_mask:
                    c["VADD wraps (carry out)"] += 1
                if op == VSUB and x < y:
                    c["VSUB wraps (borrow)"] += 1
                if op == VMUL and x * y > self.lane_mask:
                    c["VMUL drops high bits"] += 1
                if op in (VSLL, VSRL) and y >= self.width:
                    c["shift amount >= WIDTH (low bits used)"] += 1
                if op == VCMPLT and (self.signed(x) < 0) != (self.signed(y) < 0):
                    c["VCMPLT operands of opposite sign"] += 1
            if op in (VCMPEQ, VCMPLT):
                for lane in range(self.lanes):
                    flag = self.lane_op(op, a[lane], b[lane])[1]
                    c[f"compare lane {lane} {'true' if flag else 'false'}"] += 1
            written = vd
        elif op == SLI:
            if (instr >> 8) & 0xF:
                c["ignored bits set (SLI)"] += 1
            if (instr & 0xFF) >> self.width:
                c["SLI immediate wider than WIDTH"] += 1
        elif op == VBCAST:
            written = vd
        elif op == VLD:
            if instr & 0x1FF in self.stored:
                c["load from an address stored earlier"] += 1
            written = vd
        elif op == VST:
            addr = instr & 0x1FF
            if addr in self.stored:
                c["store overwrites an earlier store"] += 1
            self.stored.add(addr)
        elif op == HALT:
            if self.pc + 1 < 256 and any(w != 0xF000 for w in self.imem[self.pc + 1:]):
                c["HALT with instructions after it"] += 1
        self.last_written = written
        super().step()


def coverage_goals(lanes, width):
    goals = [f"op {OPCODE_NAMES[op]}" for op in range(16)]
    goals += [f"scalar operand {OPCODE_NAMES[op]}" for op in ALU_OPCODES]
    goals += [f"compare lane {lane} {v}" for lane in range(lanes) for v in ("true", "false")]
    goals += ["ignored bits set (R-type)", "ignored bits set (SLI)", "reads register written by previous instruction",
              "destination is also a source", "VADD wraps (carry out)", "VSUB wraps (borrow)",
              "VMUL drops high bits", "VCMPLT operands of opposite sign", "load from an address stored earlier",
              "store overwrites an earlier store", "HALT with instructions after it",
              "program reaches the cycle limit (no HALT)", "PC wraps past 255"]
    if width < 8:
        goals.append("SLI immediate wider than WIDTH")
    if width < 256:
        goals.append("shift amount >= WIDTH (low bits used)")
    return goals


# -----------------------------------------------------------------------------
#  Running
# -----------------------------------------------------------------------------
def programs_for(seed, lanes, width, count):
    rng = random.Random(f"laneforge-{seed}-L{lanes}-W{width}")
    # Every 40th program (from index 7) is a full image without HALT.
    return [random_program(rng, lanes, width, no_halt=(i % 40 == 7)) for i in range(count)]


def check_config(lanes, width, count, seed, work, args, rtl_dir=None, timeout=600):
    programs = programs_for(seed, lanes, width, count)
    binary, _ = lfrun.compile_runner(lanes, width, work, args.iverilog, rtl_dir)
    states = lfrun.run_rtl(binary, programs, lanes, width, work, args.vvp, MAX_CYCLES, timeout)
    coverage, failures, executed = Counter(), [], 0
    for index, ((words, data), rtl) in enumerate(zip(programs, states)):
        machine = CoverageMachine(lanes, width, words, data, coverage)
        steps = machine.run(MAX_CYCLES)
        executed += steps
        if not machine.halted:
            coverage["program reaches the cycle limit (no HALT)"] += 1
        if steps > 256:
            coverage["PC wraps past 255"] += 1
        model = machine.state()
        model["steps"] = steps
        diffs = lfrun.compare(rtl, model)
        if diffs:
            failures.append((index, words, data, diffs))
    return {"lanes": lanes, "width": width, "programs": count, "executed": executed,
            "coverage": coverage, "failures": failures, "goals": len(coverage_goals(lanes, width)),
            "holes": [g for g in coverage_goals(lanes, width) if not coverage[g]]}


def tool_line(command):
    try:
        return subprocess.run(command, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT).stdout.splitlines()[0]
    except (OSError, IndexError):
        return "unknown"


def header(args, title, reproduce):
    hashes = source_hashes()
    return (f"# LaneForge {title}\n"
            f"# Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            f"# Tools: {tool_line([args.iverilog, '-V'])}; Python {platform.python_version()}\n"
            f"# Seed: {args.seed}; programs per build: {args.n}; cycle limit per program: {MAX_CYCLES}\n"
            f"# Reproduce: {reproduce}\n"
            "# Source SHA-256 hashes:\n" + "".join(f"{v}  {k}\n" for k, v in hashes.items()) + "\n")


def describe_failure(lanes, width, index, words, data, diffs):
    text = f"### L{lanes}_W{width} program {index}: {len(diffs)} difference(s)\n"
    text += "".join(f"  {d}\n" for d in diffs[:20])
    text += "  program:\n" + "".join(f"    {a:3d}  {w:04X}  {lfasm.disassemble(w)}\n" for a, w in enumerate(words))
    text += "  data:\n" + "".join(f"    mem[{a}] = {v:#x}\n" for a, v in sorted(data.items()))
    return text


def verify(args, configs):
    with tempfile.TemporaryDirectory(prefix="laneforge-random-") as tmp:
        def job(config):
            lanes, width = config
            result = check_config(lanes, width, args.n, args.seed, Path(tmp) / f"L{lanes}_W{width}", args)
            status = "PASS" if not result["failures"] and not result["holes"] else "FAIL"
            print(f"L{lanes}_W{width}: {status}, {args.n} programs, {result['executed']} instructions, "
                  f"{len(result['failures'])} mismatches, {len(result['holes'])} coverage holes", flush=True)
            return result

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(job, configs))

    total_programs = sum(r["programs"] for r in results)
    total_executed = sum(r["executed"] for r in results)
    passed = all(not r["failures"] and not r["holes"] for r in results)
    report = header(args, "random-program verification (RTL vs independent ISA model)",
                    f"make random_tests N={args.n} SEED={args.seed}")
    report += "## Summary by build\n"
    for r in results:
        status = "PASS" if not r["failures"] and not r["holes"] else "FAIL"
        report += (f"L{r['lanes']}_W{r['width']}: {status}, {r['programs']} programs, "
                   f"{r['executed']} instructions executed, {r['programs'] * STATE_VALUES} state values compared, "
                   f"{len(r['failures'])} mismatches, {r['goals'] - len(r['holes'])}/{r['goals']} applicable "
                   f"coverage goals hit, holes: {', '.join(r['holes']) or 'none'}\n")
    report += (f"\nTotal: {total_programs} programs, {total_executed} instructions executed, "
               f"{total_programs * STATE_VALUES} state values compared "
               f"({STATE_VALUES} per program: {len(lfrun.FIELDS)} status values, 8 vector registers, 512 memory words); "
               f"result: {'ALL PASS' if passed else 'FAILED'}\n")
    merged = Counter()
    for r in results:
        merged.update(r["coverage"])
    report += ("\n## Functional coverage, all builds together (times each goal was hit)\n"
               "Goals that depend on the build (per-lane compares, narrow-width SLI) apply only where they exist.\n")
    goals = []
    for r in results:
        goals += [g for g in coverage_goals(r["lanes"], r["width"]) if g not in goals]
    report += "".join(f"{merged[g]:>9}  {g}\n" for g in goals)
    failures = [(r["lanes"], r["width"], *f) for r in results for f in r["failures"]]
    if failures:
        report += "\n## Mismatches (first 10)\n" + "".join(describe_failure(*f) for f in failures[:10])
    atomic_write(ROOT / args.out, report)
    print(f"Total: {total_programs} programs, {total_executed} instructions; "
          f"{'ALL PASS' if passed else 'FAILED'} -> {args.out}", flush=True)
    return passed


def mutate(destination, case):
    shutil.copytree(ROOT / "rtl", destination)
    path = destination / Path(case.path).name
    text = path.read_text()
    for old, new, occurrences in case.edits:
        if text.count(old) != occurrences:
            raise ValueError(f"{case.name}: expected {occurrences} occurrence(s) of {old!r}")
        text = text.replace(old, new)
    path.write_text(text)


def mutations(args):
    """Run the random suite against each planted RTL bug from scripts/mutation_cases.py."""
    with tempfile.TemporaryDirectory(prefix="laneforge-random-mut-") as tmp:
        def job(case):
            work = Path(tmp) / case.name
            try:
                mutate(work / "rtl", case)
                result = check_config(case.lanes, case.width, args.n, args.seed, work / "build", args,
                                      work / "rtl", timeout=args.mutation_timeout)
                status = "DETECTED" if result["failures"] else "NOT DETECTED"
                first = result["failures"][0][3][0] if result["failures"] else ""
                detail = (f"{len(result['failures'])}/{args.n} programs differ; first: {first}"
                          if result["failures"] else f"all {args.n} programs agree with the ISA model")
            except lfrun.RunError as error:
                if "timed out" in str(error):
                    status = "HANG"
                    detail = (f"the simulation does not finish within {args.mutation_timeout}s "
                              "(zero-delay combinational loop)")
                else:
                    status, detail = "ERROR", str(error).splitlines()[0]
            except (ValueError, OSError) as error:
                status, detail = "ERROR", str(error).splitlines()[0]
            print(f"{case.name}: {status}", flush=True)
            return case, status, detail

        baseline = check_config(4, 8, args.n, args.seed, Path(tmp) / "baseline", args)
        if baseline["failures"]:
            raise RuntimeError("unmodified RTL fails the random suite; no mutation score is valid")
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            rows = list(pool.map(job, MUTATIONS))

    detected = sum(status == "DETECTED" for _, status, _ in rows)
    hangs = sum(status == "HANG" for _, status, _ in rows)
    errors = sum(status == "ERROR" for _, status, _ in rows)
    report = header(args, "random-program testing against the planted RTL bugs",
                    f"python3 scripts/random_verify.py --mutations --n {args.n} --seed {args.seed}")
    report += ("Each mutant from scripts/mutation_cases.py is built at its own LANES/WIDTH and run with the "
               "random programs only (no module testbenches). A mutant counts as detected when at least one "
               "program's final state differs from the ISA model; HANG means the simulation never finishes. "
               "The unmodified RTL passes the same programs.\n\n")
    report += (f"## Random programs alone: {detected}/{len(rows)} detected by a state mismatch, "
               f"{hangs} hang, {len(rows) - detected - hangs - errors} not detected, {errors} errors\n")
    for case, status, detail in rows:
        report += f"{case.name} [{status}] L{case.lanes}_W{case.width} {case.path}: {case.description}. {detail}\n"
    atomic_write(ROOT / args.mutation_out, report)
    print(f"Random programs alone: {detected}/{len(rows)} detected, {hangs} hang -> {args.mutation_out}", flush=True)
    return errors == 0


def parse_configs(text):
    if text == "all":
        return CONFIGS
    configs = []
    for item in text.split(","):
        lanes, width = item.lower().split("x")
        configs.append((int(lanes), int(width)))
    return tuple(configs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=1000, help="programs per build (default 1000)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--configs", default="all", help="'all' or a list like 1x8,4x8,8x16 (LANESxWIDTH)")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--out", default="results/random_verification.txt")
    parser.add_argument("--mutations", action="store_true", help="score the random suite against the planted RTL bugs")
    parser.add_argument("--mutation-out", default="results/random_mutations.txt")
    parser.add_argument("--mutation-timeout", type=int, default=60, help="seconds per mutant simulation")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    args = parser.parse_args(argv)
    if args.n < 1 or args.jobs < 1:
        parser.error("--n and --jobs must be positive")
    try:
        ok = mutations(args) if args.mutations else verify(args, parse_configs(args.configs))
    except (lfrun.RunError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
