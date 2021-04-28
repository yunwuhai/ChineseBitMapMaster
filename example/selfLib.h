/*************************************************************************************
 * @Description  : 
 * @Version      : 
 * @Author       : YunWuHai
 * @Date         : 2021-04-28 00:10:36
 * @LastEditors  : YunWuHai
 * @LastEditTime : 2021-04-28 15:03:18
 * @FilePath     : \ChineseBitMapMaker\example\selfLib.h
 * @Copyright (C) 2021 YunWuHai. All rights reserved.
 *************************************************************************************/
#ifndef SELF_LIB_H
#define SELF_LIB_H

#include "../src/FindCode.h"
#include <stdio.h>
#include <stdbool.h>
#include <stdint.h>

#define MAX_BYTE_NUM 2  // 编码最大字节数

#define GET_WORD_FUNC fread(codeStr, 1, MAX_BYTE_NUM, fp)  // 读数据函数，每次读取最大字节数的字节内容存储在codeStr中，以保证绝对可以搜索到编码
#define BEFORE_x_BYTE_FUNC(x) (fseek(fp, -x, 1))    // 文件回读函数，在读取到的编码不是最大长度编码时需要进行回读，以保证下次读取到正确编码。回读长度为最大字节数减去实际本次编码长度。

void DraBitMap(uint8_t* bitMap);

#endif