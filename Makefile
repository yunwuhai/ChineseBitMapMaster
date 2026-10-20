# SPDX-License-Identifier: MIT

PYTHON ?= python
CC ?= cc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror -O2
FONT ?=
FONT_FLAG = $(if $(FONT),-f "$(FONT)",)
MKDIR ?= mkdir -p

.PHONY: all fonts fixtures test test-py test-c demo clean

all: fonts

# 生成示例字库（默认自动探测中文字体，可用 make fonts FONT=/path/to/simsun.ttc 指定）
fonts:
	$(PYTHON) tools/cbfont.py build -c gb2312 -s 16 -o example/gb2312.cbm $(FONT_FLAG)

# 重新生成 C 测试用的合成字库
fixtures:
	$(PYTHON) tests/fixtures/make_fixtures.py

test: test-py test-c

test-py:
	$(PYTHON) -m pytest -q

test-c: build/test_cbm
	./build/test_cbm

build/test_cbm: tests/test_cbm.c src/cbm.c src/cbm.h src/cbm_stdio.c src/cbm_stdio.h
	@$(MKDIR) build
	$(CC) $(CFLAGS) tests/test_cbm.c src/cbm.c src/cbm_stdio.c -o $@

demo: fonts
	@$(MKDIR) build
	$(CC) $(CFLAGS) example/Example.c example/selfLib.c src/cbm.c src/cbm_stdio.c -o build/demo
	./build/demo

clean:
	rm -rf build
