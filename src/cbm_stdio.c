/*************************************************************************************
 * @Description  : cbm 的 stdio 适配层
 * @Version      : 2.1.0
 * SPDX-License-Identifier: MIT
 *************************************************************************************/
#include "cbm_stdio.h"

int cbm_stdio_read(void *ctx, uint32_t offset, void *buf, uint32_t len)
{
    cbm_stdio_ctx *stream = (cbm_stdio_ctx *)ctx;

    if(stream == 0 || stream->fp == 0 || buf == 0)
    {
        return -1;
    }
    if(fseek(stream->fp, (long)offset, SEEK_SET) != 0)
    {
        return -1;
    }
    if(fread(buf, 1, (size_t)len, stream->fp) != (size_t)len)
    {
        return -1;
    }
    return 0;
}
