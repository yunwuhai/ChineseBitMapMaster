#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""ChineseBitmap 字库生成器（CBMF v2）。

把 TrueType 字体渲染成自描述点阵字库，支持：

* **多编码**（可选）：GB2312 / GBK / Big5 / Shift-JIS / EUC-KR / Latin-1 / UTF-8
* **任意语言**（可选）：语言预设、Unicode 范围、任意 UTF-8 文本文件
* **布局可选**：UTF-8 支持 BMP 密集（快/常更小）与码点索引，auto 自动选小的
* 定长 1bpp 点阵，默认 16x16，也可 8/12/24/32

文件格式（全部小端）：

    [48 字节头][区间表 12*N][码点索引（仅 UTF-8 模式）][字模数据]

用法示例：

    python tools/cbfont.py build -c gb2312 -s 16 -o build/gb2312.cbm
    python tools/cbfont.py build -c lang:ru -f arial.ttf -o build/ru.cbm
    python tools/cbfont.py build -c text:words.txt -f simsun.ttc -o build/words.cbm
    python tools/cbfont.py preview build/gb2312.cbm "永和九年"
    python tools/cbfont.py info build/gb2312.cbm
"""

from __future__ import annotations

import argparse
import importlib.util
import struct
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - 只在缺少依赖时触发
    sys.exit("缺少依赖 Pillow，请先执行: pip install pillow")


# ==========================================================================
# 文件格式
# ==========================================================================
MAGIC = b"CBMF"
VERSION = 2
HEADER_SIZE = 48
RANGE_SIZE = 12
MAX_RANGES = 8          # 与 C 运行时的 CBM_MAX_RANGES 保持一致
LAYOUTS = ("auto", "indexed", "dense")
HEADER_FMT = "<4sBBBBHIBBIIIIIIII"
RANGE_FMT = "<BBBBII"

FLAG_LSB_FIRST = 0x01
FLAG_INVERTED = 0x02
FLAG_INDEXED = 0x04
FLAG_INDEX_U16 = 0x08

ENC_SINGLE = 0
ENC_GB2312 = 1
ENC_GBK = 2
ENC_BIG5 = 3
ENC_SHIFT_JIS = 4
ENC_EUC_KR = 5
ENC_UTF8 = 6

ENCODING_CODEC = {
    ENC_SINGLE: "latin-1",
    ENC_GB2312: "gb2312",
    ENC_GBK: "gbk",
    ENC_BIG5: "big5",
    ENC_SHIFT_JIS: "shift_jis",
    ENC_EUC_KR: "euc_kr",
    ENC_UTF8: "utf-8",
}

ENCODING_LABEL = {
    ENC_SINGLE: "单字节",
    ENC_GB2312: "GB2312",
    ENC_GBK: "GBK",
    ENC_BIG5: "Big5",
    ENC_SHIFT_JIS: "Shift-JIS",
    ENC_EUC_KR: "EUC-KR",
    ENC_UTF8: "UTF-8 / Unicode",
}


@dataclass(frozen=True)
class Range:
    """一个密集区间：字形下标 = glyph_offset + (hi-hi_min)*宽 + (lo-lo_min)。

    hi_min == hi_max == 0 表示单字节区间，此时 key 本身就是 lo。
    """

    hi_min: int
    hi_max: int
    lo_min: int
    lo_max: int
    glyph_offset: int

    @property
    def width(self) -> int:
        return self.lo_max - self.lo_min + 1

    @property
    def single_byte(self) -> bool:
        return self.hi_min == 0 and self.hi_max == 0


@dataclass
class BuildPlan:
    name: str
    encoding: int
    ranges: list[Range]
    keys: list[int]
    chars: list[str | None]
    indexed: bool


# ==========================================================================
# 密集编码字符集
# ==========================================================================
@dataclass(frozen=True)
class DenseSpec:
    name: str
    encoding: int
    lead_blocks: tuple[tuple[int, int], ...]
    trail_rects: tuple[tuple[int, int], ...]
    single_range: tuple[int, int] | None = None
    single_extra: tuple[int, int] | None = None
    include_ascii: bool = True


DENSE_CHARSETS: dict[str, DenseSpec] = {
    # 简体中文：符号区 0xA1-0xA9 + 汉字区 0xB0-0xF7
    "gb2312": DenseSpec("gb2312", ENC_GB2312, ((0xA1, 0xA9), (0xB0, 0xF7)), ((0xA1, 0xFE),)),
    # 简体+繁体扩展
    "gbk": DenseSpec("gbk", ENC_GBK, ((0x81, 0xFE),), ((0x40, 0x7E), (0x80, 0xFE))),
    # 繁体中文
    "big5": DenseSpec("big5", ENC_BIG5, ((0xA1, 0xF9),), ((0x40, 0x7E), (0xA1, 0xFE))),
    # 日文（另含半角片假名单字节 0xA1-0xDF）
    "shift-jis": DenseSpec(
        "shift-jis", ENC_SHIFT_JIS,
        ((0x81, 0x9F), (0xE0, 0xEF)),
        ((0x40, 0x7E), (0x80, 0xFC)),
        single_extra=(0xA1, 0xDF),
    ),
    # 韩文
    "euc-kr": DenseSpec("euc-kr", ENC_EUC_KR, ((0xA1, 0xFE),), ((0xA1, 0xFE),)),
    # 西欧
    "latin-1": DenseSpec(
        "latin-1", ENC_SINGLE, (), (), single_range=(0x20, 0xFF), include_ascii=False
    ),
    # 纯 ASCII
    "ascii": DenseSpec(
        "ascii", ENC_SINGLE, (), (), single_range=(0x20, 0x7E), include_ascii=False
    ),
}

# 语言预设：UTF-8 索引模式，覆盖该语言的常用 Unicode 区段。
# 统一附带 ASCII（0x20-0x7E），方便中英/日英混排。
_ASCII_RANGE = ((0x0020, 0x007E),)

LANGUAGES: dict[str, tuple[str, tuple[tuple[int, int], ...]]] = {
    "zh-Hans": ("简体中文（CJK 全集）", _ASCII_RANGE + ((0x3000, 0x303F), (0xFF00, 0xFFEF), (0x4E00, 0x9FFF))),
    "zh-Hant": ("繁体中文（CJK 全集）", _ASCII_RANGE + ((0x3000, 0x303F), (0xFF00, 0xFFEF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF))),
    "ja": ("日文", _ASCII_RANGE + ((0x3000, 0x30FF), (0x31F0, 0x31FF), (0xFF00, 0xFFEF), (0x4E00, 0x9FFF))),
    "ko": ("韩文", _ASCII_RANGE + ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7A3))),
    "ru": ("俄文", _ASCII_RANGE + ((0x0400, 0x04FF),)),
    "el": ("希腊文", _ASCII_RANGE + ((0x0370, 0x03FF),)),
    "he": ("希伯来文", _ASCII_RANGE + ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))),
    "ar": ("阿拉伯文", _ASCII_RANGE + ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFE70, 0xFEFF))),
    "th": ("泰文", _ASCII_RANGE + ((0x0E00, 0x0E7F),)),
    "hi": ("天城文（印地语）", _ASCII_RANGE + ((0x0900, 0x097F),)),
    "latin": ("拉丁字母（含西欧）", ((0x0020, 0x007E), (0x00A0, 0x024F))),
    "vi": ("越南文", ((0x0020, 0x007E), (0x00C0, 0x1EF9))),
}


def charset_choices() -> list[tuple[str, str]]:
    """给 UI / CLI 用的字符集清单：(spec, 说明)。"""
    choices = []
    for name, spec in DENSE_CHARSETS.items():
        choices.append((name, f"{ENCODING_LABEL[spec.encoding]} 密集编码（{name}）"))
    for code, (label, ranges) in LANGUAGES.items():
        total = sum(b - a + 1 for a, b in ranges)
        choices.append((f"lang:{code}", f"{label}（UTF-8，最多 {total} 个码位）"))
    choices.append(("text:FILE", "UTF-8 文本文件（只收录出现的字符）"))
    choices.append(("range:U+4E00-U+9FFF", "自定义 Unicode 区段"))
    return choices


# ==========================================================================
# 字符集规划
# ==========================================================================
def parse_range_spec(spec: str) -> list[int]:
    """解析 'U+4E00-U+9FFF,U+0400' 这类区段描述。"""
    codepoints: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left.strip().replace("U+", "").replace("u+", ""), 16)
            end = int(right.strip().replace("U+", "").replace("u+", ""), 16)
        else:
            start = end = int(part.replace("U+", "").replace("u+", ""), 16)
        if end < start:
            start, end = end, start
        codepoints.extend(range(start, end + 1))
    return codepoints


def _is_renderable(cp: int) -> bool:
    category = unicodedata.category(chr(cp))
    return category not in ("Cc", "Cs", "Cn")


def plan_dense(spec: DenseSpec, include_ascii: bool = True) -> BuildPlan:
    ranges: list[Range] = []
    keys: list[int] = []
    offset = 0

    def add_range(hi_min: int, hi_max: int, lo_min: int, lo_max: int) -> None:
        nonlocal offset
        ranges.append(Range(hi_min, hi_max, lo_min, lo_max, offset))
        for hi in range(hi_min, hi_max + 1):
            for lo in range(lo_min, lo_max + 1):
                keys.append(lo if hi == 0 else (hi << 8) | lo)
        offset += (hi_max - hi_min + 1) * (lo_max - lo_min + 1)

    for hi_min, hi_max in spec.lead_blocks:
        for lo_min, lo_max in spec.trail_rects:
            add_range(hi_min, hi_max, lo_min, lo_max)
    if spec.single_range is not None:
        add_range(0, 0, *spec.single_range)
    if spec.single_extra is not None:
        add_range(0, 0, *spec.single_extra)
    if include_ascii and spec.include_ascii:
        add_range(0, 0, 0x20, 0x7E)

    chars = [decode_key(spec.encoding, key) for key in keys]
    return BuildPlan(spec.name, spec.encoding, ranges, keys, chars, indexed=False)


def plan_unicode(codepoints: list[int], name: str) -> BuildPlan:
    unique = sorted({cp for cp in codepoints if 0 <= cp <= 0x10FFFF and _is_renderable(cp)})
    return BuildPlan(name, ENC_UTF8, [], unique, [chr(cp) for cp in unique], indexed=True)


def plan_bmp_dense(plan: BuildPlan) -> BuildPlan | None:
    """把 UTF-8 索引计划改写为 BMP 密集计划；不适用时返回 None。

    UTF-8 的查找 key 就是码点，而密集查找按 ``(key >> 8, key & 0xFF)`` 计算，
    所以只支持 BMP（U+0000–U+FFFF）。同一个 256 码点块内取最小/最大 lo 组成
    矩形，相邻且矩形完全相同的块会合并；超过 C 运行时的区间上限就放弃。
    """
    if plan.encoding != ENC_UTF8 or not plan.indexed or not plan.keys:
        return None
    if any(cp > 0xFFFF for cp in plan.keys):
        return None

    blocks: dict[int, list[int]] = {}
    for cp in plan.keys:
        blocks.setdefault(cp >> 8, []).append(cp & 0xFF)

    raw: list[list[int]] = []
    for hi in sorted(blocks):
        los = blocks[hi]
        raw.append([hi, hi, min(los), max(los)])

    merged: list[list[int]] = []
    for hi_min, hi_max, lo_min, lo_max in raw:
        if (merged and hi_min != 0 and merged[-1][1] != 0
                and merged[-1][1] + 1 == hi_min
                and merged[-1][2] == lo_min and merged[-1][3] == lo_max):
            merged[-1][1] = hi_max
        else:
            merged.append([hi_min, hi_max, lo_min, lo_max])

    if len(merged) > MAX_RANGES:
        return None

    ranges: list[Range] = []
    keys: list[int] = []
    offset = 0
    for hi_min, hi_max, lo_min, lo_max in merged:
        ranges.append(Range(hi_min, hi_max, lo_min, lo_max, offset))
        for hi in range(hi_min, hi_max + 1):
            for lo in range(lo_min, lo_max + 1):
                keys.append(lo if hi == 0 else (hi << 8) | lo)
        offset += (hi_max - hi_min + 1) * (lo_max - lo_min + 1)

    return BuildPlan(plan.name, ENC_UTF8, ranges, keys, [chr(key) for key in keys], False)


def estimate_font_size(plan: BuildPlan, size: int) -> int:
    """按 CBMF v2 布局估算文件字节数（不考虑字体缺字被跳过的变化）。"""
    bpg = bytes_per_glyph(size, size)
    total = HEADER_SIZE + RANGE_SIZE * len(plan.ranges) + bpg * len(plan.keys)
    if plan.indexed:
        width = 2 if max(plan.keys, default=0) <= 0xFFFF else 4
        total += width * len(plan.keys)
    return total


def choose_layout(plan: BuildPlan, layout: str, size: int) -> BuildPlan:
    """按 auto/indexed/dense 选择布局。

    这是 plan 级估算（供 ``chars`` 和测试使用）；``build_font`` 会先渲染，
    再按实际缺字情况重新选择。DBCS 字符集本身必须是密集布局。
    """
    if layout not in LAYOUTS:
        raise ValueError(f"未知布局: {layout}（可选: {', '.join(LAYOUTS)}）")
    if not plan.indexed:
        if layout == "indexed":
            raise ValueError("GB2312/GBK 等 DBCS 字符集只能使用密集布局")
        return plan
    if layout == "indexed":
        return plan

    dense = plan_bmp_dense(plan)
    if layout == "dense":
        if dense is None:
            raise ValueError("密集布局仅支持 BMP（U+0000–U+FFFF）且区间不超过 8 个")
        return dense
    if dense is None:
        return plan
    return dense if estimate_font_size(dense, size) <= estimate_font_size(plan, size) else plan


def plan_charset(spec: str, include_ascii: bool = True, text_encoding: str = "utf-8") -> BuildPlan:
    if spec in DENSE_CHARSETS:
        return plan_dense(DENSE_CHARSETS[spec], include_ascii)

    if spec.startswith("lang:"):
        code = spec[5:]
        if code not in LANGUAGES:
            raise ValueError(f"未知语言: {code}（可选: {', '.join(LANGUAGES)}）")
        label, ranges = LANGUAGES[code]
        points: list[int] = []
        for start, end in ranges:
            points.extend(range(start, end + 1))
        return plan_unicode(points, f"lang:{code}")

    if spec.startswith("range:"):
        points = parse_range_spec(spec[6:])
        return plan_unicode(points, spec)

    if spec.startswith("text:"):
        path = Path(spec[5:])
        if not path.is_file():
            raise ValueError(f"文本文件不存在: {path}")
        text = path.read_text(encoding=text_encoding, errors="replace")
        return plan_unicode([ord(ch) for ch in text], f"text:{path.name}")

    raise ValueError(f"未知字符集: {spec}")


def decode_key(encoding: int, key: int) -> str | None:
    """把编码 key 还原成字符；未分配码返回 None。"""
    codec = ENCODING_CODEC[encoding]
    try:
        if key <= 0xFF:
            return bytes((key,)).decode(codec)
        return bytes(((key >> 8) & 0xFF, key & 0xFF)).decode(codec)
    except (UnicodeDecodeError, ValueError):
        return None


def encode_key(encoding: int, ch: str) -> int | None:
    """把字符编码成查找 key；无法用该编码表示时返回 None。

    与 C 侧 cbm_next 产出的 key 完全一致：UTF-8 为码点，DBCS 为
    ``(前导字节 << 8) | 尾字节``，单字节编码为字节本身。
    """
    if encoding == ENC_UTF8:
        return ord(ch)
    try:
        raw = ch.encode(ENCODING_CODEC[encoding])
    except UnicodeEncodeError:
        return None
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return (raw[0] << 8) | raw[1]
    return None


# ==========================================================================
# 渲染与打包
# ==========================================================================
def bytes_per_row(width: int) -> int:
    return (width + 7) // 8


def bytes_per_glyph(width: int, height: int) -> int:
    return bytes_per_row(width) * height


def pack_image(img: "Image.Image", threshold: int = 128,
               invert: bool = False, lsb_first: bool = False) -> bytes:
    """灰度图 -> 行主序 1bpp 位图（纯函数，不依赖字体）。"""
    width, height = img.size
    stride = bytes_per_row(width)
    out = bytearray(stride * height)
    pixels = img.load()
    for y in range(height):
        base = y * stride
        for x in range(width):
            on = pixels[x, y] >= threshold
            if invert:
                on = not on
            if on:
                if lsb_first:
                    out[base + (x >> 3)] |= 1 << (x & 7)
                else:
                    out[base + (x >> 3)] |= 0x80 >> (x & 7)
    return bytes(out)


_MISSING_SENTINEL = "\U000F0000"


class Rasterizer:
    """把字符渲染到固定大小的单元格。"""

    def __init__(self, font_path: Path, size: int = 16, font_index: int = 0,
                 threshold: int = 128, x_offset: int = 0, y_offset: int = 0,
                 fit: bool = False, invert: bool = False, lsb_first: bool = False) -> None:
        self.size = size
        self.width = size
        self.height = size
        self.threshold = threshold
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.fit = fit
        self.invert = invert
        self.lsb_first = lsb_first
        self.font = ImageFont.truetype(
            str(font_path), size, index=font_index,
            layout_engine=ImageFont.Layout.BASIC,
        )
        self._notdef = self.image(_MISSING_SENTINEL).tobytes()

    def image(self, text: str) -> "Image.Image":
        img = Image.new("L", (self.width, self.height), 0)
        draw = ImageDraw.Draw(img)
        x, y = self.x_offset, self.y_offset
        if self.fit:
            left, top, right, bottom = self.font.getbbox(text)
            x += (self.width - (right - left)) // 2 - left
            y += (self.height - (bottom - top)) // 2 - top
        draw.text((x, y), text, font=self.font, fill=255)
        return img

    def is_missing_image(self, img: "Image.Image") -> bool:
        return img.tobytes() == self._notdef

    def is_missing(self, ch: str) -> bool:
        if ch.isspace():
            return False
        return self.is_missing_image(self.image(ch))


# ==========================================================================
# 字体探测
# ==========================================================================
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\mingliub.ttc",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]


def resolve_font(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"字体文件不存在: {path}")
        return path
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    raise SystemExit(
        "未找到可用的中文字体，请用 -f/--font 指定，例如:\n"
        "  -f C:/Windows/Fonts/simsun.ttc"
    )


def list_fonts() -> list[str]:
    """扫描常见目录，供 UI 下拉选择。"""
    seen: list[str] = []
    for pattern in ("*.ttf", "*.ttc", "*.otf", "*.TTF", "*.TTC", "*.OTF"):
        for base in (Path(r"C:\Windows\Fonts"), Path("/usr/share/fonts"), Path("/System/Library/Fonts")):
            if not base.is_dir():
                continue
            try:
                for path in base.rglob(pattern):
                    text = str(path)
                    if text not in seen:
                        seen.append(text)
            except OSError:
                continue
    return sorted(seen)


def _ttc_face_offsets(fp) -> list[int]:
    """返回 TTC 各 face 的偏移；普通字体返回 [0]。"""
    head = fp.read(12)
    if len(head) < 12:
        return []
    if head[:4] != b"ttcf":
        return [0]
    count = struct.unpack(">I", head[8:12])[0]
    if not 1 <= count <= 1024:
        return []
    raw = fp.read(count * 4)
    if len(raw) < count * 4:
        return []
    return list(struct.unpack(f">{count}I", raw))


def _name_records(fp, face_offset: int) -> list[tuple[int, int, int, str]]:
    """读取一个 face 的 name 表，返回 (platform, lang, name_id, text)。"""
    fp.seek(face_offset)
    header = fp.read(12)
    if len(header) < 12:
        return []
    table_count = struct.unpack(">H", header[4:6])[0]
    if not 1 <= table_count <= 4096:
        return []
    fp.seek(face_offset + 12)
    directory = fp.read(table_count * 16)
    if len(directory) < table_count * 16:
        return []

    name_offset = name_length = 0
    for i in range(table_count):
        tag, _checksum, offset, length = struct.unpack(">4sIII", directory[i * 16:(i + 1) * 16])
        if tag == b"name":
            name_offset, name_length = offset, length
            break
    if not name_offset or name_length < 6:
        return []

    fp.seek(name_offset)
    data = fp.read(name_length)
    if len(data) < 6:
        return []
    _fmt, count, string_offset = struct.unpack(">HHH", data[:6])
    if count > 4096:
        return []

    records: list[tuple[int, int, int, str]] = []
    for i in range(count):
        pos = 6 + i * 12
        if pos + 12 > len(data):
            break
        platform, _encoding, lang, name_id, length, offset = struct.unpack(
            ">HHHHHH", data[pos:pos + 12]
        )
        start = string_offset + offset
        raw = data[start:start + length]
        if len(raw) < length:
            continue
        if platform in (0, 3):
            text = raw.decode("utf-16-be", errors="replace")
        elif platform == 1:
            text = raw.decode("mac_roman", errors="replace")
        else:
            continue
        text = text.strip()
        if text:
            records.append((platform, lang, name_id, text))
    return records


_NAME_LANG_PRIORITY = (0x0804, 0x1004, 0x0404, 0x0C04, 0x0409, 0x0411, 0x0412, 0)


def _pick_name(records: list[tuple[int, int, int, str]], name_id: int) -> str:
    for lang in _NAME_LANG_PRIORITY:
        for _platform, record_lang, record_id, text in records:
            if record_id == name_id and record_lang == lang:
                return text
    for _platform, _lang, record_id, text in records:
        if record_id == name_id:
            return text
    return ""


def font_faces(path: str) -> list[tuple[int, str]]:
    """读取字体的本地化家族名（TTC 会展开全部 face）。

    返回 [(face_index, name), ...]；读取失败时返回空列表。
    """
    try:
        with open(path, "rb") as fp:
            offsets = _ttc_face_offsets(fp)
            faces: list[tuple[int, str]] = []
            for index, offset in enumerate(offsets):
                records = _name_records(fp, offset)
                family = _pick_name(records, 16) or _pick_name(records, 1)
                if not family:
                    continue
                style = _pick_name(records, 17) or _pick_name(records, 2)
                if style and style.lower() not in ("regular", "常规"):
                    family = f"{family} {style}"
                faces.append((index, family))
            return faces
    except (OSError, struct.error):
        return []


def font_display_name(path: str) -> str:
    """单个字体的显示名；没有 name 表时退回文件名。"""
    faces = font_faces(path)
    return faces[0][1] if faces else Path(path).stem


def font_catalog() -> list[dict[str, object]]:
    """供 UI 下拉使用的字体清单（TTC 每个 face 一项）。"""
    catalog: list[dict[str, object]] = []
    for path in list_fonts():
        faces = font_faces(path) or [(0, Path(path).stem)]
        for index, name in faces:
            catalog.append({"path": path, "index": index, "name": name})
    return catalog


# ==========================================================================
# 构建
# ==========================================================================
@dataclass
class BuildResult:
    data: bytes
    plan: BuildPlan
    width: int
    height: int
    bytes_per_glyph: int
    missing: list[str] = field(default_factory=list)
    blank: int = 0
    skipped: int = 0

    @property
    def count(self) -> int:
        return len(self.plan.keys)

    @property
    def mode(self) -> str:
        return "indexed" if self.plan.indexed else "dense"


def assemble_font(plan: BuildPlan, chunks: bytes, size: int, flags: int) -> bytes:
    """把计划 + 字模数据组装成 CBMF v2 文件（供 build 与测试夹具共用）。"""
    bpg = bytes_per_glyph(size, size)

    if len(plan.ranges) > MAX_RANGES:
        raise ValueError(
            f"密集区间 {len(plan.ranges)} 个，超过 C 运行时上限 {MAX_RANGES} 个"
        )
    if not plan.keys:
        raise ValueError("字符集为空，没有可生成的字符")
    if bpg > 0xFFFF:
        raise ValueError(f"单字模 {bpg} 字节，超过头部 u16 字段上限")
    if len(chunks) != len(plan.keys) * bpg:
        raise ValueError("字模数据长度与字符集不匹配")

    range_bytes = b"".join(
        struct.pack(RANGE_FMT, r.hi_min, r.hi_max, r.lo_min, r.lo_max, r.glyph_offset, 0)
        for r in plan.ranges
    )

    index_bytes = b""
    index_count = 0
    if plan.indexed:
        index_count = len(plan.keys)
        use16 = max(plan.keys, default=0) <= 0xFFFF
        flags |= FLAG_INDEXED
        if use16:
            flags |= FLAG_INDEX_U16
        index_bytes = struct.pack(f"<{index_count}{'H' if use16 else 'I'}", *plan.keys)

    index_offset = HEADER_SIZE + len(range_bytes) if index_bytes else 0
    data_offset = HEADER_SIZE + len(range_bytes) + len(index_bytes)
    file_size = data_offset + len(chunks)

    body = range_bytes + index_bytes + bytes(chunks)
    crc = crc32(body)
    header = struct.pack(
        HEADER_FMT,
        MAGIC, VERSION, flags, size, size, bpg,
        len(plan.keys), plan.encoding, len(plan.ranges),
        data_offset, index_offset, index_count, crc,
        file_size, 0, 0, 0,
    )
    return header + body


def build_font(*, font_path: Path, charset: str, size: int = 16, font_index: int = 0,
               threshold: int = 128, x_offset: int = 0, y_offset: int = 0,
               fit: bool = False, invert: bool = False, lsb_first: bool = False,
               include_ascii: bool = True, text_encoding: str = "utf-8",
               fill_missing: bool = False, layout: str = "auto") -> BuildResult:
    if not 1 <= size <= 255:
        raise ValueError("点阵尺寸必须在 1-255 之间（头部宽高各占 1 字节）")
    if layout not in LAYOUTS:
        raise ValueError(f"未知布局: {layout}（可选: {', '.join(LAYOUTS)}）")
    plan = plan_charset(charset, include_ascii, text_encoding)
    if not plan.indexed and layout == "indexed":
        raise ValueError("GB2312/GBK 等 DBCS 字符集只能使用密集布局")
    if not plan.keys:
        raise ValueError("字符集为空，没有可生成的字符")

    bpg = bytes_per_glyph(size, size)
    dense_plan: BuildPlan | None = None
    if plan.indexed and layout != "indexed":
        dense_plan = plan_bmp_dense(plan)
        if layout == "dense" and dense_plan is None:
            raise ValueError("密集布局仅支持 BMP（U+0000–U+FFFF）且区间不超过 8 个")

    raster = Rasterizer(
        font_path, size=size, font_index=font_index, threshold=threshold,
        x_offset=x_offset, y_offset=y_offset, fit=fit, invert=invert, lsb_first=lsb_first,
    )

    # 只渲染一次：索引模式据此判断缺字，auto 模式据此比较“跳过缺字后的索引体积”
    # 与“包含空槽的密集体积”，避免对缺字多的字体误选密集布局。
    present: dict[int, bytes] = {}
    missing: list[str] = []
    for key, ch in zip(plan.keys, plan.chars):
        if ch is None:
            continue
        if unicodedata.category(ch) in ("Cc", "Cs", "Cf"):
            continue
        img = raster.image(ch)
        if not ch.isspace() and raster.is_missing_image(img):
            missing.append(ch)
            continue
        present[key] = pack_image(img, raster.threshold, raster.invert, raster.lsb_first)

    if not plan.indexed:
        chosen = plan
    elif layout == "indexed" or dense_plan is None:
        chosen = plan
    elif layout == "dense":
        chosen = dense_plan
    else:
        count = len(plan.keys) if fill_missing else len(present)
        index_width = 2 if max(plan.keys, default=0) <= 0xFFFF else 4
        indexed_size = HEADER_SIZE + index_width * count + bpg * count
        dense_size = (HEADER_SIZE + RANGE_SIZE * len(dense_plan.ranges)
                      + bpg * len(dense_plan.keys))
        chosen = dense_plan if dense_size <= indexed_size else plan

    if chosen.indexed:
        keys: list[int] = []
        chars: list[str] = []
        chunks = bytearray()
        for key, ch in zip(plan.keys, plan.chars):
            if key in present:
                keys.append(key)
                chars.append(ch)
                chunks += present[key]
            elif fill_missing and ch is not None:
                keys.append(key)
                chars.append(ch)
                chunks += bytes(bpg)
        if not keys:
            raise ValueError("没有可生成的字符（字体缺少全部字形）")
        chosen = BuildPlan(chosen.name, chosen.encoding, [], keys, chars, True)
    else:
        chunks = bytearray()
        keys = list(chosen.keys)
        for key in keys:
            chunks += present.get(key, bytes(bpg))
        if not keys:
            raise ValueError("没有可生成的字符（字体缺少全部字形）")

    blank = sum(1 for key in keys if key not in present)
    skipped = len(missing) if chosen.indexed and not fill_missing else 0

    flags = 0
    if lsb_first:
        flags |= FLAG_LSB_FIRST
    if invert:
        flags |= FLAG_INVERTED

    data = assemble_font(chosen, bytes(chunks), size, flags)
    return BuildResult(data, chosen, size, size, bpg, missing, blank, skipped)


def crc32(data: bytes) -> int:
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


# ==========================================================================
# 读取 / 查找
# ==========================================================================
class FontFile:
    """解析 CBMF v2 文件，供 preview/info/UI/测试使用。"""

    def __init__(self, data: bytes) -> None:
        if len(data) < HEADER_SIZE:
            raise ValueError("文件太短，不是有效的 CBMF 字库")
        (magic, self.version, self.flags, self.width, self.height, self.bytes_per_glyph,
         self.glyph_count, self.encoding, self.range_count, self.data_offset,
         self.index_offset, self.index_count, self.crc32, self.file_size,
         *_rest) = struct.unpack_from(HEADER_FMT, data, 0)

        if magic != MAGIC:
            raise ValueError(f"文件头错误: {magic!r}，应为 {MAGIC!r}")
        if self.version != VERSION:
            raise ValueError(f"不支持的版本: {self.version}")
        if self.range_count > MAX_RANGES:
            raise ValueError(f"区间数量 {self.range_count} 超过上限 {MAX_RANGES}")
        if any(_rest):
            raise ValueError("头部保留字段必须为 0")
        if self.file_size != len(data):
            raise ValueError(f"文件长度 {len(data)} 与头部记录 {self.file_size} 不一致")
        if self.width == 0 or self.height == 0 or self.bytes_per_glyph == 0:
            raise ValueError("点阵尺寸无效")
        if self.bytes_per_glyph != bytes_per_glyph(self.width, self.height):
            raise ValueError("bytes_per_glyph 与 width/height 不一致")
        if self.encoding not in ENCODING_LABEL:
            raise ValueError(f"未知编码编号: {self.encoding}")
        if self.data_offset < HEADER_SIZE or self.data_offset > self.file_size:
            raise ValueError("data_offset 越界")
        if self.glyph_count * self.bytes_per_glyph > self.file_size - self.data_offset:
            raise ValueError("字模数据超出文件范围")

        index_width = 2 if self.flags & FLAG_INDEX_U16 else 4
        if self.flags & FLAG_INDEXED:
            if self.range_count != 0:
                raise ValueError("索引模式不应包含密集区间")
            if self.index_count != self.glyph_count:
                raise ValueError("index_count 与 glyph_count 不一致")
            if not (HEADER_SIZE <= self.index_offset <= self.data_offset):
                raise ValueError("index_offset 越界")
            if self.index_count * index_width > self.data_offset - self.index_offset:
                raise ValueError("码点索引超出数据区")
        elif self.index_offset != 0 or self.index_count != 0:
            raise ValueError("密集模式不应包含码点索引")

        self.data = data
        self.ranges: list[Range] = []
        pos = HEADER_SIZE
        for _ in range(self.range_count):
            hi_min, hi_max, lo_min, lo_max, offset, _reserved = struct.unpack_from(RANGE_FMT, data, pos)
            self.ranges.append(Range(hi_min, hi_max, lo_min, lo_max, offset))
            pos += RANGE_SIZE

        self.index: list[int] = []
        if self.flags & FLAG_INDEXED and self.index_count:
            width = 2 if self.flags & FLAG_INDEX_U16 else 4
            fmt = "<%d%s" % (self.index_count, "H" if width == 2 else "I")
            self.index = list(struct.unpack_from(fmt, data, self.index_offset))

    # -- 基础信息 ---------------------------------------------------------
    @property
    def indexed(self) -> bool:
        return bool(self.flags & FLAG_INDEXED)

    @property
    def lsb_first(self) -> bool:
        return bool(self.flags & FLAG_LSB_FIRST)

    @property
    def inverted(self) -> bool:
        return bool(self.flags & FLAG_INVERTED)

    @property
    def encoding_name(self) -> str:
        return ENCODING_LABEL.get(self.encoding, f"未知({self.encoding})")

    def glyph(self, index: int) -> bytes:
        start = self.data_offset + index * self.bytes_per_glyph
        return self.data[start:start + self.bytes_per_glyph]

    def verify_crc(self) -> bool:
        return crc32(self.data[HEADER_SIZE:self.file_size]) == self.crc32

    # -- 查找 -------------------------------------------------------------
    def key_for_char(self, ch: str) -> int | None:
        return encode_key(self.encoding, ch)

    def lookup_key(self, key: int) -> int | None:
        if self.indexed:
            lo, hi = 0, len(self.index)
            while lo < hi:
                mid = (lo + hi) // 2
                if self.index[mid] < key:
                    lo = mid + 1
                else:
                    hi = mid
            if lo < len(self.index) and self.index[lo] == key:
                return lo
            return None

        for r in self.ranges:
            if r.single_byte:
                if r.lo_min <= key <= r.lo_max:
                    idx = r.glyph_offset + (key - r.lo_min)
                    return idx if idx < self.glyph_count else None
            else:
                hi, lo = key >> 8, key & 0xFF
                if r.hi_min <= hi <= r.hi_max and r.lo_min <= lo <= r.lo_max:
                    idx = r.glyph_offset + (hi - r.hi_min) * r.width + (lo - r.lo_min)
                    return idx if idx < self.glyph_count else None
        return None

    def lookup_char(self, ch: str) -> int | None:
        key = self.key_for_char(ch)
        return None if key is None else self.lookup_key(key)

    # -- 预览 -------------------------------------------------------------
    def draw(self, index: int) -> list[str]:
        glyph = self.glyph(index)
        stride = bytes_per_row(self.width)
        lines = []
        for y in range(self.height):
            row = glyph[y * stride:(y + 1) * stride]
            bits = []
            for x in range(self.width):
                if self.lsb_first:
                    on = row[x >> 3] & (1 << (x & 7))
                else:
                    on = row[x >> 3] & (0x80 >> (x & 7))
                bits.append("#" if on else ".")
            lines.append("".join(bits))
        return lines


def load_font(path: Path) -> FontFile:
    return FontFile(path.read_bytes())


# ==========================================================================
# 命令行
# ==========================================================================
def write_c_header(path: Path, result: BuildResult) -> None:
    stem = path.stem.replace("-", "_")
    upper = stem.upper()
    lines = [
        "/* Generated by tools/cbfont.py - do not edit by hand. */",
        f"#ifndef {upper}_H",
        f"#define {upper}_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define {upper}_WIDTH  {result.width}",
        f"#define {upper}_HEIGHT {result.height}",
        f"#define {upper}_COUNT  {result.count}",
        "",
        f"static const uint8_t {stem}[{len(result.data)}] = {{",
    ]
    data = result.data
    for start in range(0, len(data), 16):
        chunk = data[start:start + 16]
        lines.append("    " + ", ".join(f"0x{byte:02X}" for byte in chunk) + ",")
    lines += ["};", "", "#endif", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def describe_plan(plan: BuildPlan) -> str:
    assigned = sum(1 for ch in plan.chars if ch is not None)
    return (
        f"{plan.name}: {len(plan.keys)} 个码位/槽位，"
        f"{assigned} 个已分配，{len(plan.keys) - assigned} 个未分配"
    )


def cmd_build(args: argparse.Namespace) -> int:
    font_path = resolve_font(args.font)
    try:
        result = build_font(
            font_path=font_path,
            charset=args.charset,
            size=args.size,
            font_index=args.font_index,
            threshold=args.threshold,
            x_offset=args.x_offset,
            y_offset=args.y_offset,
            fit=args.fit,
            invert=args.invert,
            lsb_first=args.lsb_first,
            include_ascii=not args.no_ascii,
            text_encoding=args.text_encoding,
            fill_missing=args.fill_missing,
            layout=args.layout,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))

    out = Path(args.output) if args.output else Path(f"{result.plan.name.replace(':', '_')}.cbm")
    if args.format == "c":
        if out.suffix.lower() not in (".h", ".c"):
            out = out.with_suffix(".h")
        write_c_header(out, result)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(result.data)

    print(f"字体: {font_path}")
    print(f"字符集: {describe_plan(result.plan)}")
    print(f"规格: {result.width}x{result.height}, {result.bytes_per_glyph} 字节/字, "
          f"编码 {result.plan.encoding} ({ENCODING_LABEL[result.plan.encoding]}), {result.mode}")
    print(f"输出: {out}  ({len(result.data)} 字节, {result.count} 字)")

    if result.blank:
        print(f"空白槽位: {result.blank}")
    if result.skipped:
        print(f"已跳过 {result.skipped} 个字体缺失字符（可用 --fill-missing 保留空白）")
    elif result.missing:
        sample = " ".join(result.missing[:10])
        print(f"警告: 字体缺少 {len(result.missing)} 个字模（示例: {sample}）")
    if result.missing and args.strict:
        print("--strict 已开启，构建失败。", file=sys.stderr)
        return 1
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    font = load_font(Path(args.file))
    print(f"文件: {args.file}")
    print(f"版本: {font.version}  大小: {font.file_size} 字节  CRC: {'OK' if font.verify_crc() else '校验失败'}")
    print(f"编码: {font.encoding} ({font.encoding_name})  模式: {'索引' if font.indexed else '密集'}")
    print(f"规格: {font.width}x{font.height}, {font.bytes_per_glyph} 字节/字, {font.glyph_count} 字")
    print(f"标志: lsb_first={font.lsb_first} inverted={font.inverted}")
    print(f"区间: {font.range_count}  码点索引: {font.index_count}")
    for i, r in enumerate(font.ranges[:8]):
        kind = "单字节" if r.single_byte else f"0x{r.hi_min:02X}-0x{r.hi_max:02X}"
        print(f"  [{i}] {kind} x 0x{r.lo_min:02X}-0x{r.lo_max:02X}  offset={r.glyph_offset}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    font = load_font(Path(args.file))
    text = args.text
    shown = 0
    for ch in text:
        if ch in "\r\n":
            continue
        index = font.lookup_char(ch)
        if index is None:
            print(f"--- {ch!r}: 字库中不存在 ---")
            continue
        print(f"--- {ch}  U+{ord(ch):04X}  id={index} ---")
        for line in font.draw(index):
            print(line)
        shown += 1
    if shown == 0:
        print("(没有可显示的字形)")
    return 0


def cmd_chars(args: argparse.Namespace) -> int:
    try:
        plan = plan_charset(args.charset, not args.no_ascii, args.text_encoding)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.layout == "indexed" and not plan.indexed:
        raise SystemExit("GB2312/GBK 等 DBCS 字符集只能使用密集布局")
    if args.layout == "dense" and plan.indexed and plan_bmp_dense(plan) is None:
        raise SystemExit("密集布局仅支持 BMP（U+0000–U+FFFF）且区间不超过 8 个")
    print(describe_plan(plan))

    dense = plan_bmp_dense(plan) if plan.indexed else None
    if args.layout == "auto":
        if not plan.indexed:
            print(f"布局: 密集  "
                  f"预计: {estimate_font_size(plan, args.size)} 字节")
        elif dense is None:
            print(f"布局: 索引  "
                  f"预计: {estimate_font_size(plan, args.size)} 字节")
        else:
            print(f"布局: auto（索引约 {estimate_font_size(plan, args.size)} 字节 / "
                  f"密集约 {estimate_font_size(dense, args.size)} 字节，构建时按缺字选择）")
    else:
        try:
            plan = choose_layout(plan, args.layout, args.size)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"布局: {'索引' if plan.indexed else '密集'}  "
              f"预计: {estimate_font_size(plan, args.size)} 字节")
    if plan.ranges:
        for i, r in enumerate(plan.ranges):
            kind = "单字节" if r.single_byte else f"0x{r.hi_min:02X}-0x{r.hi_max:02X}"
            print(f"  [{i}] {kind} x 0x{r.lo_min:02X}-0x{r.lo_max:02X}  offset={r.glyph_offset}")
    if args.font:
        font_path = resolve_font(args.font)
        raster = Rasterizer(font_path, size=args.size, font_index=args.font_index)
        missing = [ch for ch in plan.chars if ch and not ch.isspace()
                   and unicodedata.category(ch) not in ("Cc", "Cs", "Cf")
                   and raster.is_missing(ch)]
        print(f"字体缺失: {len(missing)}")
        for ch in missing[:20]:
            print(f"  U+{ord(ch):04X} {ch}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """启动 Web 配置界面（委托给同目录的 cbfont_ui.py）。"""
    spec = importlib.util.spec_from_file_location(
        "cbfont_ui", Path(__file__).with_name("cbfont_ui.py")
    )
    if spec is None or spec.loader is None:
        raise SystemExit("找不到 tools/cbfont_ui.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["cbfont_ui"] = module
    spec.loader.exec_module(module)
    argv = ["--host", args.host, "--port", str(args.port)]
    if args.no_browser:
        argv.append("--no-browser")
    return module.main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cbfont",
        description="把 TTF 渲染成 CBMF v2 点阵字库（支持多编码与任意语言）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-f", "--font", help="TTF/TTC 字体路径（缺省自动探测）")
        p.add_argument("--font-index", type=int, default=0, help="TTC 中的字体序号")
        p.add_argument("-s", "--size", type=int, default=16, help="点阵尺寸（默认 16）")

    build = sub.add_parser("build", help="生成字库")
    build.add_argument("-c", "--charset", default="gb2312",
                       help="gb2312/gbk/big5/shift-jis/euc-kr/latin-1/ascii/lang:XX/text:FILE/range:SPEC")
    build.add_argument("-o", "--output", help="输出路径")
    build.add_argument("--format", choices=("bin", "c"), default="bin")
    build.add_argument("--no-ascii", action="store_true", help="不附加 ASCII 字模")
    build.add_argument("--fill-missing", action="store_true",
                       help="索引模式下保留字体缺失字符的空白字模（默认跳过）")
    build.add_argument("--layout", choices=LAYOUTS, default="auto",
                       help="UTF-8 布局：auto 选更小的，indexed 强制索引，dense 强制 BMP 密集")
    build.add_argument("--text-encoding", default="utf-8", help="text:FILE 的文本编码")
    build.add_argument("--threshold", type=int, default=128)
    build.add_argument("--x-offset", type=int, default=0)
    build.add_argument("--y-offset", type=int, default=0)
    build.add_argument("--fit", action="store_true")
    build.add_argument("--invert", action="store_true")
    build.add_argument("--lsb-first", action="store_true")
    build.add_argument("--strict", action="store_true", help="字体缺字时失败")
    add_common(build)
    build.set_defaults(func=cmd_build)

    info = sub.add_parser("info", help="查看字库信息")
    info.add_argument("file")
    info.set_defaults(func=cmd_info)

    preview = sub.add_parser("preview", help="在终端预览字形")
    preview.add_argument("file")
    preview.add_argument("text")
    preview.set_defaults(func=cmd_preview)

    chars = sub.add_parser("chars", help="查看字符集统计/缺字情况")
    chars.add_argument("-c", "--charset", default="gb2312")
    chars.add_argument("--no-ascii", action="store_true")
    chars.add_argument("--layout", choices=LAYOUTS, default="auto")
    chars.add_argument("--text-encoding", default="utf-8")
    add_common(chars)
    chars.set_defaults(func=cmd_chars)

    ui = sub.add_parser("ui", help="打开 Web 配置界面")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    ui.set_defaults(func=cmd_ui, size=16)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:  # pragma: no cover
        pass
    args = build_parser().parse_args(argv)
    if not 1 <= getattr(args, "size", 16) <= 255:
        raise SystemExit("--size 必须在 1-255 之间")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
