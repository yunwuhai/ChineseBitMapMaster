#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 C 测试用的合成字库（不含任何真实字体数据，可安全提交）。

用法：python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

_SPEC = importlib.util.spec_from_file_location("cbfont", ROOT / "tools" / "cbfont.py")
cbfont = importlib.util.module_from_spec(_SPEC)
sys.modules["cbfont"] = cbfont
_SPEC.loader.exec_module(cbfont)


def synthetic_chunks(count: int, bpg: int = 32) -> bytes:
    out = bytearray()
    for i in range(count):
        for j in range(bpg):
            out.append((i * 7 + j * 3) & 0xFF)
    return bytes(out)


def make_dense() -> bytes:
    ranges = [
        cbfont.Range(0xA1, 0xA2, 0xA1, 0xA2, 0),   # 4 槽
        cbfont.Range(0xB0, 0xB1, 0xA1, 0xA3, 4),   # 6 槽
        cbfont.Range(0, 0, 0x41, 0x43, 10),        # 单字节 A-C
    ]
    keys: list[int] = []
    for r in ranges:
        if r.single_byte:
            keys.extend(range(r.lo_min, r.lo_max + 1))
        else:
            for hi in range(r.hi_min, r.hi_max + 1):
                for lo in range(r.lo_min, r.lo_max + 1):
                    keys.append((hi << 8) | lo)
    chars = [cbfont.decode_key(cbfont.ENC_GB2312, key) for key in keys]
    plan = cbfont.BuildPlan("fixture-dense", cbfont.ENC_GB2312, ranges, keys, chars, False)
    return cbfont.assemble_font(plan, synthetic_chunks(len(keys)), 16, 0)


def make_indexed() -> bytes:
    plan = cbfont.plan_unicode([0x20, 0x41, 0x4E00, 0x4E8C, 0x1F600], "fixture-indexed")
    return cbfont.assemble_font(plan, synthetic_chunks(len(plan.keys)), 16, 0)


def make_dense_utf8() -> bytes:
    plan = cbfont.plan_bmp_dense(
        cbfont.plan_unicode([0x41, 0x42, 0x4E00, 0x4E01, 0x4E02], "fixture-dense-utf8")
    )
    assert plan is not None
    return cbfont.assemble_font(plan, synthetic_chunks(len(plan.keys)), 16, 0)


def main() -> int:
    (HERE / "dense.cbm").write_bytes(make_dense())
    (HERE / "indexed.cbm").write_bytes(make_indexed())
    (HERE / "dense_utf8.cbm").write_bytes(make_dense_utf8())
    print(f"wrote {HERE / 'dense.cbm'}")
    print(f"wrote {HERE / 'indexed.cbm'}")
    print(f"wrote {HERE / 'dense_utf8.cbm'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
