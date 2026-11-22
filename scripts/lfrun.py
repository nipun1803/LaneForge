#!/usr/bin/env python3
"""Run programs on the RTL (tb/prog_runner.v) and on the Python ISA model, and compare.

Library used by the random-program verification and the benchmarks, and a
command-line tool for running any one program:

    python3 scripts/lfrun.py programs/add.s                 # 4 lanes x 8 bits
    python3 scripts/lfrun.py prog.s --lanes 8 --width 16
    python3 scripts/lfrun.py --hex prog.hex --data prog_data.hex

It prints the final state from the RTL and reports MATCH if the RTL and the
model agree on every counter, the mask, the scalar, the PC, the HALT flag, all
eight vector registers and every data-memory word.
Only the Python standard library is used; needs Icarus Verilog.
"""

import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import lfasm
from lfmodel import Machine

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tb" / "prog_runner.v"
FIELDS = ("steps", "cycle_count", "instr_count", "lane_ops", "mask", "scalar", "pc", "halted")


class RunError(RuntimeError):
    pass


def _call(command, timeout):
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, timeout=timeout,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunError(f"{shlex.join(command)}: {error}") from error
    if result.returncode:
        raise RunError(f"{shlex.join(command)} failed:\n{result.stdout}")
    return result.stdout


def compile_runner(lanes, width, build_dir, iverilog="iverilog", rtl_dir=None, timeout=300):
    """Compile prog_runner for one LANES/WIDTH build; returns (binary path, command)."""
    rtl_dir = Path(rtl_dir or ROOT / "rtl")
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    binary = build_dir / f"prog_runner_L{lanes}_W{width}.out"
    command = [iverilog, "-g2012", "-Wall", "-I", str(rtl_dir), "-I", str(ROOT / "tb"),
               "-s", "prog_runner", "-P", f"prog_runner.LANES={lanes}",
               "-P", f"prog_runner.WIDTH={width}", "-o", str(binary)]
    command += [str(p) for p in sorted(rtl_dir.glob("*.v"))] + [str(RUNNER)]
    _call(command, timeout)
    return binary, command


def write_program(prefix, index, words, data, lanes, width):
    """Write <prefix>_<index>.hex and <prefix>_<index>_data.hex (always non-empty)."""
    digits = (lanes * width + 3) // 4
    Path(f"{prefix}_{index}.hex").write_text("".join(f"{w:04X}\n" for w in words) or "F000\n")
    top = max(data) if data else 0
    Path(f"{prefix}_{index}_data.hex").write_text(
        "".join(f"{data.get(a, 0):0{digits}X}\n" for a in range(top + 1)))


def _value(text):
    """Hex value from the runner; a value with unknown (x/z) bits stays a string."""
    try:
        return int(text, 16)
    except ValueError:
        return text.lower()


def _show(value):
    return f"{value:#x}" if isinstance(value, int) else f"{value} (unknown bits)"


def parse_states(text):
    states, current = [], None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == "P":
            current = {"index": int(parts[1]), "vregs": [0] * 8, "dmem": {}}
        elif tag == "S":
            current["steps"] = int(parts[1])
            for name, value in zip(FIELDS[1:], parts[2:]):
                current[name] = _value(value)
        elif tag == "V":
            current["vregs"][int(parts[1])] = _value(parts[2])
        elif tag == "M":
            current["dmem"][int(parts[1])] = _value(parts[2])
        elif tag == "E":
            states.append(current)
            current = None
    return states


def run_rtl(binary, programs, lanes, width, work_dir, vvp="vvp", max_cycles=1000, timeout=600):
    """Run [(words, data_dict), ...] on one compiled build; returns one state per program."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    prefix = work_dir / "prog"
    for index, (words, data) in enumerate(programs):
        write_program(prefix, index, words, data, lanes, width)
    out = work_dir / "states.txt"
    command = [vvp, "-n", str(binary), f"+prefix={prefix}", f"+count={len(programs)}",
               f"+out={out}", f"+maxcyc={max_cycles}"]
    log = _call(command, timeout)
    if f"RUNNER DONE {len(programs)}" not in log:
        raise RunError(f"runner did not finish:\n{log}")
    states = parse_states(out.read_text())
    if [s["index"] for s in states] != list(range(len(programs))):
        raise RunError("runner output is incomplete")
    return states


def run_model(words, data, lanes, width, max_cycles=1000):
    machine = Machine(lanes, width, imem=words, dmem=data)
    steps = machine.run(max_cycles)
    state = machine.state()
    state["steps"] = steps
    return state


def compare(rtl, model):
    """List of human-readable differences (empty when the states agree)."""
    diffs = [f"{name}: rtl={_show(rtl[name])} model={_show(model[name])}"
             for name in FIELDS if rtl[name] != model[name]]
    diffs += [f"v{r}: rtl={_show(a)} model={_show(b)}"
              for r, (a, b) in enumerate(zip(rtl["vregs"], model["vregs"])) if a != b]
    for address in sorted(set(rtl["dmem"]) | set(model["dmem"])):
        a, b = rtl["dmem"].get(address, 0), model["dmem"].get(address, 0)
        if a != b:
            diffs.append(f"mem[{address}]: rtl={_show(a)} model={_show(b)}")
    return diffs


def format_state(state, lanes, width):
    digits = (lanes * width + 3) // 4
    lines = [f"steps={state['steps']} cycle_count={state['cycle_count']} "
             f"instr_count={state['instr_count']} lane_ops={state['lane_ops']} "
             f"halted={state['halted']} pc={state['pc']}",
             f"mask={state['mask']:0{lanes}b} scalar={state['scalar']:#x}"]
    lines += [f"v{r} = {v:0{digits}x}" for r, v in enumerate(state["vregs"])]
    lines += [f"mem[{a}] = {w:0{digits}x}" for a, w in sorted(state["dmem"].items())]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", nargs="?", help="assembly file (.s)")
    parser.add_argument("--hex", help="instruction hex file instead of a .s source")
    parser.add_argument("--data", help="data hex file (with --hex)")
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--max-cycles", type=int, default=1000)
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    args = parser.parse_args(argv)

    if args.source:
        program = lfasm.assemble(Path(args.source).read_text(), args.lanes, args.width)
        words, data = program.words, program.data
    elif args.hex:
        words = [int(x, 16) for x in Path(args.hex).read_text().split()]
        data = {}
        if args.data:
            data = {a: int(x, 16) for a, x in enumerate(Path(args.data).read_text().split())}
    else:
        parser.error("give a .s source or --hex")

    with tempfile.TemporaryDirectory(prefix="laneforge-run-") as tmp:
        binary, _ = compile_runner(args.lanes, args.width, tmp, args.iverilog)
        rtl = run_rtl(binary, [(words, data)], args.lanes, args.width, tmp, args.vvp, args.max_cycles)[0]
    model = run_model(words, data, args.lanes, args.width, args.max_cycles)
    print(f"RTL final state (LANES={args.lanes}, WIDTH={args.width}):")
    print(format_state(rtl, args.lanes, args.width))
    diffs = compare(rtl, model)
    print("MATCH: RTL and ISA model agree" if not diffs else "MISMATCH:\n  " + "\n  ".join(diffs))
    return 1 if diffs else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RunError, lfasm.AsmError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
