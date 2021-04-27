/*************************************************************************************
 * @Description  : 功能测试
 * @Version      : 
 * @Author       : YunWuHai
 * @Date         : 2021-04-27 23:51:31
 * @LastEditors  : YunWuHai
 * @LastEditTime : 2021-04-28 00:56:58
 * @FilePath     : \ChineseBitMapMaker\example\Example.c
 * @Copyright (C) 2021 YunWuHai. All rights reserved.
 *************************************************************************************/
#include "selfLib.h"

int main()
{
    if((fp = fopen("./word.txt", "rb")) == 0)
    {
        printf("Open false!");
        return(-1);
    }
    CodeType chr = {{0}};
    int id = -1;
    while(codeStr[0] != '\n')
    {
        GET_WORD_FUNC;
        chr = GetCodeType(codeStr);
        switch (chr.Type[0])
        {
        case 0x01:
            printf("ASCII:");
            printf("%d\n", codeStr[0]);
            BEFORE_x_BYTE_FUNC(1);
            break;
        case 0x02:
            printf("CH_Punc:");
            id = GetID_GB_Punc(codeStr);
            printf("(id)%d\n", id);
            break;
        case 0x04:
            printf("CH_Word:");
            id = GetID_GB_Ch(codeStr);
            printf("(id)%d\n", id);
            break;
        default:
            printf("No Coding!\n");
            break;
        }
    }
    
    
    fclose(fp);
    return 0;
}