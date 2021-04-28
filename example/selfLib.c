/*************************************************************************************
 * @Description  : 
 * @Version      : 
 * @Author       : YunWuHai
 * @Date         : 2021-04-28 00:10:28
 * @LastEditors  : YunWuHai
 * @LastEditTime : 2021-04-28 15:20:46
 * @FilePath     : \ChineseBitMapMaker\example\selfLib.c
 * @Copyright (C) 2021 YunWuHai. All rights reserved.
 *************************************************************************************/
#include "selfLib.h"

void DraBitMap(uint8_t* bitMap)
{
    printf("|----------------|\n|");
    int line = 0;
    // 遍历bitMap
    for(int i = 0; i < 32; i++)
    {
        for(int j = 0; j < 8; j++)
        {
            uint8_t temp = bitMap[i] & (0b10000000 >> j);
            if(temp == 0)
            {
                printf(".");
            }
            else
            {
                printf("X");
            }
        }
        if(line >= 1)
        {
            printf("|\n|");
            line = 0;
        }
        else
        {
            line++;
        }
    }
    printf("----------------|\n");
}