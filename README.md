# ChineseBitmap

把 TrueType 字体渲染成 **自描述点阵字库**，并提供一个可在 MCU 上运行的极小 C 运行时：
字库放在 SD 卡或外部 Flash，运行时按编码直接寻址，每显示一个字只读一个定长字模
（16×16 时 32 字节），不需要把整本字库塞进 RAM。

```text
TTF ──tools/cbfont.py──► gb2312.cbm ──► SD 卡 / SPI Flash / XIP Flash
                                            │
                    cbm.c: 解码 → 查下标 → 读字模 → 画点
```

**可选能力**：

- **多编码**：GB2312 / GBK / Big5 / Shift-JIS / EUC-KR / Latin-1 / ASCII / UTF-8
- **任意语言**：语言预设（中/繁/日/韩/俄/希/希伯来/阿拉伯/泰/印地/越南…）、
  自定义 Unicode 区段、任意 UTF-8 文本文件
- **可调布局**：UTF-8 可选 BMP 密集（查找零 I/O，简体全字集反而更小）或码点索引，
  `auto` 自动选更小的
- **Web 配置界面**：选字体、选字符集、调参数、实时预览、一键生成；
  每个选项带 `?` 说明和点阵前后对比

## 快速开始

```bash
pip install -r tools/requirements.txt          # 只需要 Pillow

# 简体中文（符号 + 汉字 + ASCII）
python tools/cbfont.py build -c gb2312 -s 16 -o build/gb2312.cbm

# 简体全字集：auto 会自动选更小、更快的 BMP 密集布局
python tools/cbfont.py build -c lang:zh-Hans -f C:/Windows/Fonts/simsun.ttc -o build/zh.cbm

# 日文，使用 MS Gothic
python tools/cbfont.py build -c lang:ja -f C:/Windows/Fonts/msgothic.ttc -s 16 -o build/ja.cbm

# 只收录某个文本文件里出现的字符（示例文件是 GB2312 编码的《兰亭集序》）
python tools/cbfont.py build -c text:example/word.txt --text-encoding gb2312 \
       -f C:/Windows/Fonts/simsun.ttc -o build/words.cbm

# 预览
python tools/cbfont.py preview build/gb2312.cbm "永和九年"
```

图形化操作：

```bash
python tools/cbfont.py ui            # 自动打开 http://127.0.0.1:8765
```

界面里可以选字体、选编码或语言、调字号/阈值/偏移、选 UTF-8 布局、看实时预览
（绿框=正常，黄框=字体缺字，红框=不在字符集），然后一键生成并下载 `.cbm`。
每个选项旁的 `?` 会说明用途；阈值、尺寸、偏移、居中、反色还会用当前字体渲染
“修改前 / 修改后”的点阵对比，方便判断参数该往哪边调。

## 支持的编码与字符集

| `--charset` | 编码 | 查找模式 | 内容 |
|---|---|---|---|
| `gb2312` | GB2312 | 密集区间 | 682 符号 + 6763 汉字 + ASCII（共 7709 个槽位） |
| `gbk` | GBK | 密集区间 | GB2312 + 扩展汉字 + ASCII |
| `big5` | Big5 | 密集区间 | 繁体中文 + ASCII |
| `shift-jis` | Shift-JIS | 密集区间 | 日文 + 半角片假名 + ASCII |
| `euc-kr` | EUC-KR | 密集区间 | 韩文 + ASCII |
| `latin-1` | Latin-1 | 密集区间 | 西欧字符 |
| `ascii` | ASCII | 密集区间 | 0x20–0x7E |
| `lang:XX` | UTF-8 | BMP 密集 / 索引 | 语言预设，自带 ASCII |
| `text:FILE` | UTF-8 | BMP 密集 / 索引 | 只收录文件里出现的字符 |
| `range:U+4E00-U+9FFF` | UTF-8 | BMP 密集 / 索引 | 自定义 Unicode 区段 |

语言预设：`zh-Hans` `zh-Hant` `ja` `ko` `ru` `el` `he` `ar` `th` `hi` `latin` `vi`。

> 密集编码按矩形区块连续排布，GB2312 的符号区/汉字区里未分配的码位会保留为空白槽位，
> 所以文件大小按 7709 个槽位计算，而不是按 7540 个实际字符计算。

> **缺字处理**：UTF-8 索引模式默认直接跳过字体里没有的字符，不会浪费空间；
> 加 `--fill-missing` 可保留空白字模。密集布局（DBCS 字符集、`--layout dense`）
> 必须保留槽位，缺字一律输出空白并告警。`--strict` 可让任何缺字直接导致构建失败。

## 布局：更快还是更小

UTF-8 字符集（`lang:` / `text:` / `range:`）支持两种布局，用 `--layout` 选择：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 构建时比较“跳过缺字后的索引体积”和“BMP 密集槽位体积”，选小的；简体中文全字集通常选密集 |
| `indexed` | 码点索引，二分查找；适合稀疏字符、`text:` 和小字符集 |
| `dense` | BMP 密集区间，查找零 I/O；仅支持 U+0000–U+FFFF，文件可能更大 |

16×16 实测（SimSun / MS Gothic）：

| 计划 | 索引 | 密集 | `auto` 结果 |
|---|---:|---:|---|
| `lang:zh-Hans` | 721,596 B | 684,544 B | **密集**（省 37 KB，索引读 0 次） |
| `lang:ja`（MS Gothic） | 445,482 B | 691,212 B | **索引**（密集大 55%） |
| `range:U+4E00-U+9FFF`（SimSun） | 713,776 B | 671,804 B | **密集** |

密集布局的 key 就是 Unicode 码点，C 侧仍按 `(key >> 8, key & 0xFF)` 寻址，
因此只支持 BMP；需要 emoji 等非 BMP 字符时必须用索引模式。

> **关于复杂文字**：阿拉伯文、天城文、泰文需要字形整形（连写/重排），本项目只做
> 单字模渲染，输出的是孤立字形；排版顺序也需要调用方处理。拉丁、西里尔、希腊、
> 中日韩、韩文音节等不需要整形的文字可以直接使用。

## 字库格式 CBMF v2

```text
[48 字节头][区间表 12×N][码点索引（仅 UTF-8 索引模式）][字模数据]
```

头部字段（小端）：

| 偏移 | 大小 | 字段 |
|---|---|---|
| 0 | 4 | magic `CBMF` |
| 4 | 1 | version = 2 |
| 5 | 1 | flags（bit0 低位在前 / bit1 反色 / bit2 索引模式 / bit3 索引项 16 位） |
| 6 | 1 | width |
| 7 | 1 | height |
| 8 | 2 | bytes_per_glyph |
| 10 | 4 | glyph_count |
| 14 | 1 | encoding |
| 15 | 1 | range_count |
| 16 | 4 | data_offset |
| 20 | 4 | index_offset |
| 24 | 4 | index_count |
| 28 | 4 | crc32（覆盖头部之后全部数据） |
| 32 | 4 | file_size |
| 36 | 12 | 保留 |

完整字节级说明见 [docs/FORMAT.md](docs/FORMAT.md)。

查找规则：

- **密集区间**：`idx = glyph_offset + (hi - hi_min) * (lo_max - lo_min + 1) + (lo - lo_min)`，
  单字节区间 `hi_min = hi_max = 0`，此时 `key` 就是字节本身。UTF-8 密集布局的 `key`
  是 Unicode 码点（仅 BMP），同样套用这个公式。
- **码点索引**：升序数组二分查找，命中位置即字模下标。
- 字模地址：`data_offset + idx * bytes_per_glyph`，行主序、默认 MSB-first。

## 在单片机工程里使用

把 `src/cbm.c`、`src/cbm.h` 加入工程。核心不依赖 `malloc`/`stdio`，只依赖 `stdint.h`；
随机读由一个回调提供，移植到 FatFS、SPI Flash 或 XIP 都很简单。

```c
#include "cbm.h"

cbm_reader reader = { flash_base, cbm_read_mem };   /* XIP Flash 直接读 */
/* FatFS/stdio: 用 cbm_stdio_read，见 src/cbm_stdio.c */
cbm_font font;
uint8_t glyph[128];

if (cbm_open(&font, &reader) != CBM_OK) { /* 处理错误 */ }

uint32_t pos = 0;
while (pos < text_len)
{
    uint32_t key, consumed, index;
    if (cbm_next(&font, text + pos, text_len - pos, &key, &consumed) != CBM_OK) { pos++; continue; }
    pos += consumed;
    if (cbm_lookup(&font, &reader, key, &index) != CBM_OK) { continue; }
    if (cbm_read_glyph(&font, &reader, index, glyph, sizeof(glyph)) != CBM_OK) { continue; }
    DrawGlyph(glyph, font.width, font.height);
}
```

`cbm_next` 负责按编码切分字节流（UTF-8 1–4 字节、DBCS 1–2 字节、单字节 1 字节）；
`cbm_lookup` 密集模式只做算术、零 I/O，索引模式做 `log2(N)` 次小读取；
`cbm_verify` 可选的 CRC 自检。

**体积**：宿主 x86-64、`-Os` 下 `cbm.c` 约 2.5 KB，`cbm_font` 96 字节。
可用编译开关进一步裁剪：

- `-DCBM_MAX_RANGES=4|8|16`：区间数组容量（每项 8 字节 RAM）；
- `-DCBM_NO_VERIFY`：去掉 `cbm_verify`；
- `-DCBM_NO_STRERROR`：去掉 `cbm_strerror`。

后两个一起实测约 2.0 KB（1,970 B），再配合 `-ffunction-sections -Wl,--gc-sections`
还能丢掉未引用的函数。

## Web 配置界面

```bash
python tools/cbfont.py ui [--port 8791] [--no-browser]
```

- 默认监听 `127.0.0.1:8765`；端口被占用或落在系统保留区时会自动顺延，以终端输出的 URL 为准；
- 字体下拉读取字体 `name` 表显示官方名称，TTC 的每个 face 单独列出，也可手填自定义路径；
- 每个选项旁的 `?` 打开说明面板；阈值、尺寸、偏移、居中、反色会按当前字体
  渲染“修改前 / 修改后”的对比图；
- 字符集提示实时显示码位数、区间数与体积估算，修改字符集、尺寸、布局、字体
  或预览文字后自动刷新预览；
- 预览图直接渲染所选字体/字号/阈值的真实位图；
- 可选择 `auto/indexed/dense` 布局，并显示两种布局的体积预估；
- 生成的文件写入 `build/ui/`，页面提供下载链接。

## 构建与测试

```bash
pip install pytest
make fonts     # 生成示例字库（example/gb2312.cbm）
make test      # pytest + C 测试
make demo      # 编译并运行终端示例
make fixtures  # 重新生成 C 测试用的合成字库
```

C 部分需要 `gcc`/`cc` 与 `make`（Windows 可用 WSL）。
CI 会在 Linux 上跑 Python 3.10/3.12/3.13 测试，并用
`-fsanitize=address,undefined` 跑 C 测试。

## 已知限制

- 只有 1bpp 定长点阵，没有灰度、抗锯齿、比例字宽、字距调整。
- 复杂文字不做整形（见上文）。
- 字库尺寸/位序写在文件头里，C 侧会读取；但显示方向（横/竖屏、镜像）仍需调用方处理。
- UTF-8 密集布局仅支持 BMP（U+0000–U+FFFF）；emoji 等非 BMP 字符必须用索引模式。
- 索引模式每次查找约 `log2(N)` 次小读取；对 SD 卡/SPI Flash 较明显，CJK 全字集
  建议用 `--layout dense` 消除这部分 I/O。
- 密集模式缺字输出空白并告警；索引模式默认跳过缺失字符（`--fill-missing` 保留空白），
  `--strict` 可让任何缺字导致构建失败。

## 从 1.x 升级

1.x 的裸 `.GB` 格式和 `FindCode.h` API 已被 CBMF v2 与 `cbm.h` 取代，详见
[CHANGELOG.md](CHANGELOG.md)。重新生成字库即可：

```bash
python tools/cbfont.py build -c gb2312 -s 16 -o example/gb2312.cbm
```

## 许可与第三方资源

- 本项目代码（`src/`、`tools/`、`tests/`、`example/` 中的 C 代码）采用 **MIT**，
  见 [LICENSE](LICENSE)。源码文件带 `SPDX-License-Identifier: MIT` 标识。
- **生成的字库不属于本项目**：`.cbm` 是所选字体的光栅化结果，其授权跟随原字体
  （例如 SimSun、MS Gothic 等商业字体通常不允许再分发字库文件）。仓库不附带字体，
  也不提交生成的字库；对外分发前请先确认字体许可。
- `example/word.txt` 是公有领域的《兰亭集序》（王羲之，东晋），仅用于演示。
- 依赖：[Pillow](https://python-pillow.org/)（HPND 许可）、
  [pytest](https://pytest.org/)（MIT）；CI 使用 GitHub Actions。
