#!/usr/bin/env python3
"""Instruction-set reference model of the LaneForge SIMD processor.

Written from the ISA specification (docs/instruction_set.md and
docs/top_level_processor.md), not from the RTL or from the Verilog model inside
tb/tb_simd_top.v, so it is an independent second opinion. Field positions are
deliberately hard-coded here instead of read from rtl/simd_defs.vh: a mistake
in the header would then show up as a mismatch instead of being copied.

Architectural behaviour modelled:
  * 8 vector registers of LANES x WIDTH bits (lane i = bits [i*W+W-1 : i*W]),
    one WIDTH-bit scalar, a LANES-bit compare mask, 256 x 16-bit instruction
    memory, 512-word data memory, 8-bit PC, cycle / instruction / lane-op counters.
  * One instruction per cycle. Sources are read before the destination is written.
  * Arithmetic wraps mod 2^W; VCMPLT is signed; VCMPEQ / VCMPLT write all-ones or
    all-zeros per lane and are the only instructions that update the mask.
  * Shifts use the low log2(W) bits of operand B; VMUL keeps the low W bits.
  * Operand B is the broadcast scalar when the s bit (bit 2) is set.
  * SLI zero-extends imm8 for W > 8 and keeps its low W bits for W < 8.
  * lane_ops grows by LANES for each ALU or compare instruction (opcodes 1-10).
  * HALT is counted, then the PC stays on it and all state and counters freeze.
  * The PC wraps from 255 to 0. Reset clears registers, scalar, mask, PC, HALT and
    counters and keeps both memories.
Only the Python standard library is used.
"""

IMEM_DEPTH = 256
DMEM_DEPTH = 512
NUM_VREGS = 8
HALT_WORD = 0xF000

# Opcodes (instr[15:12]), from docs/instruction_set.md
NOP, VADD, VSUB, VAND, VOR, VXOR, VCMPEQ, VCMPLT = range(8)
VSLL, VSRL, VMUL, SLI, VLD, VST, VBCAST, HALT = range(8, 16)
ALU_OPCODES = range(VADD, VMUL + 1)


class Machine:
    def __init__(self, lanes=4, width=8, imem=None, dmem=None):
        if lanes < 1 or width < 2 or width & (width - 1):
            raise ValueError("lanes must be >= 1 and width a power of two >= 2")
        self.lanes, self.width = lanes, width
        self.lane_mask = (1 << width) - 1
        self.imem = [HALT_WORD] * IMEM_DEPTH
        self.dmem = [0] * DMEM_DEPTH
        if imem:
            self.load_imem(imem)
        if dmem:
            self.load_dmem(dmem)
        self.reset()

    # ---- loading -------------------------------------------------------------
    def load_imem(self, words):
        if len(words) > IMEM_DEPTH:
            raise ValueError("program longer than instruction memory")
        self.imem = [HALT_WORD] * IMEM_DEPTH
        self.imem[:len(words)] = [w & 0xFFFF for w in words]

    def load_dmem(self, data):
        """data: list of words from address 0, or {address: word}."""
        items = data.items() if isinstance(data, dict) else enumerate(data)
        self.dmem = [0] * DMEM_DEPTH
        full = (1 << (self.lanes * self.width)) - 1
        for address, word in items:
            self.dmem[address] = word & full

    def reset(self):
        self.vregs = [0] * NUM_VREGS
        self.scalar = 0
        self.mask = 0
        self.pc = 0
        self.halted = False
        self.cycle_count = self.instr_count = self.lane_ops = 0

    # ---- lane helpers ----------------------------------------------------------
    def split(self, vector):
        return [(vector >> (i * self.width)) & self.lane_mask for i in range(self.lanes)]

    def join(self, lanes):
        word = 0
        for i, value in enumerate(lanes):
            word |= (value & self.lane_mask) << (i * self.width)
        return word

    def signed(self, value):
        return value - (1 << self.width) if value >> (self.width - 1) else value

    def lane_op(self, op, a, b):
        """Return (result, compare_flag) for one lane."""
        m = self.lane_mask
        if op == VADD:
            return (a + b) & m, False
        if op == VSUB:
            return (a - b) & m, False
        if op == VAND:
            return a & b, False
        if op == VOR:
            return a | b, False
        if op == VXOR:
            return a ^ b, False
        if op == VCMPEQ:
            flag = a == b
            return (m if flag else 0), flag
        if op == VCMPLT:
            flag = self.signed(a) < self.signed(b)
            return (m if flag else 0), flag
        if op == VSLL:
            return (a << (b % self.width)) & m, False
        if op == VSRL:
            return a >> (b % self.width), False
        if op == VMUL:
            return (a * b) & m, False
        raise ValueError(f"not an ALU opcode: {op}")

    # ---- execution -------------------------------------------------------------
    def step(self):
        if self.halted:
            return
        instr = self.imem[self.pc]
        op = (instr >> 12) & 0xF
        vd = (instr >> 9) & 0x7          # also vreg for VLD / VST
        vs1 = (instr >> 6) & 0x7
        vs2 = (instr >> 3) & 0x7
        use_scalar = (instr >> 2) & 0x1
        addr = instr & 0x1FF
        imm8 = instr & 0xFF

        if op in ALU_OPCODES:
            a = self.split(self.vregs[vs1])
            b = [self.scalar] * self.lanes if use_scalar else self.split(self.vregs[vs2])
            results, flags = zip(*(self.lane_op(op, x, y) for x, y in zip(a, b)))
            self.vregs[vd] = self.join(results)
            if op in (VCMPEQ, VCMPLT):
                self.mask = sum(1 << i for i, f in enumerate(flags) if f)
            self.lane_ops += self.lanes
        elif op == SLI:
            self.scalar = imm8 & self.lane_mask
        elif op == VBCAST:
            self.vregs[vd] = self.join([self.scalar] * self.lanes)
        elif op == VLD:
            self.vregs[vd] = self.dmem[addr]
        elif op == VST:
            self.dmem[addr] = self.vregs[vd]
        # NOP and HALT change no registers

        self.cycle_count += 1
        self.instr_count += 1
        if op == HALT:
            self.halted = True                       # PC stays on the HALT
        else:
            self.pc = (self.pc + 1) % IMEM_DEPTH

    def run(self, max_cycles=1000):
        """Run until HALT or max_cycles steps; returns the number of steps taken."""
        steps = 0
        while not self.halted and steps < max_cycles:
            self.step()
            steps += 1
        return steps

    def state(self):
        """Final architectural state, in the same form lfrun parses from the RTL."""
        return {
            "cycle_count": self.cycle_count,
            "instr_count": self.instr_count,
            "lane_ops": self.lane_ops,
            "mask": self.mask,
            "scalar": self.scalar,
            "pc": self.pc,
            "halted": int(self.halted),
            "vregs": list(self.vregs),
            "dmem": {a: w for a, w in enumerate(self.dmem) if w},
        }
