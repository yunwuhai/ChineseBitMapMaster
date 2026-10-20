# Changelog

## Unreleased

### 新增

- Web UI 每个选项增加 `?` 说明面板：用途、默认值、约束；阈值 / 尺寸 / 偏移 /
  居中 / 反色会按当前字体实时渲染“修改前 / 修改后”的点阵对比图。
- 字体下拉显示字体 `name` 表中的官方名称，TTC 字体集合展开为多个 face
  （如宋体 / 新宋体），并保留自定义路径入口。
- 字符集提示实时显示码位数、区间数与体积估算；修改字符集、尺寸、布局、字体
  或预览文字后自动刷新预览。
- 预览文字与字符集完全不相交时（例如自定义 `text:` 或 emoji 字符集），自动
  改为展示字符集自身的字形，避免整屏红框。

### 变更

- Web UI 默认预览文字改为“你好，世界 Hello 点阵字库”，避免默认 GB2312 下
  出现大量红框；日文 / 韩文示例可在对应语言字符集下查看。

## 2.1.0 — 2026-10-19

### 性能

- **UTF-8 BMP 密集布局**：`--layout dense` 把 UTF-8 字符集写成密集区间，查找零 I/O；
  简体全字集比索引模式小 37 KB（721,596 → 684,544 B）。
- `--layout auto`（默认）在构建时按**实际缺字**比较两种布局体积，自动选小的：
  `lang:zh-Hans` 选密集；`lang:ja`（MS Gothic 缺字多）仍选索引，避免误选变大。
- 索引查找少读一次：二分循环里记住候选值，不再为确认命中额外读一次。
- C 运行时支持编译期裁剪：`CBM_MAX_RANGES`、`CBM_NO_VERIFY`、`CBM_NO_STRERROR`；
  `-Os` 下 2,524 B，后两者关闭后 1,970 B（x86-64，未开 gc-sections）。
- Web UI 增加布局选择、索引/密集体积预估、缺字黄框。

### 变更

- `build` / `chars` 新增 `--layout auto|indexed|dense`。
- 测试增加密集 UTF-8 夹具、C 侧读次数断言与布局选择用例（Python 59 项）。

### 兼容性

- 文件格式仍是 CBMF v2；2.0.0 生成的索引字库无需重新生成。
- `--layout` 只影响新生成的文件；UTF-8 密集布局仅支持 BMP。

## 2.0.0 — 2026-10-19

### 新增

- **CBMF v2 自描述格式**：48 字节头 + 区间表 + 可选码点索引 + CRC32，字段说明见
  [docs/FORMAT.md](docs/FORMAT.md)。
- **多编码**：GB2312 / GBK / Big5 / Shift-JIS / EUC-KR / Latin-1 / ASCII；
  `gb2312` 现在把符号区、汉字区和 ASCII 合并进一个文件。
- **任意语言（UTF-8 索引模式）**：`lang:XX` 预设（简繁中文、日、韩、俄、希腊、
  希伯来、阿拉伯、泰、印地、拉丁、越南）、`text:FILE`、`range:U+XXXX-U+YYYY`。
- **Web 配置界面**：`python tools/cbfont.py ui`，支持选字体、选编码/语言、调参数、
  实时预览和下载。
- **C 运行时 `cbm.h` / `cbm.c`**：支持全部编码、区间与索引两种查找、CRC 校验、
  回调式随机读（`cbm_read_mem` 可直接读 XIP Flash），核心仍无 `malloc`/`stdio`。
- **格式一致性校验**：`cbm_open` 会校验 `bytes_per_glyph`、`data_offset`、索引区边界
  与区间上限（8）；`FontFile` 读取时做同样的检查。规范见 [docs/FORMAT.md](docs/FORMAT.md)。
- 测试：Python 46 项、C 单元测试（含畸形头部用例），以及跨语言的合成字库夹具。

### 变更

- `-c hanzi` / `-c symbols` 合并为 `-c gb2312`；生成一个文件即可同时包含符号、汉字和 ASCII。
- 默认输出扩展名改为 `.cbm`，示例改为 `example/gb2312.cbm`。
- 16×16 GB2312 文件从 243,648 字节变为 246,772 字节（多了 ASCII 95 字、头部与区间表）。
- 字模尺寸、位序、反色等信息写入文件头，C 侧无需再用宏手工对齐。

### 移除

- 1.x 裸 `.GB` 格式与 `src/FindCode.c/.h`；由 `src/cbm.h` 取代。
- 旧的 `tests/test_findcode.c`。
- `tool/` 下的两段式手工流程与 1.6MB 的第三方字模软件压缩包。
- `.vscode/`、旧 `.GB`/`.bin` 的 ignore 规则，以及重复的编码转换函数。

### 兼容性

- **破坏性变更**：C API 从 `GetCodeType/GetID_GB_*` 改为 `cbm_open/cbm_next/cbm_lookup/
  cbm_read_glyph`；旧字库文件无法直接读取，需要重新生成。
- 保留的设计：1bpp 定长点阵、按需单字读取、无索引表的 O(1) 密集查找。

### 许可与清理

- 所有源码统一加上 `SPDX-License-Identifier: MIT`，与 [LICENSE](LICENSE) 一致。
- 示例文本 `example/word.txt` 换成公有领域的《兰亭集序》（GB2312 编码），
  避免示例内容涉及第三方歌词的授权不确定问题。
- README 增加“许可与第三方资源”：生成的字库授权跟随原字体，仓库不分发字体或字库。

### 修复（延续 1.0.0）

- GB2312 未分配码仍输出空白；`0xD7FA–0xD7FE` 的垃圾字模问题不再出现。
- 字体缺字会汇总告警，`--strict` 可让构建失败。

## 1.0.0 — 2026-10-19

### 移除

- 删除 `tool/CreateBitMap/Create.py`、`tool/MachingFile/Maching.py` 以及两段式手工流程。
- 删除 `tool/CreateBitMap/字模软件.zip`（1.6MB 的 PCtoLCD2002 第三方 GUI 工具，Windows-only、许可证不明）。
- 删除 `.idea/`、`.vscode/` 等 IDE 配置与旧版生成的字库文件。

### 新增

- `tools/cbfont.py`：直接由 TTF 生成字库，支持 `build / preview / info / chars` 四个子命令，
  可配置尺寸、阈值、偏移、居中、反色、位序，并可在缺字时以 `--strict` 失败。
- `tests/test_cbfont.py`、`tests/test_findcode.c`、`Makefile`、GitHub Actions CI。
- README 中的选型对照（u8g2 / LVGL / mcufont），说明本项目只负责「外部存储 + 极小运行时」这个场景。

### 修复

- `GetID_GB_Ch` / `GetID_GB_Punc`：区号或位号越界时返回 `-1`。
  旧实现把 `id` 初始化为 `-1` 后仍无条件执行 `id += bitOffset`，会让非法编码得到一个
  **合法但错误** 的下标，从而读到别的字的字模。
- `CodeType` 不再依赖 C 语言中「位域分配顺序由实现定义」的未定义行为。
- 示例程序：ASCII 分支不再打印编码数字；越界下标不再 `fseek` 到错误位置；
  改用 `fgetc` 逐字节读取，文件末尾的 ASCII 字符不会再被丢掉。

### 字库数据修正

用新生成器（SimSun 16px、灰度阈值 128、原点 0,0）与旧字库逐字节比对：

- **汉字 6768 个**：仅 **5 个** 不同，正好是 `0xD7FA`–`0xD7FE` 这 5 个 GB2312 未分配码。
  旧流程中它们被文本工具替换成 `??`，字模软件输出 10 行 `?` 占位，转换脚本又盲目
  两行一字，压成了 5 个「双盒」垃圾字模；现在正确输出空白。
- **符号 846 个**：48 个不同，其中 47 个是 GB2312 未分配槽位（旧为 `?` 占位），
  `0xA1A4`（`・`）SimSun 缺字输出空白，`0xA1AA`（`―`）笔画位置有细微差异。
