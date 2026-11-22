#!/usr/bin/env python3
"""Tests for the ISA reference model (scripts/lfmodel.py) and the RTL runner.

Run:  make model_test     (or: python3 scripts/test_lfmodel.py)

Part A checks the model on its own against the documented ISA behaviour.
Part B runs directed programs through the RTL (tb/prog_runner.v via
scripts/lfrun.py) for all twelve LANES x WIDTH builds and requires the RTL and
the model to agree on the complete final state. Part B is skipped if Icarus
Verilog is not installed.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

import lfasm
import lfrun
from lfmodel import Machine

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = [(lanes, width) for width in (2, 4, 8, 16) for lanes in (1, 4, 8)]


def machine(source, data=None, lanes=4, width=8):
    program = lfasm.assemble(source, lanes, width)
    m = Machine(lanes, width, imem=program.words, dmem=data if data is not None else program.data)
    return m


def run(source, data=None, lanes=4, width=8, max_cycles=1000):
    m = machine(source, data, lanes, width)
    m.run(max_cycles)
    return m


class DocumentedPrograms(unittest.TestCase):
    def test_examples_from_the_programming_guide(self):
        cases = {"add": (2, 0x14131211, 5, 4, 0b0000),
                 "add_const": (1, 0x2D190F06, 5, 4, 0b0000),
                 "threshold": (1, 0x00010003, 6, 8, 0b0101)}
        for name, (address, word, cycles, lane_ops, mask) in cases.items():
            with self.subTest(program=name):
                m = run((ROOT / "programs" / f"{name}.s").read_text())
                self.assertEqual((m.dmem[address], m.cycle_count, m.instr_count, m.lane_ops, m.mask),
                                 (word, cycles, cycles, lane_ops, mask))


class LaneOperations(unittest.TestCase):
    def lanes_after(self, op, a, b, lanes=4, width=8):
        source = f"VLD v1, [0]\nVLD v2, [1]\n{op} v3, v1, v2\nVST v3, [2]\nHALT"
        m = Machine(lanes, width)
        m.load_imem(lfasm.assemble(source, lanes, width).words)
        m.load_dmem({0: m.join(a), 1: m.join(b)})
        m.run()
        return m.split(m.dmem[2]), m

    def test_wrapping_arithmetic(self):
        self.assertEqual(self.lanes_after("VADD", [0xFF, 0x7F, 1, 200], [1, 1, 2, 100])[0], [0, 0x80, 3, 44])
        self.assertEqual(self.lanes_after("VSUB", [0, 5, 0x80, 1], [1, 5, 1, 2])[0], [0xFF, 0, 0x7F, 0xFF])
        self.assertEqual(self.lanes_after("VMUL", [16, 15, 3, 0xFF], [16, 17, 5, 0xFF])[0], [0, 255, 15, 1])

    def test_logic(self):
        a, b = [0xF0, 0xAA, 0x0F, 0xFF], [0x3C, 0x55, 0x0F, 0x00]
        self.assertEqual(self.lanes_after("VAND", a, b)[0], [0x30, 0x00, 0x0F, 0x00])
        self.assertEqual(self.lanes_after("VOR", a, b)[0], [0xFC, 0xFF, 0x0F, 0xFF])
        self.assertEqual(self.lanes_after("VXOR", a, b)[0], [0xCC, 0xFF, 0x00, 0xFF])

    def test_signed_compare_and_mask(self):
        result, m = self.lanes_after("VCMPLT", [0xFF, 0x7F, 0x80, 5], [0x01, 0x80, 0x7F, 5])
        self.assertEqual(result, [0xFF, 0x00, 0xFF, 0x00])     # -1<1, 127<-128 no, -128<127, 5<5 no
        self.assertEqual(m.mask, 0b0101)
        result, m = self.lanes_after("VCMPEQ", [1, 2, 3, 0xFF], [1, 0, 3, 0x7F])
        self.assertEqual((result, m.mask), ([0xFF, 0, 0xFF, 0], 0b0101))

    def test_shifts_use_low_log2_width_bits(self):
        self.assertEqual(self.lanes_after("VSLL", [1, 1, 0x81, 3], [9, 7, 1, 8])[0], [2, 0x80, 0x02, 3])
        self.assertEqual(self.lanes_after("VSRL", [0x80, 0x80, 0xFF, 6], [7, 15, 4, 1])[0], [1, 1, 0x0F, 3])
        self.assertEqual(self.lanes_after("VSLL", [1, 1], [3, 5], lanes=2, width=4)[0], [8, 2])

    def test_wide_and_narrow_lanes(self):
        self.assertEqual(self.lanes_after("VADD", [0xFFFF, 1], [1, 0x7FFF], lanes=2, width=16)[0], [0, 0x8000])
        self.assertEqual(self.lanes_after("VCMPLT", [2, 1], [1, 2], lanes=2, width=2)[0], [3, 0])  # -2<1, 1<-2 no


class Instructions(unittest.TestCase):
    def test_scalar_paths(self):
        m = run("SLI 7\nVBCAST v1\nVADD v2, v1, s\nHALT")
        self.assertEqual((m.scalar, m.vregs[1], m.vregs[2]), (7, 0x07070707, 0x0E0E0E0E))

    def test_sli_extends_or_truncates(self):
        self.assertEqual(run("SLI 0xFF\nHALT", lanes=1, width=16).scalar, 0x00FF)
        self.assertEqual(run("SLI 0x1F\nHALT", lanes=1, width=4).scalar, 0xF)

    def test_store_uses_vreg_field(self):
        m = run("SLI 9\nVBCAST v5\nVST v5, [7]\nHALT")
        self.assertEqual(m.dmem[7], 0x09090909)

    def test_mask_changes_only_on_compares(self):
        m = run("SLI 1\nVBCAST v1\nVCMPEQ v2, v1, s\nVADD v3, v1, v1\nVLD v4, [0]\nHALT")
        self.assertEqual(m.mask, 0b1111)

    def test_lane_ops_counts_alu_and_compares_only(self):
        m = run("SLI 1\nVBCAST v1\nVADD v2, v1, v1\nVCMPLT v3, v1, s\nVLD v4, [0]\nVST v4, [1]\nNOP\nHALT", lanes=8)
        self.assertEqual((m.lane_ops, m.cycle_count), (16, 8))

    def test_sources_read_before_write(self):
        m = run("SLI 3\nVBCAST v1\nVADD v1, v1, v1\nVADD v1, v1, v1\nHALT")
        self.assertEqual(m.vregs[1], 0x0C0C0C0C)

    def test_halt_freezes_everything(self):
        m = run("SLI 1\nHALT\nSLI 2")
        frozen = m.state()
        for _ in range(10):
            m.step()
        self.assertEqual(m.state(), frozen)
        self.assertEqual((m.pc, m.cycle_count, m.scalar), (1, 2, 1))

    def test_pc_wraps_without_halt(self):
        m = run("NOP\n" * 256, max_cycles=300)
        self.assertEqual((m.halted, m.pc, m.cycle_count), (False, 300 % 256, 300))

    def test_unused_slots_are_halt(self):
        m = run("NOP\nNOP")
        self.assertEqual((m.halted, m.pc, m.cycle_count), (True, 2, 3))

    def test_reserved_bits_ignored(self):
        m = Machine(4, 8, imem=[0xB005 | 0x0F00, 0xE200, 0x164B, 0xF000])  # SLI with bits 11:8; VADD v3,v1,v1 with bits 1:0
        m.run()
        self.assertEqual((m.scalar, m.vregs[3]), (5, 0x0A0A0A0A))

    def test_reset_keeps_memories(self):
        m = run("SLI 4\nVBCAST v1\nVST v1, [3]\nHALT")
        m.reset()
        self.assertEqual((m.dmem[3], m.vregs[1], m.scalar, m.pc, m.halted), (0x04040404, 0, 0, 0, False))
        self.assertEqual(m.imem[0], 0xB004)


def directed_program(lanes, width):
    """Every opcode, the scalar path, edge values, store-then-load, then HALT."""
    full = (1 << width) - 1
    top = 1 << (width - 1)
    pick = [0, 1, full, top, top - 1, 3 & full, (0x5A5A >> (16 - width)) & full, 2 & full]
    a = [pick[i % len(pick)] for i in range(lanes)]
    b = [pick[(i * 3 + 1) % len(pick)] for i in range(lanes)]
    source = f"""
        .vec {', '.join(map(str, a))}
        .vec {', '.join(map(str, b))}
        VLD v1, [0]
        VLD v2, [1]
        VADD v3, v1, v2
        VSUB v4, v1, v2
        VAND v5, v3, v4
        VOR  v6, v1, v5
        VXOR v7, v6, v2
        VCMPEQ v0, v1, v1
        VCMPLT v3, v1, v2
        VSLL v4, v2, v1
        VSRL v5, v2, v1
        VMUL v6, v1, v2
        NOP
        SLI 0x83
        VBCAST v7
        VADD v1, v1, s
        VCMPLT v2, v2, s
        VSLL v0, v1, s
        VMUL v1, v1, s
        VST v3, [10]
        VST v4, [511]
        VLD v2, [10]
        VSUB v2, v2, v6
        VST v2, [12]
        HALT
        VADD v1, v1, v1
    """
    return lfasm.assemble(source, lanes, width)


@unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"), "Icarus Verilog not installed")
class RtlAgreesWithModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="laneforge-model-test-")
        cls.work = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_configurations(self):
        for lanes, width in CONFIGS:
            with self.subTest(lanes=lanes, width=width):
                work = self.work / f"L{lanes}_W{width}"
                binary, _ = lfrun.compile_runner(lanes, width, work)
                directed = directed_program(lanes, width)
                wrap = lfasm.assemble("SLI 1\nVBCAST v1\n" + "VADD v2, v2, v1\n" * 254, lanes, width)
                programs = [(directed.words, directed.data),
                            (wrap.words, {}),                        # full image, no HALT
                            ([0xF000], {}),                          # HALT first
                            ([], {5: (1 << (lanes * width)) - 1})]   # all-HALT memory, data kept
                states = lfrun.run_rtl(binary, programs, lanes, width, work, max_cycles=300)
                for index, ((words, data), rtl) in enumerate(zip(programs, states)):
                    model = lfrun.run_model(words, data, lanes, width, max_cycles=300)
                    self.assertEqual(lfrun.compare(rtl, model), [], f"program {index}")
                self.assertEqual(states[1]["halted"], 0)
                self.assertEqual(states[1]["cycle_count"], 300)


if __name__ == "__main__":
    unittest.main(verbosity=1)
