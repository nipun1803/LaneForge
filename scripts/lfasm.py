#!/usr/bin/env python3
"""LaneForge assembler and disassembler.

Turns assembly source (.s) into the hex files that simd_top loads through its
IMEM_INIT / DMEM_INIT parameters:

    python3 scripts/lfasm.py programs/add.s                # -> add.hex (+ add_data.hex)
    python3 scripts/lfasm.py prog.s -o out.hex -d out_data.hex --lanes 8 --width 8
    python3 scripts/lfasm.py prog.s --listing              # print address / hex / source
    python3 scripts/lfasm.py --disasm programs/add.hex     # hex -> assembly

Opcodes and instruction-field positions are read from rtl/simd_defs.vh, the
same header the RTL includes, so the assembler cannot drift from the hardware.

Source syntax (one statement per line, case-insensitive mnemonics):

    ; comment            # comment            // comment
    .equ  NAME, expr     named constant, usable in any later expression
    .org  expr           set the data-memory address for the next .vec/.word
    .vec  e0, e1, ...    one data word given lane by lane (lane 0 first);
                         exactly LANES elements, each in -2^(W-1) .. 2^W-1
    .word expr           one raw LANES*WIDTH-bit data word
    VADD  vd, vs1, vs2   R-type ALU / compare (VADD VSUB VAND VOR VXOR
    VADD  vd, vs1, s       VCMPEQ VCMPLT VSLL VSRL VMUL); "s" = scalar operand
    VBCAST vd            every lane of vd = scalar
    VLD   vreg, [expr]   vreg = memory[expr]
    VST   vreg, [expr]   memory[expr] = vreg
    SLI   expr           scalar = expr (0..255, or -128..-1 as an 8-bit pattern)
    NOP
    HALT

Expressions use integers (decimal, 0x hex, 0b binary), .equ names, + - * ( ).
Only the Python standard library is used (Python 3.9+).
"""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "rtl" / "simd_defs.vh"

ALU_OPS = ("VADD", "VSUB", "VAND", "VOR", "VXOR", "VCMPEQ", "VCMPLT", "VSLL", "VSRL", "VMUL")


class AsmError(Exception):
    """An error tied to one source line."""

    def __init__(self, line, message):
        super().__init__(message)
        self.line = line


# -----------------------------------------------------------------------------
#  ISA definitions, read from rtl/simd_defs.vh
# -----------------------------------------------------------------------------
class Isa:
    def __init__(self, defs_path=DEFS):
        text = Path(defs_path).read_text()
        self.opcodes = {m.group(1): int(m.group(2), 2)
                        for m in re.finditer(r"`define\s+OP_(\w+)\s+4'b([01]+)", text)}
        self.fields = {}
        for m in re.finditer(r"`define\s+F_(\w+)\s+(\d+)(?::(\d+))?\s", text):
            hi = int(m.group(2))
            lo = int(m.group(3)) if m.group(3) is not None else hi
            self.fields[m.group(1)] = (hi, lo)
        sizes = {m.group(1): int(m.group(2))
                 for m in re.finditer(r"`define\s+(INSTR_W|IMEM_DEPTH|DMEM_DEPTH|NUM_VREGS)\s+(\d+)", text)}
        self.instr_w = sizes["INSTR_W"]
        self.imem_depth = sizes["IMEM_DEPTH"]
        self.dmem_depth = sizes["DMEM_DEPTH"]
        self.num_vregs = sizes["NUM_VREGS"]
        self.names = {value: name for name, value in self.opcodes.items()}
        required = set(ALU_OPS) | {"NOP", "SLI", "VLD", "VST", "VBCAST", "HALT"}
        missing = required - set(self.opcodes)
        if missing or len(self.names) != len(self.opcodes):
            raise RuntimeError(f"{defs_path}: missing or duplicate opcodes: {sorted(missing)}")
        for name in ("OP", "VD", "VS1", "VS2", "S", "VREG", "ADDR", "IMM8"):
            if name not in self.fields:
                raise RuntimeError(f"{defs_path}: field F_{name} not found")

    def width(self, field):
        hi, lo = self.fields[field]
        return hi - lo + 1

    def put(self, word, field, value):
        hi, lo = self.fields[field]
        mask = (1 << (hi - lo + 1)) - 1
        if not 0 <= value <= mask:
            raise ValueError(f"value {value} does not fit field {field} ({hi - lo + 1} bits)")
        return word | (value << lo)

    def get(self, word, field):
        hi, lo = self.fields[field]
        return (word >> lo) & ((1 << (hi - lo + 1)) - 1)


# -----------------------------------------------------------------------------
#  Expressions: integers, names, + - * ( ) and unary minus
# -----------------------------------------------------------------------------
_TOKEN = re.compile(r"\s*(?:(0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*)|([A-Za-z_]\w*)|(.))")


def evaluate(text, symbols, line):
    tokens = []
    for number, name, other in _TOKEN.findall(text.strip()):
        if number:
            tokens.append(("num", int(number.replace("_", ""), 0)))
        elif name:
            key = name.upper()
            if key not in symbols:
                raise AsmError(line, f"unknown name '{name}'")
            tokens.append(("num", symbols[key]))
        elif other.strip():
            if other not in "+-*()":
                raise AsmError(line, f"unexpected character '{other}' in expression '{text.strip()}'")
            tokens.append(("op", other))
    if not tokens:
        raise AsmError(line, "missing value")
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else (None, None)

    def take():
        nonlocal position
        position += 1
        return tokens[position - 1]

    def primary():
        kind, value = peek()
        if kind == "num":
            take()
            return value
        if value == "-":
            take()
            return -primary()
        if value == "+":
            take()
            return primary()
        if value == "(":
            take()
            result = expression()
            if peek()[1] != ")":
                raise AsmError(line, f"missing ')' in '{text.strip()}'")
            take()
            return result
        raise AsmError(line, f"bad expression '{text.strip()}'")

    def term():
        result = primary()
        while peek()[1] == "*":
            take()
            result *= primary()
        return result

    def expression():
        result = term()
        while peek()[1] in ("+", "-"):
            operator = take()[1]
            result = result + term() if operator == "+" else result - term()
        return result

    result = expression()
    if position != len(tokens):
        raise AsmError(line, f"bad expression '{text.strip()}'")
    return result


# -----------------------------------------------------------------------------
#  Assembler
# -----------------------------------------------------------------------------
def _strip_comment(text):
    for marker in (";", "#", "//"):
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    return text.strip()


def _split_operands(text):
    return [part.strip() for part in text.split(",")] if text.strip() else []


def _register(text, isa, line):
    match = re.fullmatch(r"[vV](\d+)", text.strip())
    if not match or int(match.group(1)) >= isa.num_vregs:
        raise AsmError(line, f"expected a vector register v0-v{isa.num_vregs - 1}, got '{text.strip()}'")
    return int(match.group(1))


def _address(text, isa, symbols, line):
    text = text.strip()
    match = re.fullmatch(r"\[(.*)\]", text)
    value = evaluate(match.group(1) if match else text, symbols, line)
    if not 0 <= value < isa.dmem_depth:
        raise AsmError(line, f"data address {value} is outside 0..{isa.dmem_depth - 1}")
    return value


class Program:
    """Result of assembling one source file."""

    def __init__(self, lanes, width):
        self.lanes = lanes
        self.width = width
        self.words = []         # instruction words, address = index
        self.source = []        # (line number, source text) per instruction
        self.data = {}          # data address -> LANES*WIDTH-bit word
        self.warnings = []

    def imem_text(self):
        return "".join(f"{word:04X}\n" for word in self.words)

    def dmem_text(self):
        if not self.data:
            return ""
        digits = (self.lanes * self.width + 3) // 4
        return "".join(f"{self.data.get(a, 0):0{digits}X}\n" for a in range(max(self.data) + 1))


def assemble(text, lanes=4, width=8, isa=None):
    """Assemble source text. Returns a Program; raises AsmError on the first error."""
    isa = isa or Isa()
    if lanes < 1 or width < 2 or width & (width - 1):
        raise ValueError("lanes must be >= 1 and width a power of two >= 2")
    program = Program(lanes, width)
    symbols = {}
    data_pointer = 0
    word_mask = (1 << (lanes * width)) - 1

    def store_data(line, value):
        nonlocal data_pointer
        if not 0 <= data_pointer < isa.dmem_depth:
            raise AsmError(line, f"data address {data_pointer} is outside 0..{isa.dmem_depth - 1}")
        if data_pointer in program.data:
            raise AsmError(line, f"data address {data_pointer} is written twice")
        program.data[data_pointer] = value
        data_pointer += 1

    for number, raw in enumerate(text.splitlines(), start=1):
        statement = _strip_comment(raw)
        if not statement:
            continue
        mnemonic, _, rest = statement.replace("\t", " ").partition(" ")
        mnemonic = mnemonic.upper()
        operands = _split_operands(rest)

        # ---- directives -----------------------------------------------------
        if mnemonic.startswith("."):
            if mnemonic == ".EQU":
                if len(operands) != 2 or not re.fullmatch(r"[A-Za-z_]\w*", operands[0]):
                    raise AsmError(number, ".equ needs a name and a value: .equ NAME, expr")
                key = operands[0].upper()
                if key in symbols:
                    raise AsmError(number, f"'{operands[0]}' is already defined")
                if re.fullmatch(r"[vV]\d+|[sS]", operands[0]):
                    raise AsmError(number, f"'{operands[0]}' is a register name")
                symbols[key] = evaluate(operands[1], symbols, number)
            elif mnemonic == ".ORG":
                if len(operands) != 1:
                    raise AsmError(number, ".org needs one address")
                data_pointer = _address(operands[0], isa, symbols, number)
            elif mnemonic == ".VEC":
                if len(operands) != lanes:
                    raise AsmError(number, f".vec needs exactly {lanes} elements (LANES={lanes}), got {len(operands)}")
                word = 0
                for lane, operand in enumerate(operands):
                    value = evaluate(operand, symbols, number)
                    if not -(1 << (width - 1)) <= value < (1 << width):
                        raise AsmError(number, f"element {value} does not fit in {width} bits")
                    word |= (value & ((1 << width) - 1)) << (lane * width)
                store_data(number, word)
            elif mnemonic == ".WORD":
                if len(operands) != 1:
                    raise AsmError(number, ".word needs one value")
                value = evaluate(operands[0], symbols, number)
                if not 0 <= value <= word_mask:
                    raise AsmError(number, f"{value:#x} does not fit in a {lanes * width}-bit data word")
                store_data(number, value)
            else:
                raise AsmError(number, f"unknown directive '{mnemonic.lower()}'")
            continue

        # ---- instructions ---------------------------------------------------
        if mnemonic not in isa.opcodes:
            raise AsmError(number, f"unknown instruction '{mnemonic}'")
        word = isa.put(0, "OP", isa.opcodes[mnemonic])

        def expect(count, usage):
            if len(operands) != count:
                raise AsmError(number, f"{mnemonic} takes {count} operand(s): {usage}")

        if mnemonic in ALU_OPS:
            expect(3, f"{mnemonic} vd, vs1, vs2|s")
            word = isa.put(word, "VD", _register(operands[0], isa, number))
            word = isa.put(word, "VS1", _register(operands[1], isa, number))
            if operands[2].strip().lower() == "s":
                word = isa.put(word, "S", 1)
            else:
                word = isa.put(word, "VS2", _register(operands[2], isa, number))
        elif mnemonic == "VBCAST":
            expect(1, "VBCAST vd")
            word = isa.put(word, "VD", _register(operands[0], isa, number))
        elif mnemonic in ("VLD", "VST"):
            expect(2, f"{mnemonic} vreg, [addr]")
            word = isa.put(word, "VREG", _register(operands[0], isa, number))
            word = isa.put(word, "ADDR", _address(operands[1], isa, symbols, number))
        elif mnemonic == "SLI":
            expect(1, "SLI imm8")
            value = evaluate(operands[0], symbols, number)
            if not -128 <= value <= 255:
                raise AsmError(number, f"SLI immediate {value} is outside -128..255")
            word = isa.put(word, "IMM8", value & 0xFF)
            if width < 8 and (value & 0xFF) >> width:
                program.warnings.append(f"line {number}: SLI {value} is truncated to {width} bits")
        else:  # NOP, HALT
            expect(0, mnemonic)

        if len(program.words) >= isa.imem_depth:
            raise AsmError(number, f"program is longer than the {isa.imem_depth}-entry instruction memory")
        program.words.append(word)
        program.source.append((number, raw.strip()))

    halt = isa.opcodes["HALT"]
    if not any(isa.get(w, "OP") == halt for w in program.words):
        if len(program.words) == isa.imem_depth:
            program.warnings.append("full instruction memory without HALT: the PC wraps and the program repeats")
        else:
            program.warnings.append("no HALT: the program stops at the first unused (HALT-filled) slot")
    return program


# -----------------------------------------------------------------------------
#  Disassembler
# -----------------------------------------------------------------------------
def disassemble(word, isa=None):
    isa = isa or Isa()
    name = isa.names[isa.get(word, "OP")]
    if name in ALU_OPS:
        scalar = isa.get(word, "S")
        b = "s" if scalar else f"v{isa.get(word, 'VS2')}"
        text = f"{name} v{isa.get(word, 'VD')}, v{isa.get(word, 'VS1')}, {b}"
        # With s = 1 operand B is the scalar, so the vs2 field has no effect.
        used = ("OP", "VD", "VS1", "S") if scalar else ("OP", "VD", "VS1", "VS2", "S")
    elif name == "VBCAST":
        text, used = f"VBCAST v{isa.get(word, 'VD')}", ("OP", "VD")
    elif name in ("VLD", "VST"):
        text = f"{name} v{isa.get(word, 'VREG')}, [{isa.get(word, 'ADDR')}]"
        used = ("OP", "VREG", "ADDR")
    elif name == "SLI":
        text, used = f"SLI {isa.get(word, 'IMM8'):#04x}", ("OP", "IMM8")
    else:
        text, used = name, ("OP",)
    covered = 0
    for field in used:
        hi, lo = isa.fields[field]
        covered |= ((1 << (hi - lo + 1)) - 1) << lo
    ignored = word & ~covered & ((1 << isa.instr_w) - 1)
    if ignored:
        text += f"    ; ignored bits {ignored:#06x}"
    return text


# -----------------------------------------------------------------------------
#  Command line
# -----------------------------------------------------------------------------
def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="LaneForge assembler / disassembler",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    parser.add_argument("source", help="assembly file (.s), or a .hex file with --disasm")
    parser.add_argument("-o", "--output", help="instruction hex file (default: SOURCE.hex)")
    parser.add_argument("-d", "--data", help="data hex file (default: SOURCE_data.hex, written only if the source has data)")
    parser.add_argument("--lanes", type=int, default=4, help="LANES the data words are packed for (default 4)")
    parser.add_argument("--width", type=int, default=8, help="WIDTH of each lane in bits (default 8)")
    parser.add_argument("--listing", action="store_true", help="print address, hex and source for each instruction")
    parser.add_argument("--disasm", action="store_true", help="disassemble a hex file instead")
    args = parser.parse_args(argv)
    isa = Isa()
    source = Path(args.source)

    if args.disasm:
        for address, line in enumerate(l for l in source.read_text().split() if l):
            word = int(line, 16)
            print(f"{address:3d}  {word:04X}  {disassemble(word, isa)}")
        return 0

    try:
        program = assemble(source.read_text(), args.lanes, args.width, isa)
    except AsmError as error:
        print(f"{source}:{error.line}: error: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for warning in program.warnings:
        print(f"{source}: warning: {warning}", file=sys.stderr)

    output = Path(args.output) if args.output else source.with_suffix(".hex")
    _write(output, program.imem_text())
    written = [str(output)]
    if program.data or args.data:
        data = Path(args.data) if args.data else source.with_name(source.stem + "_data.hex")
        _write(data, program.dmem_text())
        written.append(str(data))
    if args.listing:
        for address, (word, (line, text)) in enumerate(zip(program.words, program.source)):
            print(f"{address:3d}  {word:04X}  {text}")
    print(f"{source}: {len(program.words)} instructions, {len(program.data)} data words -> {', '.join(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
