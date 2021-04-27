/*************************************************************************************
 * @Description  : 字库使用库函数，如果需要使用可以直接在此移植
 * @Version      : alpha-0.0.1
 * @Author       : YunWuHai
 * @Date         : 2021-04-24 23:18:09
 * @LastEditors  : YunWuHai
 * @LastEditTime : 2021-04-28 00:45:11
 * @FilePath     : \ChineseBitMapMaker\Src\FindCode.h
 * @Copyright (C) 2021 YunWuHai. All rights reserved.
 *************************************************************************************/
/**
 * 用于将GB2312编码字符转换为BitMap对应编号，并提供对比程序
 */
#ifndef FIND_CODE_H
#define FIND_CODE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C"{
#endif

// 编码类型判断标志，为节省空间使用了位域和联合体，如果需要判断内容需要先将Type清空后再使用
typedef union _CODE_TYPE
{
    struct
    {
        unsigned int ASCII  :   1;
        unsigned int GB_PUNC:   1;
        unsigned int GB_CH  :   1;
    }Bit;
    uint8_t Type[1];
}CodeType;

// 判断编码类型
CodeType GetCodeType(uint8_t* code);

// 将GB2312中文的字符传入，然后返回其位于字库的位置
int GetID_GB_Ch(uint8_t* code); 
// 将GB2312符号的字符传入，然后返回其位于字库的位置
int GetID_GB_Punc(uint8_t* code);

#ifdef __cplusplus
}
#endif

#endif