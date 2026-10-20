# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""cbfont.py 的单元测试。

默认不依赖真实字体：字符集规划、打包、文件格式与查找都用合成数据验证。
标记为 ``font`` 的用例需要本机存在中文字体，CI 上会自动跳过。
"""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("cbfont", ROOT / "tools" / "cbfont.py")
cbfont = importlib.util.module_from_spec(_SPEC)
sys.modules["cbfont"] = cbfont
_SPEC.loader.exec_module(cbfont)


def _available_font():
    for candidate in cbfont.FONT_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


FONT = _available_font()
requires_font = pytest.mark.skipif(FONT is None, reason="本机没有可用的中文字体")


def synthetic_chunks(count, bpg=32):
    return bytes((i * 7 + j * 3) & 0xFF for i in range(count) for j in range(bpg))


def build_synthetic(plan):
    return cbfont.assemble_font(plan, synthetic_chunks(len(plan.keys)), 16, 0)


def _image(points, size=16):
    img = Image.new("L", (size, size), 0)
    for x, y in points:
        img.putpixel((x, y), 255)
    return img


class TestCharsets:
    def test_gb2312(self):
        plan = cbfont.plan_charset("gb2312")
        assert len(plan.keys) == 846 + 6768 + 95
        assert len(plan.ranges) == 3
        assert plan.ranges[0].glyph_offset == 0
        assert plan.ranges[1].glyph_offset == 846
        assert plan.ranges[2].glyph_offset == 7614
        assert plan.chars[0] == "\u3000"
        assert cbfont.decode_key(cbfont.ENC_GB2312, 0xB0A1) == "啊"

    def test_gb2312_without_ascii(self):
        assert len(cbfont.plan_charset("gb2312", include_ascii=False).keys) == 846 + 6768

    def test_unassigned_codes_are_none(self):
        plan = cbfont.plan_charset("gb2312")
        index = plan.keys.index(0xD7FA)
        assert plan.chars[index] is None

    def test_single_byte_charsets(self):
        latin = cbfont.plan_charset("latin-1")
        assert len(latin.keys) == 224
        assert latin.ranges[0].single_byte
        assert cbfont.plan_charset("ascii").keys == list(range(0x20, 0x7F))

    def test_unicode_range(self):
        plan = cbfont.plan_charset("range:U+0041-U+0043")
        assert plan.keys == [0x41, 0x42, 0x43]
        assert plan.indexed
        assert plan.encoding == cbfont.ENC_UTF8

    def test_language(self):
        plan = cbfont.plan_charset("lang:ru")
        cyrillic = [key for key in plan.keys if 0x0400 <= key <= 0x04FF]
        assert len(cyrillic) == 256
        assert 0x41 in plan.keys  # 语言预设附带 ASCII，方便混排

    def test_text_file(self, tmp_path):
        path = tmp_path / "words.txt"
        path.write_text("abc你好", encoding="utf-8")
        plan = cbfont.plan_charset(f"text:{path}")
        assert plan.keys == sorted({ord(ch) for ch in "abc你好"})

    def test_unknown_charset(self):
        with pytest.raises(ValueError):
            cbfont.plan_charset("nope")


class TestLayout:
    def test_bmp_dense_plan(self):
        plan = cbfont.plan_unicode([0x41, 0x42, 0x4E00, 0x4E01, 0x4E02], "t")
        dense = cbfont.plan_bmp_dense(plan)

        assert dense is not None
        assert not dense.indexed
        assert dense.keys == [0x41, 0x42, 0x4E00, 0x4E01, 0x4E02]
        assert len(dense.ranges) == 2
        assert dense.ranges[1].glyph_offset == 2
        assert dense.chars[-1] == "\u4E02"

    def test_auto_prefers_dense_for_full_cjk(self):
        plan = cbfont.plan_unicode(range(0x4E00, 0xA000), "cjk")
        chosen = cbfont.choose_layout(plan, "auto", 16)

        assert not chosen.indexed
        assert cbfont.estimate_font_size(chosen, 16) <= cbfont.estimate_font_size(plan, 16)

    def test_auto_keeps_indexed_for_sparse(self):
        plan = cbfont.plan_unicode([0x41, 0x1000, 0x2000, 0x3000], "sparse")
        assert cbfont.choose_layout(plan, "auto", 16).indexed

    def test_dense_rejects_non_bmp(self):
        plan = cbfont.plan_unicode([0x1F600], "emoji")
        assert cbfont.plan_bmp_dense(plan) is None
        with pytest.raises(ValueError):
            cbfont.choose_layout(plan, "dense", 16)

    def test_dense_rejects_too_many_ranges(self):
        points = [0x0100, 0x0300, 0x0500, 0x0700, 0x0901, 0x0B01, 0x0D01, 0x0F00, 0x1100]
        plan = cbfont.plan_unicode(points, "many")
        assert cbfont.plan_bmp_dense(plan) is None
        with pytest.raises(ValueError):
            cbfont.choose_layout(plan, "dense", 16)

    def test_dbcs_layout(self):
        plan = cbfont.plan_charset("gb2312")
        assert cbfont.choose_layout(plan, "auto", 16) is plan
        with pytest.raises(ValueError):
            cbfont.choose_layout(plan, "indexed", 16)

    def test_build_rejects_indexed_for_dbcs(self):
        with pytest.raises(ValueError, match="DBCS"):
            cbfont.build_font(font_path=Path("missing.ttf"), charset="gb2312", layout="indexed")

    def test_build_rejects_dense_non_bmp(self):
        with pytest.raises(ValueError, match="BMP"):
            cbfont.build_font(font_path=Path("missing.ttf"),
                              charset="range:U+1F600", layout="dense")

    def test_build_rejects_bad_layout(self):
        with pytest.raises(ValueError, match="未知布局"):
            cbfont.build_font(font_path=Path("missing.ttf"), charset="gb2312", layout="nope")


class TestPacking:
    def test_msb_first(self):
        data = cbfont.pack_image(_image([(0, 0), (7, 0), (8, 0)]))
        assert len(data) == 32
        assert data[0] == 0b10000001
        assert data[1] == 0b10000000

    def test_lsb_first(self):
        data = cbfont.pack_image(_image([(0, 0), (7, 0)]), lsb_first=True)
        assert data[0] == 0b10000001
        assert data[1] == 0

    def test_row_major(self):
        data = cbfont.pack_image(_image([(0, 1)]))
        assert data[0] == 0
        assert data[cbfont.bytes_per_row(16)] == 0x80

    def test_invert_and_threshold(self):
        blank = Image.new("L", (16, 16), 0)
        white = Image.new("L", (16, 16), 255)
        assert cbfont.pack_image(blank, invert=True) == b"\xff" * 32
        assert cbfont.pack_image(white, invert=True) == b"\x00" * 32
        assert cbfont.pack_image(Image.new("L", (16, 16), 100), threshold=128) == b"\x00" * 32
        assert cbfont.pack_image(Image.new("L", (16, 16), 100), threshold=100) == b"\xff" * 32

    def test_bytes_per_glyph(self):
        assert cbfont.bytes_per_glyph(16, 16) == 32
        assert cbfont.bytes_per_glyph(12, 12) == 24
        assert cbfont.bytes_per_glyph(24, 24) == 72
        assert cbfont.bytes_per_glyph(32, 32) == 128


class TestFormat:
    def test_dense_roundtrip(self):
        plan = cbfont.plan_charset("gb2312", include_ascii=False)
        blob = build_synthetic(plan)
        font = cbfont.FontFile(blob)

        assert font.version == 2
        assert font.encoding == cbfont.ENC_GB2312
        assert not font.indexed
        assert font.glyph_count == 7614
        assert font.bytes_per_glyph == 32
        assert font.verify_crc()

        assert font.lookup_key(0xA1A1) == 0
        assert font.lookup_key(0xB0A1) == 846
        assert font.lookup_key(0xC0A1) == 846 + (16 * 94)
        assert font.lookup_key(0x41) is None  # 没有 ASCII 区间

    def test_ascii_lookup(self):
        plan = cbfont.plan_charset("gb2312", include_ascii=True)
        font = cbfont.FontFile(build_synthetic(plan))
        assert font.lookup_char("A") == 7614 + (0x41 - 0x20)
        assert font.lookup_key(0x20) == 7614

    def test_indexed_roundtrip(self):
        plan = cbfont.plan_unicode([0x4E8C, 0x41, 0x1F600, 0x4E00, 0x20], "t")
        font = cbfont.FontFile(build_synthetic(plan))
        assert font.indexed
        assert font.glyph_count == 5
        assert font.lookup_key(0x20) == 0
        assert font.lookup_key(0x4E8C) == 3
        assert font.lookup_key(0x1F600) == 4
        assert font.lookup_key(0x42) is None
        assert font.verify_crc()

    def test_glyph_bytes(self):
        plan = cbfont.plan_unicode([0x41, 0x42], "t")
        font = cbfont.FontFile(build_synthetic(plan))
        glyph = font.glyph(1)
        assert len(glyph) == 32
        assert glyph[0] == (1 * 7) & 0xFF

    def test_crc_detects_corruption(self):
        blob = bytearray(build_synthetic(cbfont.plan_unicode([0x41], "t")))
        blob[-1] ^= 0xFF
        font = cbfont.FontFile(bytes(blob))
        assert not font.verify_crc()

    def test_bad_magic(self):
        blob = bytearray(build_synthetic(cbfont.plan_unicode([0x41], "t")))
        blob[0] = ord("X")
        with pytest.raises(ValueError):
            cbfont.FontFile(bytes(blob))

    def test_key_for_char(self):
        gb = cbfont.FontFile(build_synthetic(cbfont.plan_charset("gb2312")))
        assert gb.key_for_char("啊") == 0xB0A1
        assert gb.key_for_char("A") == 0x41
        assert gb.key_for_char("€") is None  # 不在 GB2312 中

        ru = cbfont.FontFile(build_synthetic(cbfont.plan_charset("lang:ru")))
        assert ru.key_for_char("П") == 0x041F

    def test_lsb_first_flag(self):
        plan = cbfont.plan_unicode([0x41], "t")
        blob = cbfont.assemble_font(plan, synthetic_chunks(1), 16, cbfont.FLAG_LSB_FIRST)
        assert cbfont.FontFile(blob).lsb_first


class TestSpec:
    """docs/FORMAT.md 中的硬性约定。"""

    def test_constants(self):
        assert struct.calcsize(cbfont.HEADER_FMT) == cbfont.HEADER_SIZE == 48
        assert struct.calcsize(cbfont.RANGE_FMT) == cbfont.RANGE_SIZE == 12
        assert cbfont.MAX_RANGES == 8
        assert cbfont.ENC_UTF8 == 6
        assert len(cbfont.ENCODING_CODEC) == 7

    def test_dense_header_layout(self):
        blob = build_synthetic(cbfont.plan_charset("gb2312"))
        font = cbfont.FontFile(blob)

        assert blob[:4] == b"CBMF"
        assert blob[4] == 2
        assert blob[36:48] == b"\x00" * 12  # reserved 必须为 0
        assert font.file_size == len(blob)
        assert font.data_offset == 48 + 3 * 12
        assert font.index_offset == 0 and font.index_count == 0
        assert font.glyph_count == 7709

    def test_indexed_header_layout(self):
        blob = build_synthetic(cbfont.plan_unicode([0x20, 0x41, 0x4E00], "t"))
        font = cbfont.FontFile(blob)

        assert font.index_offset == 48
        assert font.index_count == font.glyph_count == 3
        assert font.data_offset == 48 + 3 * 2  # 全部 <= 0xFFFF，用 u16
        assert font.flags & cbfont.FLAG_INDEX_U16
        assert font.flags & cbfont.FLAG_INDEXED

    def test_indexed_u32_when_needed(self):
        blob = build_synthetic(cbfont.plan_unicode([0x41, 0x1F600], "t"))
        font = cbfont.FontFile(blob)
        assert not (font.flags & cbfont.FLAG_INDEX_U16)
        assert font.data_offset == 48 + 2 * 4

    def test_crc_covers_body_only(self):
        blob = build_synthetic(cbfont.plan_charset("gb2312", include_ascii=False))
        font = cbfont.FontFile(blob)
        assert cbfont.crc32(blob[48:font.file_size]) == font.crc32

    def test_range_limit_matches_runtime(self):
        ranges = [cbfont.Range(0xA1, 0xA1, 0xA1, 0xA1, i) for i in range(cbfont.MAX_RANGES + 1)]
        plan = cbfont.BuildPlan(
            "too-many", cbfont.ENC_GB2312, ranges,
            [0xA1A1] * len(ranges), [None] * len(ranges), False,
        )
        with pytest.raises(ValueError):
            cbfont.assemble_font(plan, bytes(len(ranges) * 32), 16, 0)

    def test_empty_charset_rejected(self):
        plan = cbfont.plan_unicode([], "empty")
        with pytest.raises(ValueError, match="字符集为空"):
            cbfont.assemble_font(plan, b"", 16, 0)

    def test_rejects_bad_data_offset(self):
        blob = bytearray(build_synthetic(cbfont.plan_unicode([0x41], "t")))
        struct.pack_into("<I", blob, 16, len(blob) + 4)
        with pytest.raises(ValueError):
            cbfont.FontFile(bytes(blob))

    def test_encode_key_roundtrip(self):
        for encoding, ch in (
            (cbfont.ENC_GB2312, "啊"),
            (cbfont.ENC_SHIFT_JIS, "あ"),
            (cbfont.ENC_SINGLE, "A"),
        ):
            key = cbfont.encode_key(encoding, ch)
            assert key is not None
            assert cbfont.decode_key(encoding, key) == ch
        assert cbfont.encode_key(cbfont.ENC_GB2312, "€") is None
        assert cbfont.encode_key(cbfont.ENC_UTF8, "😀") == 0x1F600


class TestCli:
    def test_info(self, tmp_path, capsys):
        path = tmp_path / "font.cbm"
        path.write_bytes(build_synthetic(cbfont.plan_unicode([0x41], "t")))
        assert cbfont.main(["info", str(path)]) == 0
        out = capsys.readouterr().out
        assert "CBMF" not in out
        assert "UTF-8" in out

    def test_preview_missing_char(self, tmp_path, capsys):
        path = tmp_path / "font.cbm"
        path.write_bytes(build_synthetic(cbfont.plan_unicode([0x41], "t")))
        assert cbfont.main(["preview", str(path), "AB"]) == 0
        out = capsys.readouterr().out
        assert "不存在" in out

    def test_build_rejects_missing_font(self, tmp_path):
        with pytest.raises(SystemExit):
            cbfont.main(["build", "-f", str(tmp_path / "nope.ttf"), "-c", "gb2312"])

    def test_unknown_command(self):
        with pytest.raises(SystemExit):
            cbfont.main(["nope"])


@requires_font
class TestWithFont:
    def test_build_deterministic(self, tmp_path):
        argv = ["build", "-c", "latin-1", "-f", str(FONT), "-s", "16"]
        a, b = tmp_path / "a.cbm", tmp_path / "b.cbm"
        assert cbfont.main(argv + ["-o", str(a)]) == 0
        assert cbfont.main(argv + ["-o", str(b)]) == 0
        assert a.read_bytes() == b.read_bytes()

    def test_build_gb2312_size(self, tmp_path):
        out = tmp_path / "gb.cbm"
        assert cbfont.main(["build", "-c", "gb2312", "-f", str(FONT), "-o", str(out)]) == 0
        font = cbfont.FontFile(out.read_bytes())
        assert font.glyph_count == 7709
        assert font.file_size == len(out.read_bytes())
        assert font.verify_crc()

    def test_preview_glyph(self, tmp_path, capsys):
        out = tmp_path / "gb.cbm"
        assert cbfont.main(["build", "-c", "gb2312", "-f", str(FONT), "-o", str(out)]) == 0
        capsys.readouterr()
        # 用 ASCII 而不是汉字：CI 上回退到 DejaVu 等西文字体时也能渲染出点阵
        assert cbfont.main(["preview", str(out), "A"]) == 0
        art = capsys.readouterr().out
        assert "#" in art
        assert "id=7647" in art  # 符号、汉字区之后是 ASCII 区


class TestFontCatalog:
    def test_missing_font_falls_back_to_stem(self, tmp_path):
        assert cbfont.font_display_name(str(tmp_path / "MyFont.ttf")) == "MyFont"

    def test_catalog_entries_have_localized_names(self):
        catalog = cbfont.font_catalog()
        if not catalog:
            pytest.skip("本机没有字体")
        for entry in catalog[:20]:
            assert set(entry) == {"path", "index", "name"}
            assert isinstance(entry["index"], int)
            assert entry["name"]
            assert "\\" not in entry["name"] and "/" not in entry["name"]

    def test_ttc_faces_are_expanded(self):
        path = next((p for p in cbfont.list_fonts() if p.lower().endswith(".ttc")), None)
        if not path:
            pytest.skip("本机没有 TTC 字体")
        faces = cbfont.font_faces(path)
        assert faces
        assert len({index for index, _ in faces}) == len(faces)
        assert all(name for _, name in faces)

