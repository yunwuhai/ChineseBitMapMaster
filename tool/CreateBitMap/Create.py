# !usr/bin/env python
# -*- coding:utf-8 -*-

'''
 Description  : 生成GB2312的编码，如果需要生成其他编码请安下方注释进行修改
 Version      : beta-1.0.0
 Author       : YunWuHai
 Date         : 2021-04-25 20:57:07
 LastEditors  : YunWuHai
 LastEditTime : 2021-04-26 15:29:52
 FilePath     : \ChineseBitMapMaker\tool\CreateBitMap\Create.py
 Copyright (C) 2021 YunWuHai. All rights reserved.
'''
import struct
import sys

# 这里使用的是GB2312的编码，如果需要换编码，只需要修改这里的四个数即可
# 其中START_HIGH和START_LOW共同组成编码起始位置
# 而END_HIGH和END_LOW共同组成编码结束位置
# 不过具体还请自行查看你将要转换的编码规则
# START_HIGH = 0xB0 #汉字编码
START_HIGH = 0xA1 # 符号编码
START_LOW = 0xA1
# END_HIGH = 0xF7   # 汉字编码
END_HIGH = 0xA9   # 符号编码
END_LOW = 0xFE


print("正在生成全字符文件%s" % (sys.argv[1]))

codeFile = open(sys.argv[1], "wb+")

# 如果要修改编码，只需要修改这里的遍历位置即可
for i in range(START_HIGH, END_HIGH + 1):
    for j in range(START_LOW, END_LOW + 1):
        codeFile.write(struct.pack('B', i))
        codeFile.write(struct.pack('B', j))
