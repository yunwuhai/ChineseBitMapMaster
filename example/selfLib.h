/*************************************************************************************
 * @Description  : 
 * @Version      : 
 * @Author       : YunWuHai
 * @Date         : 2021-04-28 00:10:36
 * @LastEditors  : YunWuHai
 * @LastEditTime : 2021-04-28 00:37:14
 * @FilePath     : \ChineseBitMapMaker\example\selfLib.h
 * @Copyright (C) 2021 YunWuHai. All rights reserved.
 *************************************************************************************/
#ifndef SELF_LIB_H
#define SELF_LIB_H

#include "..\src\FindCode.h"
#include <stdio.h>

#define MAX_BYTE_NUM 2  // 编码最大字节数
uint8_t codeStr[MAX_BYTE_NUM] = {0};

CodeType chr = {{0}};

FILE *fp = 0;
#define GET_WORD_FUNC fread(codeStr, 1, MAX_BYTE_NUM, fp)  // 从b文件里读取MAX_BYTE_NUM个字节到a数组中
#define BEFORE_x_BYTE_FUNC(x) (fseek(fp, -x, 1))

#endif