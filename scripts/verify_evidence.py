#!/usr/bin/env python3
"""Generate complete simulation, mutation and coverage evidence using Icarus.

Uses only the Python standard library (Python 3.9+). Live RTL is never edited.
Build failures, simulator errors, timeouts and surviving mutants fail the run;
only a completed self-checking simulation can count as a detected mutation.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

from mutation_cases import COVERAGE_PROBES, MUTATIONS


ROOT = Path(__file__).resolve().parents[1]
CONFIGURATIONS = tuple((lanes, width) for width in (8, 16, 2, 4) for lanes in (1, 4, 8))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = [ROOT / "Makefile"]
    paths += sorted((ROOT / "rtl").glob("*.v")) + sorted((ROOT / "rtl").glob("*.vh"))
    paths += sorted((ROOT / "tb").glob("*.v")) + sorted((ROOT / "tb").glob("*.vh"))
    paths += sorted((ROOT / "scripts").glob("*.py"))
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(text, encoding="utf-8")
    pending.replace(path)


def invoke(command, cwd, timeout):
    try:
        result = subprocess.run(command, cwd=cwd, timeout=timeout, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return -1, output + "\nHARNESS ERROR: command timed out\n"
    except OSError as exc:
        return -1, f"HARNESS ERROR: {exc}\n"


def header(args, hashes):
    rc, version = invoke([args.iverilog, "-V"], ROOT, args.timeout)
    if rc:
        raise RuntimeError(version)
    return ("# LaneForge reproducible verification evidence\n"
            f"# Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            f"# Tool: {version.splitlines()[0]}\n"
            f"# Mode: {args.mode}; per-command timeout: {args.timeout}s\n"
            f"# Reproduce: make {'verify_matrix' if args.mode == 'matrix' else 'mutation_tests'}\n"
            "# Commands below use temporary paths recreated by the harness.\n"
            "# Source SHA-256 hashes:\n" +
            "".join(f"{value}  {name}\n" for name, value in hashes.items()) + "\n")


def run_bench(source, build, bench, lanes, width, args, overrides=()):
    build.mkdir(parents=True, exist_ok=True)
    binary = build / "simulation.out"
    top = f"tb_{bench}"
    compile_cmd = [args.iverilog, "-g2012", "-Wall", "-I", str(source / "rtl"),
                   "-I", str(source / "tb"), "-s", top,
                   "-P", f"{top}.LANES={lanes}", "-P", f"{top}.WIDTH={width}"]
    for parameter, value in overrides:
        compile_cmd += ["-P", f"{top}.{parameter}={value}"]
    compile_cmd += ["-o", str(binary)]
    compile_cmd += [str(p) for p in sorted((source / "rtl").glob("*.v"))]
    compile_cmd += [str(source / "tb" / f"{top}.v")]
    rc, output = invoke(compile_cmd, source, args.timeout)
    log = "$ " + shlex.join(compile_cmd) + "\n" + output
    if rc:
        return {"status": "ERROR", "log": log, "checks": None}
    run_cmd = [args.vvp, "-n", str(binary), f"+vcd={build / 'waveform.vcd'}"]
    rc, output = invoke(run_cmd, source, args.timeout)
    log += "$ " + shlex.join(run_cmd) + "\n" + output
    count = re.findall(r"^\s*total checks: (\d+)\s*$", output, re.MULTILINE)
    failed = re.search(r"^FAILED with ([1-9]\d*) errors\s*$", output, re.MULTILINE)
    passed = re.search(r"^ALL PASS\s*$", output, re.MULTILINE)
    if rc or len(count) != 1 or (bool(passed) == bool(failed)):
        status = "ERROR"
    else:
        status = "PASS" if passed else "FAIL"
    return {"status": status, "log": log, "checks": int(count[0]) if len(count) == 1 else None}


def snapshot(destination):
    destination.mkdir(parents=True)
    for directory in ("rtl", "tb"):
        shutil.copytree(ROOT / directory, destination / directory)


def matrix(args, prefix, source, work):
    benches = [p.stem.removeprefix("tb_") for p in sorted((source / "tb").glob("tb_*.v"))]

    def configuration(config):
        lanes, width = config
        tag = f"L{lanes}_W{width}"
        rows, log = [], prefix + f"# Configuration: LANES={lanes}, WIDTH={width}\n\n"
        for bench in benches:
            result = run_bench(source, work / tag / bench, bench, lanes, width, args)
            rows.append((bench, result["status"], result["checks"]))
            log += f"## tb_{bench}: {result['status']}\n" + result["log"] + "\n"
        passed = all(row[1] == "PASS" for row in rows)
        total = sum(row[2] or 0 for row in rows)
        log += "## Summary\n" + "".join(f"tb_{b}: {s}, checks={n}\n" for b, s, n in rows)
        log += f"Total checks: {total}; configuration: {'PASS' if passed else 'FAIL'}\n"
        atomic_write(ROOT / "results" / "configurations" / f"{tag}.txt", log)
        print(f"{tag}: {'PASS' if passed else 'FAIL'}, {len(rows)} benches, {total} checks", flush=True)
        return tag, passed, total

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(configuration, CONFIGURATIONS))
    summary = prefix + "## Complete suite by configuration\n"
    summary += "".join(f"{tag}: {'PASS' if passed else 'FAIL'}, {total} checks; log: results/configurations/{tag}.txt\n"
                       for tag, passed, total in rows)
    atomic_write(ROOT / "results" / "verification_matrix.txt", summary)
    return all(passed for _, passed, _ in rows)


def prepare_case(case, source, destination):
    shutil.copytree(source, destination)
    path = destination / case.path
    text = path.read_text()
    details = ""
    for old, new, occurrences in case.edits:
        found = text.count(old)
        if found != occurrences:
            raise ValueError(f"{case.name}: expected {occurrences} occurrences of {old!r}, found {found}")
        text = text.replace(old, new)
        details += f"Replace {occurrences} occurrence(s):\n- {old}\n+ {new}\n"
    path.write_text(text)
    details += f"Original SHA-256: {digest(source / case.path)}\nAltered SHA-256: {digest(path)}\n"
    return details


def campaigns(args, prefix, source, work):
    all_cases = MUTATIONS + COVERAGE_PROBES
    keys = sorted({(case.bench, case.lanes, case.width) for case in all_cases})

    def baseline(key):
        bench, lanes, width = key
        return key, run_bench(source, work / "baseline" / f"{bench}_L{lanes}_W{width}",
                             bench, lanes, width, args)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        baselines = dict(pool.map(baseline, keys))
    baseline_log = "## Unmodified RTL and testbench baselines\n"
    for key, result in baselines.items():
        baseline_log += f"### {key}: {result['status']}\n{result['log']}\n"
    if any(result["status"] != "PASS" for result in baselines.values()):
        atomic_write(ROOT / "results" / "mutations.txt", prefix + baseline_log +
                     "HARNESS ERROR: an unmodified baseline failed; no mutation score is valid.\n")
        print("Mutation baseline failed; no mutants credited.", flush=True)
        return False

    def execute(case):
        try:
            details = prepare_case(case, source, work / "cases" / case.name / "source")
            result = run_bench(work / "cases" / case.name / "source",
                               work / "cases" / case.name / "build", case.bench,
                               case.lanes, case.width, args, case.overrides)
            detected = result["status"] == "FAIL"
            if case.name.startswith("C"):
                detected = detected and "coverage hole:" in result["log"]
            status = "DETECTED" if detected else ("SURVIVED" if result["status"] == "PASS" else "ERROR")
            log = (f"### {case.name}: {case.description}\nFile: {case.path}; test: tb_{case.bench}; "
                   f"LANES={case.lanes}, WIDTH={case.width}\n{details}Result: {status}\n{result['log']}\n")
        except (OSError, ValueError) as exc:
            status, log = "ERROR", f"### {case.name}: HARNESS ERROR: {exc}\n"
        print(f"{case.name}: {status} ({case.bench})", flush=True)
        return case.name, status, log

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        outcomes = list(pool.map(execute, all_cases))
    for cases, filename, label in ((MUTATIONS, "mutations.txt", "RTL mutants"),
                                    (COVERAGE_PROBES, "coverage_guards.txt", "Coverage probes")):
        names = {case.name for case in cases}
        selected = [row for row in outcomes if row[0] in names]
        detected = sum(status == "DETECTED" for _, status, _ in selected)
        report = prefix + baseline_log + f"## {label}: {detected}/{len(cases)} detected\n"
        report += "".join(f"{name}: {status}\n" for name, status, _ in selected) + "\n"
        report += "".join(log for _, _, log in selected)
        atomic_write(ROOT / "results" / filename, report)
        print(f"{label}: {detected}/{len(cases)} detected", flush=True)
    return all(status == "DETECTED" for _, status, _ in outcomes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("matrix", "mutations"))
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be positive")
    hashes = source_hashes()
    prefix = header(args, hashes)
    with tempfile.TemporaryDirectory(prefix="laneforge-evidence-") as temporary:
        work = Path(temporary)
        source = work / "source"
        snapshot(source)
        function = matrix if args.mode == "matrix" else campaigns
        passed = function(args, prefix, source, work)
    if source_hashes() != hashes:
        raise RuntimeError("Live sources changed during verification; regenerate the evidence")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
