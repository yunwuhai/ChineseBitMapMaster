/*************************************************************************************
 * @Description  : 功能测试
 * @Version      : 
 * @Author       : YunWuHai
 * @Date         : 2021-04-27 23:51:31
 * @LastEditors  : YunWuHai
 * @LastEditTime : 2021-04-28 15:21:47
 * @FilePath     : \ChineseBitMapMaker\example\Example.c
 * @Copyright (C) 2021 YunWuHai. All rights reserved.
 *************************************************************************************/
#include "selfLib.h"

#define CH_FILE "./gb_ch.GB"
#define PUNC_FILE "./gb_punc.GB"
#define WORKING_FILE "./word.txt"

int main()
{
    uint8_t codeStr[MAX_BYTE_NUM] = {0};    // 用来存储编码

    FILE *fp = 0;   // 待读文本文件
    FILE *chFile = 0;   // 中文字库文件
    FILE *puncFile = 0; // 中文符号文件
    if((fp = fopen("./word.txt", "rb")) == 0)
    {
        printf("Open false!");
        return(-1);
    }
    if((chFile = fopen("./gb_ch.GB", "rb")) == 0)
    {
        printf("ch Open false!");
        return(-1);
    }
    if((puncFile = fopen("./gb_punc.GB", "rb")) == 0)
    {
        printf("punc Open false!");
        return(-1);
    }
    CodeType chr = {{0}};
    int id = -1;
    size_t flag = MAX_BYTE_NUM;
    uint8_t bitMap[32] = {0};
    while(flag == MAX_BYTE_NUM)
    {
        flag = GET_WORD_FUNC;
        chr = GetCodeType(codeStr);
        switch (chr.Type[0])
        {
        case 0x01:
            printf("ASCII:");
            printf("%d\n", codeStr[0]);
            BEFORE_x_BYTE_FUNC(1);
            break;
        case 0x02:
            id = GetID_GB_Punc(codeStr);
            fseek(puncFile, id * 32, SEEK_SET);
            fread(bitMap, 1, 32, puncFile);
            DraBitMap(bitMap);
            break;
        case 0x04:
            id = GetID_GB_Ch(codeStr);
            fseek(chFile, id * 32, SEEK_SET);
            fread(bitMap, 1, 32, chFile);
            DraBitMap(bitMap);
            break;
        default:
            printf("No Coding!\n");
            break;
        }
    }
    fclose(fp);
    fclose(chFile);
    fclose(puncFile);
    printf("Search File Ending!");
    return 0;
}