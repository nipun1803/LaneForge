#!/usr/bin/env python3
"""Self-checking tests for the LaneForge assembler (scripts/lfasm.py).

Run:  make asm_test      (or: python3 scripts/test_lfasm.py)

* Every encoding example in docs/instruction_set.md assembles to the hex the
  document gives, so the document, the assembler and simd_defs.vh agree.
* All 65,536 instruction words survive disassemble -> assemble unchanged
  (apart from reserved bits, which the hardware ignores).
* The example programs in programs/*.s assemble to the checked-in .hex files.
* Data packing, expressions and every error message are exercised.
"""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import re
import tempfile
import unittest

import lfasm

ROOT = Path(__file__).resolve().parents[1]
ISA = lfasm.Isa()


def asm(text, lanes=4, width=8):
    return lfasm.assemble(text, lanes, width, ISA)


class DocumentExamples(unittest.TestCase):
    def test_encoding_table(self):
        rows = re.findall(r"^\| `([^`]+)` \| `[01 ]+` \| `([0-9A-F]{4})` \|$",
                          (ROOT / "docs" / "instruction_set.md").read_text(), re.MULTILINE)
        self.assertGreaterEqual(len(rows), 8, "encoding table not found in instruction_set.md")
        for source, expected in rows:
            with self.subTest(source=source):
                self.assertEqual(asm(source).words, [int(expected, 16)])


class Exhaustive(unittest.TestCase):
    def test_all_words_round_trip(self):
        checked = 0
        for word in range(1 << ISA.instr_w):
            text = lfasm.disassemble(word, ISA)
            clean = text.split(";")[0]
            ignored = int(text.split("ignored bits")[1], 16) if "ignored bits" in text else 0
            self.assertEqual(asm(clean).words, [word & ~ignored], f"{word:04X} -> {text}")
            checked += 1
        self.assertEqual(checked, 65536)

    def test_every_opcode_has_a_mnemonic(self):
        self.assertEqual(sorted(ISA.names), list(range(16)))


class ExamplePrograms(unittest.TestCase):
    def test_programs_match_checked_in_hex(self):
        sources = sorted((ROOT / "programs").glob("*.s"))
        self.assertGreaterEqual(len(sources), 3)
        for source in sources:
            with self.subTest(program=source.name):
                program = asm(source.read_text())
                self.assertEqual(program.imem_text(), source.with_suffix(".hex").read_text())
                data = source.with_name(source.stem + "_data.hex")
                self.assertEqual(program.dmem_text(), data.read_text() if data.exists() else "")
                self.assertEqual(program.warnings, [])


class Syntax(unittest.TestCase):
    def test_scalar_operand_and_case(self):
        self.assertEqual(asm("vadd V3, v1, S").words, [0x1644])

    def test_comment_styles_and_blank_lines(self):
        program = asm("; a\n# b\n// c\n\n  NOP ; d\n\tHALT\t# e\n")
        self.assertEqual(program.words, [0x0000, 0xF000])

    def test_expressions_and_equ(self):
        program = asm(".equ BASE, 0x10\n.equ STEP, 2*3\nVLD v1, [BASE + STEP - 1]\n"
                      "VST v1, BASE*(1+1)\nSLI -(1)\nSLI 0b1010\nHALT")
        self.assertEqual(program.words, [0xC215, 0xD220, 0xB0FF, 0xB00A, 0xF000])

    def test_vec_packing_lane0_first(self):
        program = asm(".org 3\n.vec 1, 2, 3, -1\n.word 0xDEADBEEF\nHALT")
        self.assertEqual(program.data, {3: 0xFF030201, 4: 0xDEADBEEF})
        self.assertEqual(program.dmem_text(), "00000000\n" * 3 + "FF030201\nDEADBEEF\n")

    def test_other_configurations(self):
        program = asm(".vec 1, 2, 3, 4, 5, 6, 7, -2\nHALT", lanes=8, width=16)
        self.assertEqual(program.data[0], 0xFFFE_0007_0006_0005_0004_0003_0002_0001)
        self.assertEqual(program.dmem_text(), "FFFE000700060005000400030002000" "1\n")
        self.assertEqual(asm(".vec 9\nHALT", lanes=1, width=8).dmem_text(), "09\n")
        self.assertEqual(asm(".vec 3, 1\nHALT", lanes=2, width=2).dmem_text(), "7\n")

    def test_full_memory_without_halt_warns(self):
        program = asm("NOP\n" * 256)
        self.assertEqual(len(program.words), 256)
        self.assertIn("wraps", program.warnings[0])

    def test_missing_halt_warns(self):
        self.assertIn("no HALT", asm("NOP").warnings[0])

    def test_narrow_sli_warns(self):
        self.assertIn("truncated", asm("SLI 9\nHALT", lanes=4, width=2).warnings[0])


class Errors(unittest.TestCase):
    CASES = [
        ("FOO v1, v2, v3", 1, "unknown instruction"),
        ("VADD v8, v1, v2", 1, "vector register"),
        ("VADD v1, v2", 1, "takes 3 operand"),
        ("VADD v1, v2, 5", 1, "vector register"),
        ("VBCAST s", 1, "vector register"),
        ("HALT v1", 1, "takes 0 operand"),
        ("VLD v1, [512]", 1, "outside 0..511"),
        ("VST v1, [-1]", 1, "outside 0..511"),
        ("SLI 256", 1, "outside -128..255"),
        ("SLI -129", 1, "outside -128..255"),
        ("SLI LIMIT", 1, "unknown name"),
        ("SLI 3 +", 1, "bad expression"),
        ("SLI (3", 1, "missing ')'"),
        ("SLI 3 / 1", 1, "unexpected character"),
        ("NOP\n.vec 1, 2, 3", 2, "exactly 4 elements"),
        (".vec 1, 2, 3, 256", 1, "does not fit"),
        (".vec 1, 2, 3, -129", 1, "does not fit"),
        (".word 0x100000000", 1, "does not fit"),
        (".org 0\n.word 1\n.org 0\n.word 2", 4, "written twice"),
        (".org 511\n.word 1\n.word 2", 3, "outside 0..511"),
        (".equ X, 1\n.equ x, 2", 2, "already defined"),
        (".equ v1, 2", 1, "register name"),
        (".equ X", 1, ".equ needs"),
        (".bogus 1", 1, "unknown directive"),
        ("NOP\n" * 256 + "HALT", 257, "longer than the 256-entry"),
    ]

    def test_errors(self):
        for source, line, message in self.CASES:
            with self.subTest(source=source[:40]):
                with self.assertRaises(lfasm.AsmError) as caught:
                    asm(source)
                self.assertEqual(caught.exception.line, line)
                self.assertIn(message, str(caught.exception))

    def test_bad_configuration(self):
        with self.assertRaises(ValueError):
            asm("HALT", lanes=4, width=6)


class CommandLine(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = lfasm.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_files_listing_and_disasm(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "p.s"
            source.write_text("VLD v1, [0]\nHALT\n.vec 1, 2, 3, 4\n")
            code, out, _ = self.run_cli(str(source), "--listing")
            self.assertEqual(code, 0)
            self.assertIn("C200  VLD v1, [0]", out)
            self.assertEqual((Path(tmp) / "p.hex").read_text(), "C200\nF000\n")
            self.assertEqual((Path(tmp) / "p_data.hex").read_text(), "04030201\n")
            code, out, _ = self.run_cli(str(Path(tmp) / "p.hex"), "--disasm")
            self.assertEqual(code, 0)
            self.assertIn("1  F000  HALT", out)

    def test_error_reports_file_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad.s"
            source.write_text("NOP\nVADD v9, v1, v2\n")
            code, _, err = self.run_cli(str(source))
            self.assertEqual(code, 1)
            self.assertIn("bad.s:2: error:", err)
            self.assertFalse((Path(tmp) / "bad.hex").exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
