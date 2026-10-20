/*************************************************************************************
 * @Description  : CBMF v2 点阵字库运行时
 * @Version      : 2.1.0
 * @Author       : YunWuHai
 * @Copyright (C) 2021-2026 YunWuHai
 * SPDX-License-Identifier: MIT
 *************************************************************************************/
#include "cbm.h"

/* -------------------------------------------------------------------------- */
/* 小工具                                                                      */
/* -------------------------------------------------------------------------- */
static uint16_t read_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static cbm_status reader_read(const cbm_reader *reader, uint32_t offset,
                              void *buf, uint32_t len)
{
    if(reader == 0 || reader->read == 0)
    {
        return CBM_ERR_ARG;
    }
    return reader->read(reader->ctx, offset, buf, len) == 0 ? CBM_OK : CBM_ERR_IO;
}

static uint32_t crc32_byte(uint32_t crc, uint8_t byte)
{
    int bit;

    crc ^= (uint32_t)byte;
    for(bit = 0; bit < 8; bit++)
    {
        crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1u)));
    }
    return crc;
}

/* -------------------------------------------------------------------------- */
/* 打开字库                                                                    */
/* -------------------------------------------------------------------------- */
cbm_status cbm_open(cbm_font *font, const cbm_reader *reader)
{
    uint8_t header[CBM_HEADER_SIZE];
    uint8_t range_buf[CBM_MAX_RANGES * CBM_RANGE_SIZE];
    uint32_t range_bytes;
    uint32_t index_width;
    uint32_t i;
    cbm_status status;

    if(font == 0 || reader == 0)
    {
        return CBM_ERR_ARG;
    }

    status = reader_read(reader, 0, header, CBM_HEADER_SIZE);
    if(status != CBM_OK)
    {
        return status;
    }

    if(header[0] != 'C' || header[1] != 'B' || header[2] != 'M' || header[3] != 'F')
    {
        return CBM_ERR_FORMAT;
    }

    font->version          = header[4];
    font->flags            = header[5];
    font->width            = header[6];
    font->height           = header[7];
    font->bytes_per_glyph  = read_u16(&header[8]);
    font->glyph_count      = read_u32(&header[10]);
    font->encoding         = header[14];
    font->range_count      = header[15];
    font->data_offset      = read_u32(&header[16]);
    font->index_offset     = read_u32(&header[20]);
    font->index_count      = read_u32(&header[24]);
    font->crc32            = read_u32(&header[28]);
    font->file_size        = read_u32(&header[32]);

    if(font->version != 2u || font->width == 0 || font->height == 0 ||
       font->bytes_per_glyph == 0 || font->range_count > CBM_MAX_RANGES ||
       font->encoding > CBM_ENC_UTF8 || font->file_size < CBM_HEADER_SIZE)
    {
        return CBM_ERR_FORMAT;
    }

    /* bytes_per_glyph 必须与宽高一致，否则读字模的步长会错位 */
    if(font->bytes_per_glyph !=
       (uint16_t)(((uint32_t)font->width + 7u) / 8u) * font->height)
    {
        return CBM_ERR_FORMAT;
    }

    range_bytes = (uint32_t)font->range_count * CBM_RANGE_SIZE;
    if(font->data_offset < CBM_HEADER_SIZE + range_bytes ||
       font->data_offset > font->file_size)
    {
        return CBM_ERR_FORMAT;
    }
    if(font->glyph_count > 0 &&
       font->bytes_per_glyph > (font->file_size - font->data_offset) / font->glyph_count)
    {
        return CBM_ERR_FORMAT;
    }

    if((font->flags & CBM_FLAG_INDEXED) != 0)
    {
        index_width = (font->flags & CBM_FLAG_INDEX_U16) ? 2u : 4u;
        if(font->range_count != 0 || font->index_count != font->glyph_count ||
           font->index_offset < CBM_HEADER_SIZE + range_bytes ||
           font->index_offset > font->data_offset ||
           font->index_count > (font->data_offset - font->index_offset) / index_width)
        {
            return CBM_ERR_FORMAT;
        }
    }
    else if(font->index_offset != 0 || font->index_count != 0)
    {
        return CBM_ERR_FORMAT;
    }

    if(font->range_count > 0)
    {
        status = reader_read(reader, CBM_HEADER_SIZE,
                             range_buf, (uint32_t)font->range_count * CBM_RANGE_SIZE);
        if(status != CBM_OK)
        {
            return status;
        }
        for(i = 0; i < font->range_count; i++)
        {
            const uint8_t *p = &range_buf[i * CBM_RANGE_SIZE];
            font->ranges[i].hi_min       = p[0];
            font->ranges[i].hi_max       = p[1];
            font->ranges[i].lo_min       = p[2];
            font->ranges[i].lo_max       = p[3];
            font->ranges[i].glyph_offset = read_u32(&p[4]);
            if(font->ranges[i].hi_min > font->ranges[i].hi_max ||
               font->ranges[i].lo_min > font->ranges[i].lo_max)
            {
                return CBM_ERR_FORMAT;
            }
        }
    }

    return CBM_OK;
}

/* -------------------------------------------------------------------------- */
/* 编码解析                                                                    */
/* -------------------------------------------------------------------------- */
static int is_lead_byte(const cbm_font *font, uint8_t byte)
{
    uint32_t i;

    for(i = 0; i < font->range_count; i++)
    {
        const cbm_range *r = &font->ranges[i];
        if(r->hi_max != 0 && byte >= r->hi_min && byte <= r->hi_max)
        {
            return 1;
        }
    }
    return 0;
}

static cbm_status utf8_decode(const uint8_t *text, uint32_t len,
                              uint32_t *key, uint32_t *consumed)
{
    uint8_t b0 = text[0];
    uint32_t cp;
    uint32_t need;
    uint32_t i;

    if(b0 < 0x80u)
    {
        *key = b0;
        *consumed = 1;
        return CBM_OK;
    }
    if((b0 & 0xE0u) == 0xC0u)
    {
        cp = b0 & 0x1Fu;
        need = 1;
    }
    else if((b0 & 0xF0u) == 0xE0u)
    {
        cp = b0 & 0x0Fu;
        need = 2;
    }
    else if((b0 & 0xF8u) == 0xF0u)
    {
        cp = b0 & 0x07u;
        need = 3;
    }
    else
    {
        return CBM_ERR_FORMAT;
    }

    if(len < need + 1u)
    {
        return CBM_ERR_FORMAT;
    }
    for(i = 1; i <= need; i++)
    {
        uint8_t b = text[i];
        if((b & 0xC0u) != 0x80u)
        {
            return CBM_ERR_FORMAT;
        }
        cp = (cp << 6) | (b & 0x3Fu);
    }

    /* 拒绝过长编码、代理区与越界码点 */
    if((need == 1 && cp < 0x80u) || (need == 2 && cp < 0x800u) ||
       (need == 3 && cp < 0x10000u) || cp > 0x10FFFFu ||
       (cp >= 0xD800u && cp <= 0xDFFFu))
    {
        return CBM_ERR_FORMAT;
    }

    *key = cp;
    *consumed = need + 1u;
    return CBM_OK;
}

cbm_status cbm_next(const cbm_font *font, const uint8_t *text, uint32_t len,
                    uint32_t *key, uint32_t *consumed)
{
    if(font == 0 || text == 0 || key == 0 || consumed == 0 || len == 0)
    {
        return CBM_ERR_ARG;
    }

    if(font->encoding > CBM_ENC_UTF8)
    {
        return CBM_ERR_FORMAT;
    }

    if(font->encoding == CBM_ENC_UTF8)
    {
        return utf8_decode(text, len, key, consumed);
    }

    if(font->encoding == CBM_ENC_SINGLE || text[0] < 0x80u)
    {
        *key = text[0];
        *consumed = 1;
        return CBM_OK;
    }

    if(!is_lead_byte(font, text[0]))
    {
        *key = text[0];
        *consumed = 1;
        return CBM_OK;
    }

    if(len < 2u)
    {
        return CBM_ERR_FORMAT;
    }

    *key = ((uint32_t)text[0] << 8) | (uint32_t)text[1];
    *consumed = 2;
    return CBM_OK;
}

/* -------------------------------------------------------------------------- */
/* 查找                                                                        */
/* -------------------------------------------------------------------------- */
static cbm_status index_read(const cbm_font *font, const cbm_reader *reader,
                             uint32_t pos, uint32_t *value)
{
    uint8_t buf[4];
    uint32_t width = (font->flags & CBM_FLAG_INDEX_U16) ? 2u : 4u;
    cbm_status status;

    status = reader_read(reader, font->index_offset + pos * width, buf, width);
    if(status != CBM_OK)
    {
        return status;
    }
    *value = (width == 2u) ? (uint32_t)read_u16(buf) : read_u32(buf);
    return CBM_OK;
}

cbm_status cbm_lookup(const cbm_font *font, const cbm_reader *reader,
                      uint32_t key, uint32_t *glyph_index)
{
    uint32_t i;

    if(font == 0 || glyph_index == 0)
    {
        return CBM_ERR_ARG;
    }

    if((font->flags & CBM_FLAG_INDEXED) != 0)
    {
        uint32_t lo = 0;
        uint32_t hi = font->index_count;
        uint32_t candidate = 0;
        int have_candidate = 0;

        while(lo < hi)
        {
            uint32_t mid = lo + (hi - lo) / 2u;
            uint32_t value;
            cbm_status status = index_read(font, reader, mid, &value);
            if(status != CBM_OK)
            {
                return status;
            }
            if(value < key)
            {
                lo = mid + 1u;
            }
            else
            {
                hi = mid;
                candidate = value;
                have_candidate = 1;
            }
        }
        /* 循环里已经读过 hi=mid 的值，无需为了确认再读一次 */
        if(!have_candidate || lo >= font->index_count || candidate != key)
        {
            return CBM_ERR_NOTFOUND;
        }
        *glyph_index = lo;
        return CBM_OK;
    }

    for(i = 0; i < font->range_count; i++)
    {
        const cbm_range *r = &font->ranges[i];
        uint32_t idx;

        if(r->hi_min == 0 && r->hi_max == 0)
        {
            if(key < r->lo_min || key > r->lo_max)
            {
                continue;
            }
            idx = r->glyph_offset + (key - r->lo_min);
        }
        else
        {
            uint32_t hi = key >> 8;
            uint32_t lo = key & 0xFFu;
            uint32_t width;

            if(hi < r->hi_min || hi > r->hi_max || lo < r->lo_min || lo > r->lo_max)
            {
                continue;
            }
            width = (uint32_t)r->lo_max - r->lo_min + 1u;
            idx = r->glyph_offset + (hi - r->hi_min) * width + (lo - r->lo_min);
        }

        if(idx >= font->glyph_count)
        {
            return CBM_ERR_FORMAT;
        }
        *glyph_index = idx;
        return CBM_OK;
    }

    return CBM_ERR_NOTFOUND;
}

cbm_status cbm_read_glyph(const cbm_font *font, const cbm_reader *reader,
                          uint32_t index, uint8_t *buf, uint32_t buf_size)
{
    uint32_t offset;
    uint32_t span;

    if(font == 0 || buf == 0)
    {
        return CBM_ERR_ARG;
    }
    if(buf_size < font->bytes_per_glyph)
    {
        return CBM_ERR_BUFFER;
    }
    if(index >= font->glyph_count)
    {
        return CBM_ERR_NOTFOUND;
    }

    offset = font->data_offset + index * (uint32_t)font->bytes_per_glyph;
    span = (uint32_t)font->bytes_per_glyph;
    if(offset > font->file_size || span > font->file_size - offset)
    {
        return CBM_ERR_FORMAT;
    }

    return reader_read(reader, offset, buf, span);
}

#ifndef CBM_NO_VERIFY
cbm_status cbm_verify(const cbm_font *font, const cbm_reader *reader)
{
    uint8_t chunk[128];
    uint32_t offset = CBM_HEADER_SIZE;
    uint32_t crc = 0xFFFFFFFFu;

    if(font == 0 || reader == 0)
    {
        return CBM_ERR_ARG;
    }

    while(offset < font->file_size)
    {
        uint32_t span = font->file_size - offset;
        uint32_t i;
        cbm_status status;

        if(span > sizeof(chunk))
        {
            span = (uint32_t)sizeof(chunk);
        }
        status = reader_read(reader, offset, chunk, span);
        if(status != CBM_OK)
        {
            return status;
        }
        for(i = 0; i < span; i++)
        {
            crc = crc32_byte(crc, chunk[i]);
        }
        offset += span;
    }

    return (crc ^ 0xFFFFFFFFu) == font->crc32 ? CBM_OK : CBM_ERR_CRC;
}
#endif /* CBM_NO_VERIFY */

#ifndef CBM_NO_STRERROR
const char *cbm_strerror(cbm_status status)
{
    switch(status)
    {
    case CBM_OK:           return "ok";
    case CBM_ERR_ARG:      return "invalid argument";
    case CBM_ERR_IO:       return "read failed";
    case CBM_ERR_FORMAT:   return "bad font format";
    case CBM_ERR_NOTFOUND: return "glyph not found";
    case CBM_ERR_CRC:      return "crc mismatch";
    case CBM_ERR_BUFFER:   return "buffer too small";
    default:               return "unknown error";
    }
}
#endif /* CBM_NO_STRERROR */
