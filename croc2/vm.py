"""Follow an actor's entry code far enough to see which model it sets.

Actor payloads are bytecode for a stack machine in ``Croc2.exe``. Each opcode is a
32-bit word followed by its operands, and is dispatched through a table of handler
pointers at 0x4af340, one per opcode. Ghidra never turned the handlers into functions
because nothing calls them directly, so they are absent from ``decompiled.c``; the
meanings below come from disassembling them. The VM keeps its pc at +0xc, its stack
pointer at +0xf8 and its local variables through +0xe4.

Only the opcodes an entry passes through on the way to setting its model are handled.
Any other opcode ends the walk, because without its operand count the rest of the code
cannot even be split into instructions.

Numbers are 20.12 fixed point. ``RAND`` makes the walk probabilistic: a value is kept
as a distribution, and a ``SWITCH`` on it forks the walk with each branch's share.
"""
from __future__ import annotations

import struct
from collections import defaultdict

PUSH = 10          # n: push n
PUSH_RAW = 180     # n: push n (same handler as PUSH)
PUSH_STPC = 124    # off: push a pointer to STPC + off, then rewrite itself to PUSH
LOCAL_ADDR = 5     # n: push the address of local n
LOCAL = 1          # n: push local n
CODE_WORD = 2      # n: push the code word n words past the operand
CODE_ADDR = 6      # n: push the address of that word
PROPERTY = 4       # n: push property n of the actor (FUN_00485010)
STORE = 18         # pop value, pop address, store
ADD = 14
FLOOR = 118        # clear the 12 fraction bits
RAND = 152         # pop n, push a random number below n (MSVC rand, 0x343fd/0x269ec3)
SWITCH = 137       # off: pop a value, jump through the case table off words on
JUMP = 132         # n: skip n words
SET_MODEL = 22     # pop a model pointer into the actor's model (+0x10)
STATE = 176        # two operands; the second sets a code pointer (+0x18)
FLAGS = (111, 161, 177, 226)  # one operand, sets actor flags

MAX_STEPS = 400
MAX_RAND = 1 << 20


class Stuck(Exception):
    pass


def entry_models(code: bytes, entry: int, models: dict[int, int]) -> dict[int, float]:
    """The models an entry sets, with the chance of each.

    ``code`` is an actor payload, ``entry`` a byte offset into it, and ``models`` maps
    model header offsets to model indices. Every branch has to reach ``SET_MODEL``
    with a model on the stack; if any branch cannot be followed, the result is empty.
    """
    words = struct.unpack(f"<{len(code) // 4}i", code[:len(code) // 4 * 4])
    out: dict[int, float] = defaultdict(float)
    todo = [(entry // 4, [], {}, 1.0)]
    try:
        while todo:
            pc, stack, local, share = todo.pop()
            model, forks = _walk(words, pc, stack, local)
            if forks is None:
                if model not in models:
                    raise Stuck(f"model pointer {model}")
                out[models[model]] += share
            else:
                todo.extend((p, list(stack), dict(local), share * s) for p, s in forks)
    except (Stuck, IndexError):
        return {}
    return dict(out)


def _walk(words, pc, stack, local):
    """Run one branch until it sets a model or forks. Returns ``(model, forks)``."""
    for _ in range(MAX_STEPS):
        op = words[pc]
        arg = words[pc + 1] if pc + 1 < len(words) else None
        if op == STATE:
            pc += 3
        elif op in (PUSH, PUSH_RAW):
            stack.append({arg: 1.0})
            pc += 2
        elif op == PUSH_STPC:
            stack.append(("stpc", arg) if arg >= 0 else None)
            pc += 2
        elif op == LOCAL_ADDR:
            stack.append(("local", arg))
            pc += 2
        elif op == LOCAL:
            stack.append(local.get(arg))
            pc += 2
        elif op == CODE_WORD:
            stack.append({words[pc + 2 + arg]: 1.0})
            pc += 2
        elif op == CODE_ADDR:
            stack.append(("code", pc + 2 + arg))
            pc += 2
        elif op == PROPERTY:
            stack.append(None)
            pc += 2
        elif op in FLAGS:
            pc += 2
        elif op == STORE:
            value, address = stack.pop(), stack.pop()
            if isinstance(address, tuple) and address[0] == "local":
                local[address[1]] = value
            pc += 1
        elif op == ADD:
            b, a = _number(stack.pop()), _number(stack.pop())
            stack.append(_combine(a, b, lambda x, y: x + y))
            pc += 1
        elif op == FLOOR:
            a = _number(stack.pop())
            stack.append(_combine(a, {0: 1.0}, lambda x, _: x & ~0xFFF))
            pc += 1
        elif op == RAND:
            a = _number(stack.pop())
            if len(a) != 1 or not 0 < next(iter(a)) <= MAX_RAND:
                raise Stuck("rand of a non-constant")
            n = next(iter(a))
            stack.append({x: 1.0 / n for x in range(n)})
            pc += 1
        elif op == SWITCH:
            return None, _switch(words, pc, _number(stack.pop()))
        elif op == JUMP:
            pc += 2 + arg
        elif op == SET_MODEL:
            top = stack.pop() if stack else None
            if not (isinstance(top, tuple) and top[0] == "stpc"):
                raise Stuck("set model from a non-pointer")
            return top[1], None
        else:
            raise Stuck(f"opcode {op}")
    raise Stuck("too many steps")


def _switch(words, pc, value):
    """Case table: a count, then ``(10, value, target)`` triples; no match falls through."""
    after = pc + 2
    table = after + words[pc + 1] + 1
    cases = [words[table + 1 + 3 * k: table + 4 + 3 * k] for k in range(words[table])]
    if any(kind != PUSH for kind, _, _ in cases):
        raise Stuck("case of an unknown kind")
    targets = {v: after + t for _, v, t in cases}
    forks: dict[int, float] = defaultdict(float)
    for v, share in value.items():
        forks[targets.get(v, after)] += share
    return list(forks.items())


def _number(value):
    if not isinstance(value, dict):
        raise Stuck("arithmetic on a non-number")
    return value


def _combine(a, b, f):
    out: dict[int, float] = defaultdict(float)
    for x, px in a.items():
        for y, py in b.items():
            out[f(x, y)] += px * py
    return dict(out)
