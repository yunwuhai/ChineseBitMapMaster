/*************************************************************************************
 * @Description  : 字库使用库函数定义，如果需要使用可以直接在此移植
 * @Version      : alpha-0.0.1
 * @Author       : YunWuHai
 * @Date         : 2021-04-26 14:25:52
 * @LastEditors  : YunWuHai
 * @LastEditTime : 2021-04-28 00:47:53
 * @FilePath     : \ChineseBitMapMaker\Src\FindCode.c
 * @Copyright (C) 2021 YunWuHai. All rights reserved.
 *************************************************************************************/
#include "FindCode.h"

/*************************************************************************************
 * @description: 判断编码类型，请注意：这个判断目前只能进行ASCII和GB2312的判断
 * @param {uint8_t*} code:待判断的字符串，注意取放顺序需要从前到后从高到低，例如有一个编码为0x3456，那么code存储内容应该为code[0]=0x34,code[1]=0x56
 * @return {CodeType} 返回字符类型，根据字符类型决定如何工作
 *************************************************************************************/
CodeType GetCodeType(uint8_t* code)
{
    CodeType type = {{0}};  // 临时变量
    
    if(code[0] <= 0x7F)    // 如果发现高位字符在检验的时候字符小于等于127即ASCII最大编码值，则证明此编码为ASCII编码（参考GB2312兼容ASCII原理，如果有其它编码请考虑其兼容性）
    {
        type.Bit.ASCII = 1;
        return(type);
    }
    else if(code[1] >= 0xA1 && code[1] <= 0xFE)    // 如果在高位确定不是ASCII后，则应该判断其是否属于GB2312，此时先判断低位是否属于GB2312的范围
    {
        // 如果属于GB2312的范围，判断具体类型
        if(code[0] <= 0xA9) // 此时判断其为符号区域
        {
            type.Bit.GB_PUNC = 1;
            return(type);
        }
        else if(code[0] >= 0xB0 && code[0] <= 0xF7) // 此时判断其为汉字区域
        {
            type.Bit.GB_CH = 1;
            return(type);
        }
    }
    return(type);   // 如果上述情况都不满足，则直接返回，此时所有位应该都为0
}

/*************************************************************************************
 * @description: 搜索某个中文编码在字库中的位置
 * @param {uint8_t*} code: 需要搜索BitMap的GB2312中文编码
 * @return {int} 本编码在BitMap字库中的位置
 *************************************************************************************/
int GetID_GB_Ch(uint8_t* code)
{
    int areaOffset = code[0] - 0xB0;    // 区号偏移量
    int bitOffset = code[1] - 0xA1; // 位号偏移量
    int id = -1;
    if(areaOffset >= 0 && areaOffset <= 71) // 确定区编码无误
    {
        id = areaOffset * 94;   // 移动区，一个区94位
    }
    if(bitOffset >= 0 && bitOffset <= 93)   // 确定位编码无误
    {
        id += bitOffset;    // 移动位
    }
    return(id);
}

int GetID_GB_Punc(uint8_t* code)
{
    int areaOffset = code[0] - 0xA1;    // 区号偏移量
    int bitOffset = code[1] - 0xA1; // 位号偏移量
    int id = -1;
    if(areaOffset >= 0 && areaOffset <= 8) // 确定区编码无误
    {
        id = areaOffset * 94;   // 移动区，一个区94位
    }
    if(bitOffset >= 0 && bitOffset <= 93)   // 确定位编码无误
    {
        id += bitOffset;    // 移动位
    }
    return(id);
}