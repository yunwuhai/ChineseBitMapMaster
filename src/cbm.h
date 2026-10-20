/*************************************************************************************
 * @Description  : CBMF v2 点阵字库运行时（嵌入式可直接移植）
 * @Version      : 2.1.0
 * @Author       : YunWuHai
 * @Copyright (C) 2021-2026 YunWuHai
 * SPDX-License-Identifier: MIT
 *
 * 字库由 tools/cbfont.py 生成。文件布局（全部小端）：
 *     [48 字节头][区间表 12*N][码点索引（仅 UTF-8 模式）][字模数据]
 *
 * 设计目标：
 *   - 零 malloc、零 stdio（读写通过 cbm_reader 回调）；
 *   - 与字节序无关（逐字节解析）；
 *   - 支持 GB2312 / GBK / Big5 / Shift-JIS / EUC-KR / 单字节 / UTF-8。
 *************************************************************************************/
#ifndef CBM_H
#define CBM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CBM_HEADER_SIZE   48
#define CBM_RANGE_SIZE    12

/* 区间数组容量：可在编译时用 -DCBM_MAX_RANGES=N 调整（每项占 8 字节 RAM） */
#ifndef CBM_MAX_RANGES
#define CBM_MAX_RANGES    8
#endif

/* 状态码 */
typedef enum
{
    CBM_OK = 0,
    CBM_ERR_ARG,
    CBM_ERR_IO,
    CBM_ERR_FORMAT,
    CBM_ERR_NOTFOUND,
    CBM_ERR_CRC,
    CBM_ERR_BUFFER
} cbm_status;

/* 编码类型 */
typedef enum
{
    CBM_ENC_SINGLE    = 0,  /* 单字节（Latin-1 / ASCII） */
    CBM_ENC_GB2312    = 1,
    CBM_ENC_GBK       = 2,
    CBM_ENC_BIG5      = 3,
    CBM_ENC_SHIFT_JIS = 4,
    CBM_ENC_EUC_KR    = 5,
    CBM_ENC_UTF8      = 6
} cbm_encoding;

/* 文件标志 */
#define CBM_FLAG_LSB_FIRST 0x01u
#define CBM_FLAG_INVERTED  0x02u
#define CBM_FLAG_INDEXED   0x04u
#define CBM_FLAG_INDEX_U16 0x08u

/* 随机读回调：返回 0 表示成功 */
typedef int (*cbm_read_fn)(void *ctx, uint32_t offset, void *buf, uint32_t len);

typedef struct
{
    void *ctx;
    cbm_read_fn read;
} cbm_reader;

/* 密集区间：idx = glyph_offset + (hi-hi_min)*宽 + (lo-lo_min)
 * hi_min == hi_max == 0 表示单字节区间，key 即 lo。 */
typedef struct
{
    uint8_t  hi_min;
    uint8_t  hi_max;
    uint8_t  lo_min;
    uint8_t  lo_max;
    uint32_t glyph_offset;
} cbm_range;

typedef struct
{
    uint8_t  version;
    uint8_t  flags;
    uint8_t  width;
    uint8_t  height;
    uint8_t  encoding;
    uint8_t  range_count;
    uint16_t bytes_per_glyph;
    uint32_t glyph_count;
    uint32_t data_offset;
    uint32_t index_offset;
    uint32_t index_count;
    uint32_t crc32;
    uint32_t file_size;
    cbm_range ranges[CBM_MAX_RANGES];
} cbm_font;

/* 读取并校验文件头（同时读入区间表） */
cbm_status cbm_open(cbm_font *font, const cbm_reader *reader);

/* 从字节流中取出一个编码 key：
 *   UTF-8 -> Unicode 码点；DBCS -> (高位<<8)|低位；单字节 -> 字节本身。
 * consumed 返回消耗的字节数（1/2/3/4）。 */
cbm_status cbm_next(const cbm_font *font, const uint8_t *text, uint32_t len,
                    uint32_t *key, uint32_t *consumed);

/* 编码 key -> 字模下标 */
cbm_status cbm_lookup(const cbm_font *font, const cbm_reader *reader,
                      uint32_t key, uint32_t *glyph_index);

/* 读取一个字模（buf_size 必须 >= bytes_per_glyph） */
cbm_status cbm_read_glyph(const cbm_font *font, const cbm_reader *reader,
                          uint32_t index, uint8_t *buf, uint32_t buf_size);

/* 校验 CRC32（可用 -DCBM_NO_VERIFY 裁掉以减小体积） */
#ifndef CBM_NO_VERIFY
cbm_status cbm_verify(const cbm_font *font, const cbm_reader *reader);
#endif

#ifndef CBM_NO_STRERROR
const char *cbm_strerror(cbm_status status);
#endif

/* 把一段内存（如 XIP Flash 中的 const 数组）当作字库读取 */
static inline int cbm_read_mem(void *ctx, uint32_t offset, void *buf, uint32_t len)
{
    const uint8_t *base = (const uint8_t *)ctx;
    uint8_t *out = (uint8_t *)buf;
    uint32_t i;

    if(base == 0 || out == 0)
    {
        return -1;
    }
    for(i = 0; i < len; i++)
    {
        out[i] = base[offset + i];
    }
    return 0;
}

#ifdef __cplusplus
}
#endif

#endif /* CBM_H */
