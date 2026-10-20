# CBMF v2 字库格式

本文件是 `tools/cbfont.py`、`src/cbm.c` 与所有读取方之间的契约。
所有多字节数值均为 **小端**。文件由四部分组成：

```text
[48 字节头][区间表 12×N][码点索引（仅 UTF-8 模式）][字模数据]
```

## 头部（48 字节）

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 4 | char[4] | magic | 固定为 `CBMF` |
| 4 | 1 | u8 | version | 当前为 2 |
| 5 | 1 | u8 | flags | bit0 LSB-first；bit1 反色；bit2 索引模式；bit3 索引项为 u16 |
| 6 | 1 | u8 | width | 单元格宽（像素），1–255 |
| 7 | 1 | u8 | height | 单元格高（像素），1–255 |
| 8 | 2 | u16 | bytes_per_glyph | 必须等于 `ceil(width/8) * height` |
| 10 | 4 | u32 | glyph_count | 字模总数 |
| 14 | 1 | u8 | encoding | 0 单字节 / 1 GB2312 / 2 GBK / 3 Big5 / 4 Shift-JIS / 5 EUC-KR / 6 UTF-8 |
| 15 | 1 | u8 | range_count | 密集区间数；索引模式必须为 0，最大 8 |
| 16 | 4 | u32 | data_offset | 字模数据起始偏移 |
| 20 | 4 | u32 | index_offset | 码点索引起始偏移（密集模式为 0） |
| 24 | 4 | u32 | index_count | 索引项数（密集模式为 0） |
| 28 | 4 | u32 | crc32 | 覆盖 `[48, file_size)` 的 CRC-32/ISO-HDLC |
| 32 | 4 | u32 | file_size | 整个文件长度 |
| 36 | 12 | u8[12] | reserved | 写入方置 0；读取方必须忽略 |

`flags` 的 bit4–bit7 未定义：写入方置 0，读取方不得据此报错。

布局关系：

```text
data_offset  = 48 + 12 * range_count + (索引模式 ? 2 或 4 * index_count : 0)
index_offset = 48 + 12 * range_count          （仅索引模式，否则为 0）
file_size    = data_offset + glyph_count * bytes_per_glyph
```

读取方至少应校验：`version == 2`、宽高与 `bytes_per_glyph` 自洽、
`data_offset` 不越界、`glyph_count * bytes_per_glyph` 不超过文件尾、
索引模式下 `index_count == glyph_count`、密集模式下 `index_offset == index_count == 0`。

## 区间表（每项 12 字节）

| 偏移 | 大小 | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 1 | u8 | hi_min |
| 1 | 1 | u8 | hi_max |
| 2 | 1 | u8 | lo_min |
| 3 | 1 | u8 | lo_max |
| 4 | 4 | u32 | glyph_offset |
| 8 | 4 | u32 | reserved（0） |

查找：

```c
if (hi_min == 0 && hi_max == 0) {
    /* 单字节区间：key 就是字节 */
    idx = glyph_offset + (key - lo_min);
} else {
    hi = key >> 8; lo = key & 0xFF;
    idx = glyph_offset + (hi - hi_min) * (lo_max - lo_min + 1) + (lo - lo_min);
}
```

区间按文件中的顺序排列，每个区间在字模数据里是一块连续、按 `hi` 外层 `lo` 内层
排布的矩形。不同区间不应重叠；`glyph_offset` 从 0 开始且相邻区间首尾相接。
C 运行时的区间数组默认上限为 **8**（`CBM_MAX_RANGES`，可用 `-DCBM_MAX_RANGES=N` 调整），
生成器超过上限会直接报错。

例如 GB2312：

| 区间 | hi 范围 | lo 范围 | glyph_offset | 槽位数 |
|---|---|---|---|---:|
| 符号 | 0xA1–0xA9 | 0xA1–0xFE | 0 | 846 |
| 汉字 | 0xB0–0xF7 | 0xA1–0xFE | 846 | 6768 |
| ASCII | 0x00–0x00 | 0x20–0x7E | 7614 | 95 |

UTF-8 也可以使用密集布局：`encoding = 6`、`flags.bit2 = 0`、`range_count > 0`。
此时 `key` 是 Unicode 码点，只支持 **BMP**（U+0000–U+FFFF），同样套用上面的
`hi = key >> 8, lo = key & 0xFF` 公式。非 BMP 字符（如 emoji）必须使用索引模式。

## 码点索引（仅 UTF-8 索引模式）

`index_count` 个**严格升序**的 Unicode 码点，每项 2 字节（`flags.bit3 = 1`，要求
所有码点 ≤ 0xFFFF）或 4 字节。第 `i` 项命中时，字模下标就是 `i`，因此
`index_count` 必须等于 `glyph_count`，索引项与字模一一对应。

## 字模数据

每个字模 `bytes_per_glyph` 字节：

- 行主序：第 `y` 行占用 `[y*stride, y*stride+stride)`，`stride = ceil(width/8)`；
- 默认 MSB-first：最左像素在最高位；`flags.bit0 = 1` 时改为 LSB-first；
- `flags.bit1 = 1` 表示存储的是反色位图：正常模式 1 = 前景，反色模式 1 = 背景。

## 编码的字节切分

`cbm_next` 的规则：

- `encoding = 6`（UTF-8）：按 UTF-8 解码为码点，与密集/索引布局无关；拒绝过长编码、代理区与越界码点；
- `encoding = 0`（单字节）：每次 1 字节；
- 其他 DBCS：ASCII（`b0 < 0x80`）一律按 1 字节；其余字节若落在某个区间的
  `[hi_min, hi_max]` 内则取 2 字节（`key = (b0 << 8) | b1`），否则按 1 字节处理。
  Shift-JIS 的半角片假名 0xA1–0xDF 是单字节区间，因此不会被误判为双字节前导；
  用户定义区 0xF0–0xFC 不在标准字符集内，生成器未收录，遇到时按单字节处理并查不到字形。

## CRC

`crc32` 是 CRC-32/ISO-HDLC（与 `zlib.crc32` 相同：反射多项式 `0xEDB88320`，
初值 `0xFFFFFFFF`，结果异或 `0xFFFFFFFF`），输入为 `[48, file_size)` 的全部字节。
`cbm_verify` 按 128 字节分块计算，因此适合从 SD 卡/SPI Flash 流式校验。

## 兼容性

- 1.x 的裸 `.GB` 文件（无头、固定 16×16、GB2312 区位顺序）**不再支持**；
  用 `python tools/cbfont.py build -c gb2312 ...` 重新生成即可。
- 头部保留字段与 `flags` 高位为将来扩展预留，读取方必须忽略其内容。
- 同一 `version` 内不会改变已有字段的含义；新增字段只会占用保留区。
