/*************************************************************************************
 * @Description  : CBMF v2 运行时的单元测试（使用合成夹具，不依赖真实字体）
 * 编译运行：make test-c
 * SPDX-License-Identifier: MIT
 *************************************************************************************/
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/cbm.h"
#include "../src/cbm_stdio.h"

#define DENSE_PATH   "tests/fixtures/dense.cbm"
#define INDEXED_PATH "tests/fixtures/indexed.cbm"
#define DENSE_UTF8_PATH "tests/fixtures/dense_utf8.cbm"

static void open_file(cbm_font *font, cbm_stdio_ctx *stream, cbm_reader *reader,
                      FILE **fp, const char *path)
{
    *fp = fopen(path, "rb");
    assert(*fp != NULL);
    stream->fp = *fp;
    reader->ctx = stream;
    reader->read = cbm_stdio_read;
    assert(cbm_open(font, reader) == CBM_OK);
}

typedef struct
{
    const cbm_reader *inner;
    uint32_t reads;
} counting_reader;

static int counting_read(void *ctx, uint32_t offset, void *buf, uint32_t len)
{
    counting_reader *counter = (counting_reader *)ctx;
    counter->reads++;
    return counter->inner->read(counter->inner->ctx, offset, buf, len);
}

static void test_dense_header(void)
{
    cbm_font font;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    FILE *fp;

    open_file(&font, &stream, &reader, &fp, DENSE_PATH);

    assert(font.version == 2);
    assert(font.width == 16 && font.height == 16);
    assert(font.bytes_per_glyph == 32);
    assert(font.glyph_count == 13);
    assert(font.encoding == CBM_ENC_GB2312);
    assert(font.range_count == 3);
    assert(font.ranges[0].hi_min == 0xA1 && font.ranges[0].hi_max == 0xA2);
    assert(font.ranges[0].glyph_offset == 0);
    assert(font.ranges[1].glyph_offset == 4);
    assert(font.ranges[2].hi_max == 0 && font.ranges[2].lo_min == 0x41);
    assert(cbm_verify(&font, &reader) == CBM_OK);

    fclose(fp);
}

static void test_dense_lookup(void)
{
    cbm_font font;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    FILE *fp;
    uint32_t index;

    open_file(&font, &stream, &reader, &fp, DENSE_PATH);

    assert(cbm_lookup(&font, &reader, 0xA1A1u, &index) == CBM_OK && index == 0);
    assert(cbm_lookup(&font, &reader, 0xA2A2u, &index) == CBM_OK && index == 3);
    assert(cbm_lookup(&font, &reader, 0xB0A1u, &index) == CBM_OK && index == 4);
    assert(cbm_lookup(&font, &reader, 0xB1A3u, &index) == CBM_OK && index == 9);
    assert(cbm_lookup(&font, &reader, 0x41u, &index) == CBM_OK && index == 10);
    assert(cbm_lookup(&font, &reader, 0x43u, &index) == CBM_OK && index == 12);

    assert(cbm_lookup(&font, &reader, 0x44u, &index) == CBM_ERR_NOTFOUND);
    assert(cbm_lookup(&font, &reader, 0xC0A1u, &index) == CBM_ERR_NOTFOUND);
    assert(cbm_lookup(&font, &reader, 0xA2A3u, &index) == CBM_ERR_NOTFOUND);

    fclose(fp);
}

static void test_dense_glyph(void)
{
    cbm_font font;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    FILE *fp;
    uint8_t glyph[32];
    uint8_t small[16];
    int j;

    open_file(&font, &stream, &reader, &fp, DENSE_PATH);

    assert(cbm_read_glyph(&font, &reader, 5, glyph, sizeof(glyph)) == CBM_OK);
    for(j = 0; j < 32; j++)
    {
        assert(glyph[j] == (uint8_t)((5 * 7 + j * 3) & 0xFF));
    }

    /* 缓冲区不足应报错 */
    assert(cbm_read_glyph(&font, &reader, 5, small, sizeof(small)) == CBM_ERR_BUFFER);
    /* 下标越界 */
    assert(cbm_read_glyph(&font, &reader, 13, glyph, sizeof(glyph)) == CBM_ERR_NOTFOUND);

    fclose(fp);
}

static void test_dense_next(void)
{
    cbm_font font;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    FILE *fp;
    uint32_t key = 0;
    uint32_t consumed = 0;
    const uint8_t gb[] = {0xB0, 0xA1};
    const uint8_t ascii[] = {'A'};
    const uint8_t lead_only[] = {0xB0};
    const uint8_t bad_utf8[] = {0xE4, 0xB8};

    open_file(&font, &stream, &reader, &fp, DENSE_PATH);

    assert(cbm_next(&font, gb, 2, &key, &consumed) == CBM_OK);
    assert(key == 0xB0A1u && consumed == 2);

    assert(cbm_next(&font, ascii, 1, &key, &consumed) == CBM_OK);
    assert(key == 0x41u && consumed == 1);

    assert(cbm_next(&font, lead_only, 1, &key, &consumed) == CBM_ERR_FORMAT);
    assert(cbm_next(&font, bad_utf8, 2, &key, &consumed) == CBM_OK); /* 未按 UTF-8 解析 */

    assert(cbm_next(&font, gb, 0, &key, &consumed) == CBM_ERR_ARG);
    assert(cbm_next(0, gb, 2, &key, &consumed) == CBM_ERR_ARG);

    fclose(fp);
}

static void test_indexed(void)
{
    cbm_font font;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    FILE *fp;
    uint32_t index = 0;
    uint32_t key = 0;
    uint32_t consumed = 0;
    const uint8_t yi[] = {0xE4, 0xB8, 0x80};             /* U+4E00 */
    const uint8_t emoji[] = {0xF0, 0x9F, 0x98, 0x80};    /* U+1F600 */
    const uint8_t overlong[] = {0xC0, 0x80};
    const uint8_t surrogate[] = {0xED, 0xA0, 0x80};
    const uint8_t truncated[] = {0xE4, 0xB8};

    open_file(&font, &stream, &reader, &fp, INDEXED_PATH);

    assert(font.encoding == CBM_ENC_UTF8);
    assert((font.flags & CBM_FLAG_INDEXED) != 0);
    assert(font.glyph_count == 5);
    assert((font.flags & CBM_FLAG_INDEX_U16) == 0); /* 含 U+1F600，必须用 32 位 */

    assert(cbm_lookup(&font, &reader, 0x20u, &index) == CBM_OK && index == 0);
    assert(cbm_lookup(&font, &reader, 0x4E8Cu, &index) == CBM_OK && index == 3);
    assert(cbm_lookup(&font, &reader, 0x1F600u, &index) == CBM_OK && index == 4);
    assert(cbm_lookup(&font, &reader, 0x42u, &index) == CBM_ERR_NOTFOUND);

    assert(cbm_next(&font, yi, sizeof(yi), &key, &consumed) == CBM_OK);
    assert(key == 0x4E00u && consumed == 3);

    assert(cbm_next(&font, emoji, sizeof(emoji), &key, &consumed) == CBM_OK);
    assert(key == 0x1F600u && consumed == 4);

    assert(cbm_next(&font, overlong, sizeof(overlong), &key, &consumed) == CBM_ERR_FORMAT);
    assert(cbm_next(&font, surrogate, sizeof(surrogate), &key, &consumed) == CBM_ERR_FORMAT);
    assert(cbm_next(&font, truncated, sizeof(truncated), &key, &consumed) == CBM_ERR_FORMAT);

    assert(cbm_verify(&font, &reader) == CBM_OK);

    fclose(fp);
}

static void test_dense_utf8(void)
{
    cbm_font font;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    FILE *fp;
    uint32_t index = 0;
    uint32_t key = 0;
    uint32_t consumed = 0;
    uint8_t glyph[32];
    const uint8_t yi[] = {0xE4, 0xB8, 0x80}; /* U+4E00 */
    int j;

    open_file(&font, &stream, &reader, &fp, DENSE_UTF8_PATH);

    assert(font.encoding == CBM_ENC_UTF8);
    assert((font.flags & CBM_FLAG_INDEXED) == 0);
    assert(font.glyph_count == 5);
    assert(font.range_count == 2);
    assert(font.ranges[0].hi_min == 0 && font.ranges[0].lo_min == 0x41);
    assert(font.ranges[1].hi_min == 0x4E && font.ranges[1].glyph_offset == 2);

    assert(cbm_lookup(&font, &reader, 0x41u, &index) == CBM_OK && index == 0);
    assert(cbm_lookup(&font, &reader, 0x4E01u, &index) == CBM_OK && index == 3);
    assert(cbm_lookup(&font, &reader, 0x4E03u, &index) == CBM_ERR_NOTFOUND);
    assert(cbm_lookup(&font, &reader, 0x42u, &index) == CBM_OK && index == 1);

    assert(cbm_next(&font, yi, sizeof(yi), &key, &consumed) == CBM_OK);
    assert(key == 0x4E00u && consumed == 3);

    assert(cbm_read_glyph(&font, &reader, 3, glyph, sizeof(glyph)) == CBM_OK);
    for(j = 0; j < 32; j++)
    {
        assert(glyph[j] == (uint8_t)((3 * 7 + j * 3) & 0xFF));
    }
    assert(cbm_verify(&font, &reader) == CBM_OK);

    fclose(fp);
}

/* 密集查找不产生 I/O；索引查找每次约 log2(N) 次，且不再额外读一次确认 */
static void test_lookup_read_counts(void)
{
    cbm_font font;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    cbm_reader probe;
    counting_reader counter;
    FILE *fp;
    uint32_t index = 0;
    uint8_t glyph[32];

    counter.inner = &reader;
    probe.ctx = &counter;
    probe.read = counting_read;

    open_file(&font, &stream, &reader, &fp, DENSE_PATH);
    counter.reads = 0;
    assert(cbm_lookup(&font, &probe, 0xB0A1u, &index) == CBM_OK && index == 4);
    assert(counter.reads == 0);

    counter.reads = 0;
    assert(cbm_read_glyph(&font, &probe, index, glyph, sizeof(glyph)) == CBM_OK);
    assert(counter.reads == 1);
    fclose(fp);

    open_file(&font, &stream, &reader, &fp, INDEXED_PATH);
    counter.inner = &reader;
    counter.reads = 0;
    assert(cbm_lookup(&font, &probe, 0x4E8Cu, &index) == CBM_OK && index == 3);
    assert(counter.reads >= 1 && counter.reads <= 4);
    fclose(fp);
}

static size_t load_bytes(const char *path, uint8_t *buf, size_t size)
{
    FILE *fp = fopen(path, "rb");
    size_t n;
    assert(fp != NULL);
    n = fread(buf, 1, size, fp);
    fclose(fp);
    return n;
}

static void put_u32(uint8_t *p, uint32_t value)
{
    p[0] = (uint8_t)(value & 0xFFu);
    p[1] = (uint8_t)((value >> 8) & 0xFFu);
    p[2] = (uint8_t)((value >> 16) & 0xFFu);
    p[3] = (uint8_t)((value >> 24) & 0xFFu);
}

/* 头部字段必须与规范一致；越界/自相矛盾的文件应在 cbm_open 被拒绝 */
static void test_malformed_header(void)
{
    static uint8_t dense[1024];
    static uint8_t indexed[1024];
    size_t dense_size = load_bytes(DENSE_PATH, dense, sizeof(dense));
    cbm_font font;
    cbm_reader reader;
    uint8_t saved8;
    uint32_t saved32;
    uint32_t key = 0;
    uint32_t consumed = 0;
    const uint8_t ascii[] = {'A'};

    assert(dense_size > CBM_HEADER_SIZE);
    assert(load_bytes(INDEXED_PATH, indexed, sizeof(indexed)) > CBM_HEADER_SIZE);

    reader.ctx = dense;
    reader.read = cbm_read_mem;
    assert(cbm_open(&font, &reader) == CBM_OK);

    /* 版本号 */
    saved8 = dense[4];
    dense[4] = 3;
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
    dense[4] = saved8;

    /* 区间数量超过 CBM_MAX_RANGES */
    saved8 = dense[15];
    dense[15] = CBM_MAX_RANGES + 1;
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
    dense[15] = saved8;

    /* bytes_per_glyph 与宽高不匹配 */
    saved8 = dense[8];
    dense[8] = 33;
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
    dense[8] = saved8;

    /* data_offset 超出文件范围 */
    saved32 = (uint32_t)dense[16] | ((uint32_t)dense[17] << 8) |
              ((uint32_t)dense[18] << 16) | ((uint32_t)dense[19] << 24);
    put_u32(&dense[16], (uint32_t)dense_size + 4u);
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
    put_u32(&dense[16], saved32);

    /* 密集模式不应带码点索引 */
    put_u32(&dense[20], CBM_HEADER_SIZE);
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
    put_u32(&dense[20], 0u);

    /* 未知编码编号 */
    assert(cbm_open(&font, &reader) == CBM_OK);
    font.encoding = 99;
    assert(cbm_next(&font, ascii, 1, &key, &consumed) == CBM_ERR_FORMAT);

    /* 索引模式：区间表必须为空、index_count 必须等于 glyph_count */
    reader.ctx = indexed;
    assert(cbm_open(&font, &reader) == CBM_OK);

    saved8 = indexed[15];
    indexed[15] = 1;
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
    indexed[15] = saved8;

    saved32 = (uint32_t)indexed[24] | ((uint32_t)indexed[25] << 8) |
              ((uint32_t)indexed[26] << 16) | ((uint32_t)indexed[27] << 24);
    put_u32(&indexed[24], saved32 - 1u);
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
    put_u32(&indexed[24], saved32);

    assert(cbm_open(&font, &reader) == CBM_OK);
}

static void test_memory_reader_and_crc(void)
{
    static uint8_t buffer[1024];
    size_t size;
    cbm_font font;
    cbm_reader reader;
    uint32_t index = 0;

    size = load_bytes(DENSE_PATH, buffer, sizeof(buffer));
    assert(size > CBM_HEADER_SIZE);

    reader.ctx = buffer;
    reader.read = cbm_read_mem;
    assert(cbm_open(&font, &reader) == CBM_OK);
    assert(cbm_lookup(&font, &reader, 0xB0A1u, &index) == CBM_OK && index == 4);
    assert(cbm_verify(&font, &reader) == CBM_OK);

    /* 篡改一个字模字节 -> CRC 不符 */
    buffer[font.data_offset + 3] ^= 0xFF;
    assert(cbm_verify(&font, &reader) == CBM_ERR_CRC);

    /* 破坏 magic -> 打开失败 */
    buffer[0] = 'X';
    assert(cbm_open(&font, &reader) == CBM_ERR_FORMAT);
}

static void test_error_strings(void)
{
    assert(strcmp(cbm_strerror(CBM_OK), "ok") == 0);
    assert(cbm_strerror(CBM_ERR_NOTFOUND) != NULL);
    assert(cbm_strerror((cbm_status)99) != NULL);
}

int main(void)
{
    test_dense_header();
    test_dense_lookup();
    test_dense_glyph();
    test_dense_next();
    test_indexed();
    test_dense_utf8();
    test_lookup_read_counts();
    test_malformed_header();
    test_memory_reader_and_crc();
    test_error_strings();
    printf("test_cbm: all tests passed\n");
    return 0;
}
