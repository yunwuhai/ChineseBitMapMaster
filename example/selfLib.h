/*************************************************************************************
 * @Description  : 示例工程的公共声明
 * @Version      : 2.1.0
 * @Author       : YunWuHai
 * @Copyright (C) 2021-2026 YunWuHai
 * SPDX-License-Identifier: MIT
 *************************************************************************************/
#ifndef SELF_LIB_H
#define SELF_LIB_H

#include "../src/cbm.h"
#include <stdio.h>
#include <stdint.h>

/* 把字模按点阵画到终端，便于肉眼验证 */
void DraBitMap(const uint8_t *bitMap, uint8_t width, uint8_t height,
               uint8_t bytes_per_row, uint8_t lsb_first);

#endif /* SELF_LIB_H */
