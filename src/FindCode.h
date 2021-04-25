/**
 * 用于将GB2312编码字符转换为BitMap对应编号，并提供对比程序
 */
#ifndef FIND_CODE_H
#define FIND_CODE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C"{
#endif

typedef union GBCode
{
    uint8_t GB_8[2];    // 8位，用于分区和分位处理
    uint16_t GB_16; // 整个处理
}Union_GB2312;

// 将一个以GB2312保存的文件传入，然后选择需要处理的行列，如果错误则返回0
Union_GB2312 GetGB2312Code(FILE fp, int row, int col);

// 将GB2312的字符传入，然后返回其位于字库的位置，如果为非中文内容，则返回-1
int GetGB2312ID(Union_GB2312 code); 



#ifdef __cplusplus
}
#endif

#endif