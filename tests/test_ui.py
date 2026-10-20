# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Web UI 后端逻辑测试（不启动 HTTP 服务）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cbfont = _load("cbfont", ROOT / "tools" / "cbfont.py")
ui = _load("cbfont_ui", ROOT / "tools" / "cbfont_ui.py")


def _available_font():
    for candidate in cbfont.FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


FONT = _available_font()
requires_font = pytest.mark.skipif(FONT is None, reason="本机没有可用的中文字体")


class TestParams:
    def test_requires_font(self):
        with pytest.raises(ValueError):
            ui.parse_params({"charset": "gb2312"})

    def test_rejects_bad_font(self, tmp_path):
        with pytest.raises(ValueError):
            ui.parse_params({"font": str(tmp_path / "nope.ttf")})

    def test_rejects_bad_charset(self):
        with pytest.raises(ValueError):
            ui.parse_params({"font": str(FONT or "x"), "charset": "nope"})

    def test_rejects_bad_size(self):
        with pytest.raises(ValueError):
            ui.parse_params({"font": str(FONT or "x"), "size": 4})

    def test_defaults(self):
        if FONT is None:
            pytest.skip("no font")
        params = ui.parse_params({"font": str(FONT)})
        assert params["charset"] == "gb2312"
        assert params["size"] == 16
        assert params["threshold"] == 128
        assert params["layout"] == "auto"

    def test_rejects_bad_layout(self):
        if FONT is None:
            pytest.skip("no font")
        with pytest.raises(ValueError):
            ui.parse_params({"font": str(FONT), "layout": "nope"})

    def test_rejects_non_bmp_dense(self):
        if FONT is None:
            pytest.skip("no font")
        with pytest.raises(ValueError, match="密集布局"):
            ui.parse_params({"font": str(FONT), "charset": "range:U+1F600", "layout": "dense"})

    def test_rejects_indexed_for_dbcs(self):
        if FONT is None:
            pytest.skip("no font")
        with pytest.raises(ValueError, match="DBCS"):
            ui.parse_params({"font": str(FONT), "charset": "gb2312", "layout": "indexed"})


class TestHelp:
    def test_detail_dense(self):
        detail = ui.charset_detail(cbfont.plan_charset("gb2312"))
        assert detail["dense_supported"] is True
        assert detail["dense_slots"] == 7709
        assert detail["dense_reason"] == ""
        assert detail["sample"]

    def test_detail_non_bmp(self):
        detail = ui.charset_detail(cbfont.plan_charset("range:U+1F600"))
        assert detail["dense_supported"] is False
        assert "BMP" in detail["dense_reason"]

    @requires_font
    def test_compare_cells(self):
        params = ui.parse_params({
            "font": str(FONT), "charset": "gb2312", "size": 16, "sample": "你好",
        })
        data = ui.render_compare(params, "threshold")
        # 字体可能没有汉字（CI 上的 DejaVu），此时回退到字体确实有的字符
        assert data["chars"]
        assert len(data["cells"]) == 3
        assert all(cell["png"].startswith("data:image/png;base64,") for cell in data["cells"])

    def test_compare_rejects_unknown_kind(self):
        with pytest.raises(ValueError, match="未知对比类型"):
            ui.render_compare({}, "nope")


class TestPreview:
    def test_stats_for_dense_plan(self):
        plan = cbfont.plan_charset("gb2312")
        stats = ui.charset_stats(plan)
        assert stats["count"] == 7709
        assert stats["ranges"] == 3
        assert stats["indexed"] is False
        assert stats["mode"] == "dense"
        assert stats["bytes"] > 7000

    def test_stats_for_indexed_plan(self):
        plan = cbfont.plan_charset("range:U+0041-U+0043")
        stats = ui.charset_stats(plan)
        assert stats["count"] == 3
        assert stats["indexed"] is True
        assert stats["mode"] == "auto"
        assert stats["indexed_bytes"] < stats["dense_bytes"]

    def test_forced_dense_stats(self):
        plan = cbfont.plan_charset("range:U+0041-U+0043")
        stats = ui.charset_stats(plan, layout="dense")
        assert stats["mode"] == "dense"
        assert stats["bytes"] == stats["dense_bytes"]

    def test_preview_chars_fall_back_when_no_overlap(self):
        plan = cbfont.plan_charset("range:U+1F600")
        assert ui.preview_chars(plan, "你好") == ["\U0001F600"]

    def test_preview_chars_keep_matching_text(self):
        plan = cbfont.plan_charset("gb2312")
        assert ui.preview_chars(plan, "你好ABC") == list("你好ABC")


@requires_font
class TestWithFont:
    def test_preview_png(self):
        params = ui.parse_params({
            "font": str(FONT), "charset": "gb2312", "size": 16, "sample": "你好",
        })
        png = ui.render_preview(params)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_build_writes_file(self, tmp_path):
        params = ui.parse_params({
            "font": str(FONT), "charset": "latin-1", "size": 16,
        })
        result = ui.do_build(params, tmp_path)
        target = Path(result["path"])
        assert target.is_file()
        assert target.read_bytes()[:4] == b"CBMF"
        assert result["count"] == 224
