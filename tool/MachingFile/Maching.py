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
