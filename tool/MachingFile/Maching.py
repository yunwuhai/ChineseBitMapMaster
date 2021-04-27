'''
 Description  : 将字模文本文件转换为二进制字模字库文件，但是如果文本文件格式与本文件夹下的gb2312_bitmap.txt不同的话，则需要修改格式或修改本代码
 Version      : beta-1.0.0
 Author       : YunWuHai
 Date         : 2021-04-25 19:01:22
 LastEditors  : YunWuHai
 LastEditTime : 2021-04-26 15:19:55
 FilePath     : \ChineseBitMapMaker\tool\MachingFile\Maching.py
 Copyright (C) 2021 YunWuHai. All rights reserved.
'''
import struct
import sys

print("%s  ----->-----  %s" % (sys.argv[1], sys.argv[2]))

inFile = open(sys.argv[1], "r")
outFile = open(sys.argv[2], "wb+")

text = inFile.readlines()
for row in range(0, len(text) - 1, 2):
    getNum = []
    for times in range(2):
        for i in range(0, 31, 2):
            num = struct.pack('B', int(text[row + times][i:i + 2], 16))
            outFile.write(num)

print("转换完成，请将%s放至您的SD卡中" % (sys.argv[2]))

inFile.close()
outFile.close()
