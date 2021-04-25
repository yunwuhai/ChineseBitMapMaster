import struct
import sys

# 这里使用的是GB2312的编码，如果需要换编码，只需要修改这里的四个数即可
# 其中START_HIGH和START_LOW共同组成编码起始位置
# 而END_HIGH和END_LOW共同组成编码结束位置
# 不过具体还请自行查看你将要转换的编码规则
START_HIGH = 0xB0
START_LOW = 0xA1
END_HIGH = 0xF8
END_LOW = 0xFF


print("正在生成全字符文件%s" % (sys.argv[1]))

codeFile = open(sys.argv[1], "wb+")

# 如果要修改编码，只需要修改这里的遍历位置即可
for i in range(START_HIGH, END_HIGH):
    for j in range(START_LOW, END_LOW):
        codeFile.write(struct.pack('B', i))
        codeFile.write(struct.pack('B', j))
