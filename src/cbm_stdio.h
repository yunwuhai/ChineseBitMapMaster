/*************************************************************************************
 * @Description  : cbm 的 stdio 适配层（只用于 PC/示例，不参与移植）
 * @Version      : 2.1.0
 * SPDX-License-Identifier: MIT
 *************************************************************************************/
#ifndef CBM_STDIO_H
#define CBM_STDIO_H

#include <stdio.h>
#include "cbm.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct
{
    FILE *fp;
} cbm_stdio_ctx;

int cbm_stdio_read(void *ctx, uint32_t offset, void *buf, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif /* CBM_STDIO_H */
