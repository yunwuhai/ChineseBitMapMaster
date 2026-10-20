/*************************************************************************************
 * @Description  : 终端点阵绘制
 * @Version      : 2.1.0
 * @Author       : YunWuHai
 * @Copyright (C) 2021-2026 YunWuHai
 * SPDX-License-Identifier: MIT
 *************************************************************************************/
#include "selfLib.h"

void DraBitMap(const uint8_t *bitMap, uint8_t width, uint8_t height,
               uint8_t bytes_per_row, uint8_t lsb_first)
{
    int x;
    int y;

    printf("+");
    for(x = 0; x < width; x++)
    {
        printf("-");
    }
    printf("+\n");

    for(y = 0; y < height; y++)
    {
        printf("|");
        for(x = 0; x < width; x++)
        {
            uint8_t byte = bitMap[y * bytes_per_row + (x >> 3)];
            uint8_t mask = lsb_first ? (uint8_t)(1u << (x & 7)) : (uint8_t)(0x80u >> (x & 7));
            printf("%c", (byte & mask) ? 'X' : '.');
        }
        printf("|\n");
    }

    printf("+");
    for(x = 0; x < width; x++)
    {
        printf("-");
    }
    printf("+\n");
}
