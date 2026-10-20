/*************************************************************************************
 * @Description  : 示例：读取 GB2312 文本，查字库并在终端画出字形
 * @Version      : 2.1.0
 * @Author       : YunWuHai
 * @Copyright (C) 2021-2026 YunWuHai
 * SPDX-License-Identifier: MIT
 *
 * 编译（在仓库根目录）：
 *     gcc example/Example.c example/selfLib.c src/cbm.c src/cbm_stdio.c -o build/demo
 * 运行（需要先 make fonts 生成字库）：
 *     ./build/demo
 *************************************************************************************/
#include "selfLib.h"
#include "../src/cbm_stdio.h"

#define TEXT_FILE "example/word.txt"
#define FONT_FILE "example/gb2312.cbm"
#define TEXT_MAX  8192

int main(void)
{
    FILE *textFile;
    FILE *fontFile;
    cbm_stdio_ctx stream;
    cbm_reader reader;
    cbm_font font;
    cbm_status status;
    uint8_t text[TEXT_MAX];
    uint8_t glyph[128];
    size_t length;
    uint32_t pos;

    textFile = fopen(TEXT_FILE, "rb");
    fontFile = fopen(FONT_FILE, "rb");
    if(textFile == NULL || fontFile == NULL)
    {
        printf("打开文件失败，请先执行 make fonts 生成字库。\n");
        if(textFile) fclose(textFile);
        if(fontFile) fclose(fontFile);
        return 1;
    }

    stream.fp = fontFile;
    reader.ctx = &stream;
    reader.read = cbm_stdio_read;

    status = cbm_open(&font, &reader);
    if(status != CBM_OK)
    {
        printf("打开字库失败: %s\n", cbm_strerror(status));
        fclose(textFile);
        fclose(fontFile);
        return 1;
    }

    printf("字库: %ux%u, %u 个字形, 编码=%u, %s\n",
           font.width, font.height, font.glyph_count, font.encoding,
           (font.flags & CBM_FLAG_INDEXED) ? "索引" : "密集");

    length = fread(text, 1, sizeof(text), textFile);
    if(length == 0)
    {
        printf("文本为空。\n");
        fclose(textFile);
        fclose(fontFile);
        return 0;
    }

    pos = 0;
    while(pos < length)
    {
        uint32_t key = 0;
        uint32_t consumed = 0;
        uint32_t index = 0;

        status = cbm_next(&font, &text[pos], (uint32_t)(length - pos), &key, &consumed);
        if(status != CBM_OK)
        {
            printf("\n[非法字节 0x%02X]\n", text[pos]);
            pos++;
            continue;
        }
        pos += consumed;

        if(key == (uint32_t)'\r')
        {
            continue;
        }
        if(key == (uint32_t)'\n')
        {
            printf("\n");
            continue;
        }

        status = cbm_lookup(&font, &reader, key, &index);
        if(status != CBM_OK)
        {
            printf("[无字形 U+%04X]", (unsigned)key);
            continue;
        }

        status = cbm_read_glyph(&font, &reader, index, glyph, sizeof(glyph));
        if(status != CBM_OK)
        {
            printf("[读取失败 %s]", cbm_strerror(status));
            continue;
        }

        DraBitMap(glyph, font.width, font.height,
                  (uint8_t)((font.width + 7u) / 8u),
                  (uint8_t)((font.flags & CBM_FLAG_LSB_FIRST) ? 1u : 0u));
    }

    fclose(textFile);
    fclose(fontFile);
    printf("结束。\n");
    return 0;
}
