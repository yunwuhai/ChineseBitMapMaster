#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""ChineseBitmap 的 Web 配置界面。

只依赖 Python 标准库 + Pillow：
    python tools/cbfont_ui.py            # 打开 http://127.0.0.1:8765
    python tools/cbfont_ui.py --port 9000 --no-browser

界面提供：字体选择、字符集/语言选择、点阵参数调节、实时预览、一键生成与下载。
所有构建都在本机完成，服务默认只监听 127.0.0.1。
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import re
import sys
import threading
import unicodedata
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 Pillow，请先执行: pip install pillow")

# 以文件方式加载同目录的 cbfont 模块，避免依赖包结构
_SPEC = importlib.util.spec_from_file_location("cbfont", HERE / "cbfont.py")
cbfont = importlib.util.module_from_spec(_SPEC)
sys.modules["cbfont"] = cbfont
_SPEC.loader.exec_module(cbfont)

DOWNLOADS: dict[str, Path] = {}
DOWNLOADS_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# 业务逻辑
# --------------------------------------------------------------------------
def parse_params(payload: dict) -> dict:
    font = str(payload.get("font") or "").strip()
    if not font:
        raise ValueError("请选择字体文件")
    font_path = Path(font)
    if not font_path.is_file():
        raise ValueError(f"字体文件不存在: {font_path}")
    if font_path.suffix.lower() not in (".ttf", ".ttc", ".otf"):
        raise ValueError("只支持 .ttf/.ttc/.otf 字体")

    charset = str(payload.get("charset") or "gb2312").strip()
    try:
        plan = cbfont.plan_charset(charset)
    except ValueError as exc:
        raise ValueError(str(exc))

    size = int(payload.get("size", 16))
    if size < 8 or size > 64:
        raise ValueError("字号需在 8-64 之间")
    threshold = max(0, min(255, int(payload.get("threshold", 128))))
    layout = str(payload.get("layout") or "auto").strip()
    if layout not in cbfont.LAYOUTS:
        raise ValueError(f"未知布局: {layout}")
    if layout == "indexed" and not plan.indexed:
        raise ValueError("GB2312/GBK 等 DBCS 字符集只能使用密集布局")
    if layout == "dense" and plan.indexed and cbfont.plan_bmp_dense(plan) is None:
        raise ValueError("密集布局仅支持 BMP（U+0000–U+FFFF）且区间不超过 8 个")

    return {
        "font": font_path,
        "font_index": int(payload.get("font_index", 0)),
        "charset": charset,
        "size": size,
        "threshold": threshold,
        "x_offset": int(payload.get("x_offset", 0)),
        "y_offset": int(payload.get("y_offset", 0)),
        "fit": bool(payload.get("fit", False)),
        "invert": bool(payload.get("invert", False)),
        "lsb_first": bool(payload.get("lsb_first", False)),
        "layout": layout,
        "sample": str(payload.get("sample", "")),
        "text_encoding": str(payload.get("text_encoding", "utf-8")),
    }


def charset_stats(plan, size: int = 16, layout: str = "auto") -> dict:
    indexed_bytes = cbfont.estimate_font_size(plan, size) if plan.indexed else None
    dense = cbfont.plan_bmp_dense(plan) if plan.indexed else None
    dense_bytes = cbfont.estimate_font_size(dense, size) if dense is not None else None

    if not plan.indexed:
        mode, estimate = "dense", cbfont.estimate_font_size(plan, size)
    elif layout == "indexed" or dense is None:
        mode, estimate = "indexed", indexed_bytes
    elif layout == "dense":
        mode, estimate = "dense", dense_bytes
    else:
        # auto 的实际选择取决于缺字情况，预览阶段只给出两种估算
        mode, estimate = "auto", None

    return {
        "count": len(plan.keys),
        "assigned": sum(1 for ch in plan.chars if ch is not None),
        "ranges": len(plan.ranges),
        "indexed": plan.indexed,
        "encoding": cbfont.ENCODING_LABEL.get(plan.encoding, "?"),
        "encoding_id": plan.encoding,
        "mode": mode,
        "bytes": estimate,
        "indexed_bytes": indexed_bytes,
        "dense_bytes": dense_bytes,
    }


def preview_chars(plan, text: str, limit: int = 64) -> list[str]:
    """预览文字与字符集不相交时，回退到字符集自己的字形。"""
    keyset = set(plan.keys)

    def included(ch: str) -> bool:
        if ch.isspace():
            return False
        key = cbfont.encode_key(plan.encoding, ch)
        return key is not None and key in keyset

    sample = [ch for ch in text if ch not in "\r\n"]
    if sample and not any(included(ch) for ch in sample):
        sample = []
    if not sample:
        for ch in plan.chars:
            if ch and not ch.isspace() and unicodedata.category(ch) not in ("Cc", "Cs", "Cf", "Cn"):
                sample.append(ch)
            if len(sample) >= 32:
                break
    return sample[:limit]


def render_preview(params: dict) -> bytes:
    plan = cbfont.plan_charset(params["charset"], True, params["text_encoding"])
    keyset = set(plan.keys)
    raster = cbfont.Rasterizer(
        params["font"], size=params["size"], font_index=params["font_index"],
        threshold=params["threshold"], x_offset=params["x_offset"], y_offset=params["y_offset"],
        fit=params["fit"], invert=params["invert"], lsb_first=params["lsb_first"],
    )

    sample = preview_chars(plan, params["sample"])
    size = params["size"]
    scale = 4 if size <= 16 else 2
    cell = size * scale
    gap = 6
    cols = min(8, max(1, len(sample)))
    rows = max(1, (len(sample) + cols - 1) // cols)
    margin = 10
    width = margin * 2 + cols * cell + (cols - 1) * gap
    height = margin * 2 + rows * cell + (rows - 1) * gap

    canvas = Image.new("RGB", (width, height), (18, 18, 22))
    draw = ImageDraw.Draw(canvas)

    for index, ch in enumerate(sample):
        col = index % cols
        row = index // cols
        x = margin + col * (cell + gap)
        y = margin + row * (cell + gap)

        img = raster.image(ch)
        missing = not ch.isspace() and raster.is_missing_image(img)
        if params["invert"]:
            img = img.point(lambda v: 255 - v)
        cell_img = img.convert("L").resize((cell, cell), Image.NEAREST)
        canvas.paste(Image.merge("RGB", (cell_img, cell_img, cell_img)), (x, y))

        key = cbfont.encode_key(plan.encoding, ch)
        included = key is not None and key in keyset
        if not included:
            colour = (255, 80, 80)       # 红：不在字符集
        elif missing:
            colour = (255, 200, 60)      # 黄：在字符集但字体缺字
        else:
            colour = (60, 220, 120)      # 绿：正常收录
        draw.rectangle([x - 1, y - 1, x + cell, y + cell], outline=colour)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def charset_detail(plan, size: int = 16, layout: str = "auto") -> dict:
    """字符集详情，供界面提示和“?”说明面板使用。"""
    detail = charset_stats(plan, size, layout)
    detail["sample"] = [
        ch for ch in plan.chars
        if ch and not ch.isspace() and unicodedata.category(ch)[0] not in "CZ"
    ][:24]

    dense = cbfont.plan_bmp_dense(plan) if plan.indexed else None
    detail["dense_supported"] = (not plan.indexed) or dense is not None
    if dense is not None:
        detail["dense_slots"] = len(dense.keys)
    elif not plan.indexed:
        detail["dense_slots"] = len(plan.keys)
    else:
        detail["dense_slots"] = None

    reason = ""
    if plan.indexed and dense is None:
        if any(key > 0xFFFF for key in plan.keys):
            reason = "包含 BMP 以外的码位（U+10000 以上）"
        elif len(plan.ranges) > cbfont.MAX_RANGES:
            reason = f"区间数 {len(plan.ranges)} 超过上限 {cbfont.MAX_RANGES}"
        else:
            reason = "字符分布超出密集布局能力"
    detail["dense_reason"] = reason
    return detail


# --------------------------------------------------------------------------
# “?”说明里的前后对比图
# --------------------------------------------------------------------------
COMPARE_KINDS = ("threshold", "size", "offset", "fit", "invert")


def _displayable(ch: str) -> bool:
    if not ch or ch.isspace():
        return False
    return unicodedata.category(ch)[0] not in "CZP"


def _compare_chars(params: dict, plan, raster, kind: str) -> list[str]:
    """挑几个既能展示差异、当前字体又确实有的字符。"""
    ordered: list[str] = []

    def add(ch: str) -> None:
        if ch and ch not in ordered and _displayable(ch) and not raster.is_missing(ch):
            ordered.append(ch)

    if kind == "fit":
        for ch in ("一", "国", "A"):
            add(ch)
    for ch in params["sample"]:
        if len(ordered) >= 4:
            break
        add(ch)
    if len(ordered) < 2:
        for ch in ("永", "国", "A"):
            add(ch)
    if not ordered:
        for ch in plan.chars:
            add(ch)
            if len(ordered) >= 2:
                break
    return ordered[:4]


def _compare_scale(size: int) -> int:
    if size <= 12:
        return 6
    if size <= 16:
        return 5
    if size <= 24:
        return 3
    return 2


def _render_strip(params: dict, chars: list[str], scale: int) -> bytes:
    """把若干字形渲染成一条放大的点阵带（按最终阈值二值化）。"""
    size = params["size"]
    cell = size * scale
    gap = 10
    pad = 10
    width = pad * 2 + len(chars) * cell + max(0, len(chars) - 1) * gap
    height = pad * 2 + cell

    canvas = Image.new("RGB", (width, height), (16, 17, 22))
    draw = ImageDraw.Draw(canvas)
    raster = cbfont.Rasterizer(
        params["font"], size=size, font_index=params["font_index"],
        threshold=params["threshold"], x_offset=params["x_offset"], y_offset=params["y_offset"],
        fit=params["fit"], invert=params["invert"], lsb_first=params["lsb_first"],
    )
    threshold = params["threshold"]

    for index, ch in enumerate(chars):
        x = pad + index * (cell + gap)
        y = pad
        img = raster.image(ch)
        img = img.point(lambda value: 255 if value >= threshold else 0)
        if params["invert"]:
            img = img.point(lambda value: 255 - value)
        cell_img = img.convert("L").resize((cell, cell), Image.NEAREST)
        canvas.paste(Image.merge("RGB", (cell_img, cell_img, cell_img)), (x, y))
        draw.rectangle([x - 1, y - 1, x + cell, y + cell], outline=(54, 58, 78))

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def render_compare(params: dict, kind: str) -> dict:
    """生成“修改前 / 修改后”的对比图，返回 data URL 列表。"""
    if kind not in COMPARE_KINDS:
        raise ValueError(f"未知对比类型: {kind}")

    plan = cbfont.plan_charset(params["charset"], True, params["text_encoding"])
    raster = cbfont.Rasterizer(
        params["font"], size=params["size"], font_index=params["font_index"],
        threshold=params["threshold"], x_offset=params["x_offset"], y_offset=params["y_offset"],
        fit=params["fit"], invert=params["invert"], lsb_first=params["lsb_first"],
    )
    chars = _compare_chars(params, plan, raster, kind)
    if not chars:
        raise ValueError("当前字体在这个字符集里没有可用于对比的字形")

    default_scale = _compare_scale(params["size"])
    variants: list[tuple[str, dict, int]]
    if kind == "threshold":
        variants = [
            ("阈值 64 · 偏粗", {"threshold": 64}, default_scale),
            ("阈值 128 · 默认", {"threshold": 128}, default_scale),
            ("阈值 192 · 偏细", {"threshold": 192}, default_scale),
        ]
    elif kind == "size":
        variants = [
            (f"{value}×{value}", {"size": value}, max(2, round(64 / value)))
            for value in (12, 16, 24)
        ]
    elif kind == "offset":
        x, y = params["x_offset"], params["y_offset"]
        variants = [
            (f"当前 ({x}, {y})", {}, default_scale),
            (f"+2, +2 → ({x + 2}, {y + 2})", {"x_offset": x + 2, "y_offset": y + 2}, default_scale),
            (f"-2, -2 → ({x - 2}, {y - 2})", {"x_offset": x - 2, "y_offset": y - 2}, default_scale),
        ]
    elif kind == "fit":
        variants = [
            ("未居中（默认）", {"fit": False}, default_scale),
            ("单元格居中", {"fit": True}, default_scale),
        ]
    else:  # invert
        variants = [
            ("正常（bit 1 = 笔画）", {"invert": False}, default_scale),
            ("反色（bit 1 = 背景）", {"invert": True}, default_scale),
        ]

    cells = []
    for label, override, scale in variants:
        variant = dict(params)
        variant.update(override)
        png = _render_strip(variant, chars, int(scale))
        cells.append({
            "label": label,
            "png": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        })

    return {"chars": "".join(chars), "cells": cells}


def do_build(params: dict, out_dir: Path) -> dict:
    result = cbfont.build_font(
        font_path=params["font"],
        charset=params["charset"],
        size=params["size"],
        font_index=params["font_index"],
        threshold=params["threshold"],
        x_offset=params["x_offset"],
        y_offset=params["y_offset"],
        fit=params["fit"],
        invert=params["invert"],
        lsb_first=params["lsb_first"],
        include_ascii=True,
        text_encoding=params["text_encoding"],
        layout=params["layout"],
    )

    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", params["charset"]).strip("_") or "font"
    path = out_dir / f"{stem}_{params['size']}.cbm"
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.data)

    token = f"t{len(DOWNLOADS) + 1}"
    with DOWNLOADS_LOCK:
        DOWNLOADS[token] = path

    return {
        "path": str(path),
        "bytes": len(result.data),
        "count": result.count,
        "missing": len(result.missing),
        "skipped": result.skipped,
        "blank": result.blank,
        "mode": result.mode,
        "encoding": cbfont.ENCODING_LABEL.get(result.plan.encoding, "?"),
        "download": f"/api/download/{token}",
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ChineseBitmapUI/2.1"

    def log_message(self, fmt, *args):  # 安静一点
        pass

    # -- 工具 -------------------------------------------------------------
    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _png(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    # -- 路由 -------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/options":
            self._json({
                "fonts": cbfont.font_catalog(),
                "charsets": cbfont.charset_choices(),
                "out_dir": str(self.server.out_dir),
            })
        elif path.startswith("/api/download/"):
            token = path.rsplit("/", 1)[-1]
            with DOWNLOADS_LOCK:
                target = DOWNLOADS.get(token)
            if target is None or not target.is_file():
                self._json({"error": "文件不存在"}, 404)
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "请求不是合法 JSON"}, 400)
            return

        try:
            if path == "/api/preview":
                params = parse_params(payload)
                plan = cbfont.plan_charset(params["charset"], True, params["text_encoding"])
                png = render_preview(params)
                self._json({
                    "png": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                    "stats": charset_stats(plan, params["size"], params["layout"]),
                })
            elif path == "/api/stats":
                params = parse_params(payload)
                plan = cbfont.plan_charset(params["charset"], True, params["text_encoding"])
                self._json(charset_detail(plan, params["size"], params["layout"]))
            elif path == "/api/compare":
                params = parse_params(payload)
                self._json(render_compare(params, str(payload.get("kind") or "")))
            elif path == "/api/build":
                params = parse_params(payload)
                self._json(do_build(params, self.server.out_dir))
            else:
                self._json({"error": "not found"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - 兜底
            self._json({"error": f"内部错误: {exc}"}, 500)


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChineseBitmap 字库生成器</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.6 "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
         background: #14151a; color: #d8dbe2; }
  header { padding: 16px 24px; border-bottom: 1px solid #262833; display: flex;
           align-items: baseline; gap: 12px; flex-wrap: wrap; }
  header h1 { font-size: 17px; margin: 0; font-weight: 600; }
  header span { color: #7d8291; font-size: 12px; }
  main { display: grid; grid-template-columns: 390px 1fr; gap: 20px; padding: 20px 24px; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  .panel { background: #1b1d24; border: 1px solid #262833; border-radius: 10px; padding: 16px; }
  .row { margin-bottom: 12px; }
  .label { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }
  .label .title { margin: 0; }
  label.title { display: block; font-size: 12px; color: #9aa0ae; }
  .sub { display: block; font-size: 11px; color: #7d8291; margin-bottom: 3px; }
  input[type=text], select, textarea, input[type=number] {
    width: 100%; background: #12131a; color: #e6e8ee; border: 1px solid #2d3040;
    border-radius: 6px; padding: 7px 9px; font: inherit; }
  textarea { resize: vertical; min-height: 64px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .checks { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 6px; }
  .checks label { display: flex; gap: 6px; align-items: center; font-size: 13px; }
  .check { display: inline-flex; align-items: center; gap: 4px; }
  input[type=range] { width: 100%; }
  .value { color: #8ab4ff; font-variant-numeric: tabular-nums; }
  button { border: 1px solid #2f3a55; background: #23304a; color: #dce6ff;
           padding: 8px 16px; border-radius: 7px; font: inherit; cursor: pointer; }
  button:hover { background: #2b3b5c; }
  button.primary { background: #1f5f3f; border-color: #2c7a53; color: #d8ffe8; }
  button.primary:hover { background: #26734d; }
  .q { width: 18px; height: 18px; min-width: 18px; padding: 0; border-radius: 50%;
       border: 1px solid #39405a; background: #222839; color: #9db3e6; font-size: 12px;
       line-height: 1; display: inline-grid; place-items: center; cursor: pointer; }
  .q:hover { background: #2e3a58; color: #e2eaff; border-color: #4b5a80; }
  .q:focus-visible { outline: 2px solid #4f7fd0; outline-offset: 2px; }
  .actions { display: flex; gap: 10px; margin-top: 16px; align-items: center; }
  .stats { font-size: 12px; color: #9aa0ae; margin-bottom: 12px; line-height: 1.9; }
  .stats b { color: #e6e8ee; font-weight: 600; }
  .preview-box { display: flex; justify-content: center; align-items: flex-start;
                 min-height: 260px; background: #101116; border: 1px dashed #2a2d3a;
                 border-radius: 8px; padding: 12px; overflow: auto; }
  .preview-box img { image-rendering: pixelated; max-width: 100%; }
  .preview-box img:not([src]) { display: none; }
  .hint { font-size: 12px; color: #6f7482; margin-top: 6px; }
  .hint code, .modal code { background: #12131a; border: 1px solid #262a38; border-radius: 4px;
                            padding: 0 4px; font-size: 12px; }
  .result { margin-top: 14px; font-size: 13px; background: #12131a; border: 1px solid #262833;
            border-radius: 8px; padding: 12px; display: none; }
  .result.show { display: block; }
  .result a { color: #8ab4ff; }
  .error { color: #ff8f8f; }
  .ok { color: #6fe3a0; }
  .warn { color: #ffc83c; }

  /* “?” 说明面板 */
  .overlay { position: fixed; inset: 0; z-index: 50; background: rgba(8, 9, 12, .74);
             display: flex; align-items: center; justify-content: center; padding: 20px; }
  .overlay[hidden] { display: none; }
  .modal { width: min(820px, 100%); min-width: 0; max-height: 86vh; overflow: auto;
           background: #1b1d24; border: 1px solid #33384c; border-radius: 12px;
           box-shadow: 0 24px 70px rgba(0, 0, 0, .55); }
  .modal:focus { outline: none; }
  .modal-head { position: sticky; top: 0; z-index: 1; display: flex; align-items: center;
                justify-content: space-between; gap: 12px; padding: 14px 18px;
                background: #1b1d24; border-bottom: 1px solid #262833; }
  .modal-head h2 { margin: 0; font-size: 15px; font-weight: 600; }
  .close { border: 0; background: none; color: #8b90a0; font-size: 22px; line-height: 1;
           padding: 0 4px; cursor: pointer; }
  .close:hover { background: none; color: #e6e8ee; }
  .close:focus-visible { outline: 2px solid #4f7fd0; outline-offset: 2px; border-radius: 4px; }
  .modal-body { padding: 16px 18px 20px; font-size: 13px; line-height: 1.8; }
  .modal-body p { margin: 0 0 10px; }
  .modal-body ul { margin: 0 0 10px; padding-left: 20px; }
  .modal-body li { margin: 2px 0; }
  .modal-body h3 { font-size: 12.5px; margin: 16px 0 6px; color: #c9cede; font-weight: 600; }
  table.kv, table.cmp { width: 100%; border-collapse: collapse; margin: 10px 0 12px;
                        font-size: 12.5px; }
  table.kv th, table.kv td, table.cmp th, table.cmp td {
    padding: 5px 8px; border-bottom: 1px solid #262833; text-align: left; vertical-align: top; }
  table.kv th { width: 96px; font-weight: 500; color: #8f95a6; }
  table.cmp th { font-weight: 500; color: #8f95a6; }
  table.cmp td:first-child, table.cmp th:first-child { width: 64px; color: #8f95a6; }
  table.kv td { word-break: break-all; }
  .compare { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
             gap: 12px; margin: 12px 0; }
  .compare figure { margin: 0; min-width: 0; text-align: center; }
  .compare img { width: 100%; height: auto; image-rendering: pixelated;
                 background: #101116; border: 1px solid #2a2d3a; border-radius: 6px; }
  .compare figcaption { font-size: 12px; color: #9aa0ae; margin-top: 5px; }
  .loading { color: #7d8291; font-size: 12px; padding: 10px 0; }
  pre.diagram { margin: 8px 0 12px; padding: 10px 12px; background: #101116;
                border: 1px solid #262a38; border-radius: 6px; color: #b9c6e6;
                font: 12px/1.7 Consolas, "Cascadia Mono", monospace; overflow-x: auto; }
  .note { font-size: 12px; color: #7d8291; border-left: 2px solid #2f3a55;
          padding-left: 10px; margin-top: 12px; }
</style>
</head>
<body>
<header>
  <h1>ChineseBitmap 字库生成器</h1>
  <span>多编码 · 任意语言 · 实时预览 · 点选项旁的 ? 看说明和前后对比</span>
</header>
<main>
  <section class="panel">
    <div class="row">
      <div class="label">
        <label class="title" for="fontSelect">字体文件（TTF/TTC/OTF）</label>
        <button type="button" class="q" data-help="font" aria-label="字体选项说明">?</button>
      </div>
      <select id="fontSelect"><option>正在读取字体列表…</option></select>
      <input id="fontIndex" type="hidden" value="0">
      <input id="font" type="text" placeholder="C:/Windows/Fonts/simsun.ttc"
             style="margin-top:6px; display:none;">
      <div class="hint">显示字体官方名称，TTC 展开为多个 face；列表里没有就选“自定义路径…”。</div>
    </div>

    <div class="row">
      <div class="label">
        <label class="title" for="charset">字符集 / 语言 / 编码</label>
        <button type="button" class="q" data-help="charset" aria-label="字符集说明">?</button>
      </div>
      <select id="charset"><option>正在读取字符集…</option></select>
      <input id="customCharset" type="text" placeholder="text:words.txt 或 range:U+4E00-U+9FFF"
             style="margin-top:6px; display:none;">
      <div class="hint" id="charsetHint">正在读取字符集信息…</div>
    </div>

    <div class="row">
      <div class="label">
        <label class="title" for="layout">UTF-8 布局</label>
        <button type="button" class="q" data-help="layout" aria-label="布局说明">?</button>
      </div>
      <select id="layout">
        <option value="auto" selected>auto（自动选更小的）</option>
        <option value="indexed">indexed（索引，适合稀疏字符）</option>
        <option value="dense">dense（BMP 密集，查找最快）</option>
      </select>
      <div class="hint">只影响 UTF-8；GB2312/GBK 等 DBCS 字符集固定为密集布局。</div>
    </div>

    <div class="grid2">
      <div class="row">
        <div class="label">
          <label class="title" for="size">点阵尺寸</label>
          <button type="button" class="q" data-help="size" aria-label="尺寸说明">?</button>
        </div>
        <select id="size">
          <option value="8">8 x 8</option>
          <option value="12">12 x 12</option>
          <option value="16" selected>16 x 16</option>
          <option value="24">24 x 24</option>
          <option value="32">32 x 32</option>
        </select>
        <div class="hint" id="sizeHint">16×16 · 每字 32 字节</div>
      </div>
      <div class="row">
        <div class="label">
          <label class="title" for="threshold">灰度阈值 <span class="value" id="thresholdValue">128</span></label>
          <button type="button" class="q" data-help="threshold" aria-label="阈值说明">?</button>
        </div>
        <input id="threshold" type="range" min="0" max="255" value="128">
        <div class="hint">越低越粗，越高越细</div>
      </div>
    </div>

    <div class="row">
      <div class="label">
        <span class="title">位置微调</span>
        <button type="button" class="q" data-help="offset" aria-label="偏移说明">?</button>
      </div>
      <div class="grid2">
        <div>
          <label class="sub" for="xOffset">X 偏移（右为正）</label>
          <input id="xOffset" type="number" value="0" min="-16" max="16">
        </div>
        <div>
          <label class="sub" for="yOffset">Y 偏移（下为正）</label>
          <input id="yOffset" type="number" value="0" min="-16" max="16">
        </div>
      </div>
      <div class="hint">整体平移字形，修正基线或边距偏差。</div>
    </div>

    <div class="checks">
      <span class="check">
        <label><input id="fit" type="checkbox"> 单元格居中</label>
        <button type="button" class="q" data-help="fit" aria-label="居中说明">?</button>
      </span>
      <span class="check">
        <label><input id="invert" type="checkbox"> 反色</label>
        <button type="button" class="q" data-help="invert" aria-label="反色说明">?</button>
      </span>
      <span class="check">
        <label><input id="lsb" type="checkbox"> 低位在前</label>
        <button type="button" class="q" data-help="lsb" aria-label="位序说明">?</button>
      </span>
    </div>
    <div class="hint">勾选项会写入 .cbm 文件头，运行时按标志解释。</div>

    <div class="row" style="margin-top:12px">
      <div class="label">
        <label class="title" for="sample">预览文字</label>
        <button type="button" class="q" data-help="sample" aria-label="预览文字说明">?</button>
      </div>
      <textarea id="sample">你好，世界 Hello 点阵字库</textarea>
      <div class="hint">只影响预览，不写入字库；留空展示字符集前 32 个字形。</div>
    </div>

    <div class="actions">
      <button id="previewBtn">预览</button>
      <button id="buildBtn" class="primary">生成字库</button>
      <button type="button" class="q" data-help="build" aria-label="生成说明">?</button>
    </div>
    <div class="hint"><span class="ok">绿框</span> = 正常收录；<span class="warn">黄框</span> = 字体缺字；<span class="error">红框</span> = 不在当前字符集。</div>
  </section>

  <section class="panel">
    <div class="stats" id="stats">尚未预览</div>
    <div class="preview-box"><img id="preview" alt="预览"></div>
    <div class="result" id="result"></div>
  </section>
</main>

<div class="overlay" id="helpOverlay" hidden>
  <div class="modal" id="helpModal" role="dialog" aria-modal="true" aria-labelledby="helpTitle" tabindex="-1">
    <div class="modal-head">
      <h2 id="helpTitle">说明</h2>
      <button type="button" class="close" id="helpClose" aria-label="关闭">×</button>
    </div>
    <div class="modal-body" id="helpBody"></div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let charsetOptions = [];
let fontCatalog = [];
let outDir = "";
let lastStats = null;
let statsSeq = 0;
let statsTimer = 0;
let previewSeq = 0;
let previewTimer = 0;
let helpKey = "";
let helpTrigger = null;

function payload() {
  const select = $("charset").value;
  const isCustom = select === "text:FILE" || select.startsWith("range:");
  return {
    font: $("font").value,
    font_index: parseInt($("fontIndex").value, 10) || 0,
    charset: isCustom ? $("customCharset").value : select,
    size: parseInt($("size").value, 10),
    threshold: parseInt($("threshold").value, 10),
    x_offset: parseInt($("xOffset").value, 10) || 0,
    y_offset: parseInt($("yOffset").value, 10) || 0,
    fit: $("fit").checked,
    invert: $("invert").checked,
    lsb_first: $("lsb").checked,
    layout: $("layout").value,
    sample: $("sample").value,
  };
}

const fmt = (value) => value == null ? "—" : Number(value).toLocaleString();
const bpg = (size) => Math.ceil(size / 8) * size;
const esc = (value) => String(value == null ? "" : value)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

function sizeText(s) {
  if (s.mode === "auto") {
    const dense = s.dense_bytes == null ? "不支持" : fmt(s.dense_bytes) + " B";
    return "索引约 " + fmt(s.indexed_bytes) + " B / 密集约 " + dense;
  }
  return fmt(s.bytes) + " B";
}

function selectedFont() {
  const value = $("fontSelect").value;
  if (value && value !== "__custom__") {
    try {
      const parsed = JSON.parse(value);
      const found = fontCatalog.find((f) => f.path === parsed.p && f.index === parsed.i);
      if (found) return found;
      return {name: parsed.p.split(/[\\/]/).pop(), path: parsed.p, index: parsed.i};
    } catch (error) { /* 落到自定义路径 */ }
  }
  const path = $("font").value;
  const index = parseInt($("fontIndex").value, 10) || 0;
  return {name: path ? path.split(/[\\/]/).pop() : "（未选择）", path, index};
}

function updateSizeHint() {
  const size = parseInt($("size").value, 10);
  $("sizeHint").textContent = `${size}×${size} · 每字 ${bpg(size)} 字节`;
}

function sizeShort(s) {
  if (s.mode === "auto") {
    const dense = s.dense_bytes == null ? "不支持" : fmt(s.dense_bytes);
    return `索引 ${fmt(s.indexed_bytes)} / 密集 ${dense} B`;
  }
  return `${fmt(s.bytes)} B`;
}

function updateHints(s) {
  const size = parseInt($("size").value, 10);
  let hint = `${s.encoding} · ${fmt(s.count)} 码位`;
  if (!s.indexed) hint += ` · ${s.ranges} 区间`;
  hint += ` · ${sizeShort(s)}`;
  if (s.indexed && s.dense_supported === false) hint += " · 仅索引";
  $("charsetHint").textContent = hint;
  $("sizeHint").textContent = `${size}×${size} · 每字 ${bpg(size)} 字节`;
}

function showError(message) {
  const box = $("result");
  box.className = "result show";
  box.innerHTML = `<span class="error">${esc(message)}</span>`;
}

async function fetchStats() {
  const response = await fetch("/api/stats", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload()),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "无法读取字符集信息");
  return data;
}

async function refreshStats() {
  const seq = ++statsSeq;
  try {
    const data = await fetchStats();
    if (seq !== statsSeq) return;
    lastStats = data;
    updateHints(data);
  } catch (error) { /* 自定义字符集尚未输入完整时静默 */ }
}

function scheduleStats() {
  clearTimeout(statsTimer);
  statsTimer = setTimeout(refreshStats, 150);
}

function schedulePreview(delay = 250) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(preview, delay);
}

async function loadOptions() {
  const response = await fetch("/api/options");
  const data = await response.json();
  fontCatalog = data.fonts || [];
  outDir = data.out_dir || "";

  const fontSelect = $("fontSelect");
  const fontInput = $("font");
  const fontIndex = $("fontIndex");
  const customFont = "__custom__";
  const fontValue = (font) => JSON.stringify({ p: font.path, i: font.index });
  const cjkName = /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/;
  const cjkFile = /(simsun|simhei|simkai|simfang|nsimsun|msyh|msjh|mingliu|msgothic|msmincho|malgun|nanum|meiryo|yugoth|deng\.ttf|harmonyos|noto.*cjk|sourcehan|wqy)/i;
  fontSelect.innerHTML = "";
  const recommended = document.createElement("optgroup");
  recommended.label = "推荐中日韩字体";
  const others = document.createElement("optgroup");
  others.label = "其他字体";
  data.fonts.forEach((font) => {
    const cjk = cjkName.test(font.name) || cjkFile.test(font.path);
    (cjk ? recommended : others).appendChild(new Option(font.name, fontValue(font)));
  });
  if (recommended.children.length) fontSelect.appendChild(recommended);
  if (others.children.length) fontSelect.appendChild(others);
  fontSelect.appendChild(new Option("自定义路径…", customFont));
  const preferred = data.fonts.find((f) => f.index === 0 && /simsun\.ttc$/i.test(f.path))
    || data.fonts.find((f) => f.index === 0 && /msyh\.ttc$/i.test(f.path))
    || data.fonts.find((f) => /msjh|simhei|noto.*cjk|wqy/i.test(f.path))
    || data.fonts[0];
  if (preferred) {
    fontSelect.value = fontValue(preferred);
    fontInput.value = preferred.path;
    fontIndex.value = String(preferred.index);
  } else {
    fontSelect.value = customFont;
    fontInput.style.display = "block";
    fontInput.value = "";
    fontIndex.value = "0";
  }
  fontSelect.addEventListener("change", () => {
    if (fontSelect.value === customFont) {
      fontInput.style.display = "block";
      fontInput.value = "";
      fontIndex.value = "0";
      fontInput.focus();
    } else {
      const font = JSON.parse(fontSelect.value);
      fontInput.style.display = "none";
      fontInput.value = font.p;
      fontIndex.value = String(font.i);
      schedulePreview(0);
    }
  });
  fontInput.addEventListener("change", () => {
    if (!fontInput.value.trim()) return;
    fontIndex.value = "0";
    schedulePreview(0);
  });

  const select = $("charset");
  select.innerHTML = "";
  const dense = document.createElement("optgroup");
  dense.label = "密集编码（推荐，文件内按编码直接寻址）";
  const langs = document.createElement("optgroup");
  langs.label = "语言（UTF-8 索引模式）";
  const custom = document.createElement("optgroup");
  custom.label = "自定义";
  charsetOptions = data.charsets;
  data.charsets.forEach(([value, label]) => {
    if (value.startsWith("lang:")) {
      langs.appendChild(new Option(label, value));
    } else if (value === "text:FILE" || value.startsWith("range:")) {
      custom.appendChild(new Option(label, value));
    } else {
      dense.appendChild(new Option(label, value));
    }
  });
  select.append(dense, langs, custom);
  select.value = "gb2312";
  $("charsetHint").textContent = "正在估算字符集…";
}

async function preview() {
  const box = $("result");
  box.className = "result";
  const seq = ++previewSeq;
  try {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (seq !== previewSeq) return;
    if (!response.ok) { showError(data.error || "预览失败"); return; }
    $("preview").src = data.png;
    const s = data.stats;
    lastStats = s;
    updateHints(s);
    const sizeLine = s.mode === "auto"
      ? `索引约 <b>${fmt(s.indexed_bytes)} B</b> / 密集约 <b>${s.dense_bytes == null ? "不支持" : fmt(s.dense_bytes) + " B"}</b>（构建时按缺字选择）`
      : `预计 <b>${fmt(s.bytes)} B</b>`;
    const rangeLine = s.indexed ? "" : ` · 区间 <b>${s.ranges}</b>`;
    $("stats").innerHTML =
      `编码 <b>${s.encoding}</b> · 布局 <b>${s.mode}</b> · ` +
      `码位 <b>${fmt(s.count)}</b>${rangeLine}<br>${sizeLine}`;
  } catch (error) {
    if (seq === previewSeq) showError("请求失败：" + error);
  }
}

async function build() {
  const box = $("result");
  box.className = "result";
  box.textContent = "正在生成…";
  try {
    const response = await fetch("/api/build", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (!response.ok) { showError(data.error || "生成失败"); return; }
    box.className = "result show";
    box.innerHTML =
      `<span class="ok">生成成功</span><br>` +
      `文件：${esc(data.path)}<br>` +
      `大小：${fmt(data.bytes)} 字节 · 字形：${data.count} · ` +
      `编码：${esc(data.encoding)} · 模式：${esc(data.mode)}` +
      (data.skipped ? `<br><span class="error">已跳过 ${data.skipped} 个字体缺失字符</span>` : "") +
      (data.missing && !data.skipped ? `<br><span class="error">字体缺少 ${data.missing} 个字形（已输出空白）</span>` : "") +
      `<br><a href="${data.download}">下载字库文件</a>`;
  } catch (error) {
    showError("请求失败：" + error);
  }
}

/* ------------------------------------------------------------------ */
/* “?” 说明面板                                                        */
/* ------------------------------------------------------------------ */
const HELP = {
  font: {
    title: "字体文件",
    body: () => {
      const f = selectedFont();
      return `
        <p>下拉里的名字来自字体文件内部的 <code>name</code> 表，和 Word 里看到的一致，不是文件名。一个 <code>.ttc</code> 是字体集合，会展开成多个 face，例如宋体 / 新宋体。</p>
        <h3>当前选择</h3>
        <table class="kv">
          <tr><th>当前字体</th><td>${esc(f.name)}</td></tr>
          <tr><th>文件</th><td><code>${esc(f.path || "（未选择）")}</code></td></tr>
          <tr><th>TTC face</th><td>${f.index}</td></tr>
        </table>
        <p>列表里没有的字体，选“自定义路径…”直接填 <code>.ttf/.ttc/.otf</code> 的完整路径即可。字体本身不会嵌入字库，只有渲染后的点阵会保存。</p>
        <p>如果字体缺少某个字，预览会标黄：索引模式生成时跳过，密集模式写空白占位。</p>`;
    },
    note: "换字体后会自动刷新预览；生成前建议再确认一次缺字和字形风格。"
  },

  charset: {
    title: "字符集 / 语言 / 编码",
    body: `<p>决定字库里收录哪些字，也决定文件里“键”的编码方式。</p>
      <ul>
        <li><b>密集编码</b>：GB2312 / GBK / Big5 / Shift-JIS / EUC-KR / Latin-1 / ASCII。键就是编码值，按公式直接定位，查找最快、文件最小。</li>
        <li><b>语言</b>：统一用 UTF-8，按 Unicode 码位收录。适合中文以外的语言，或需要混合语言的场景。</li>
        <li><b>自定义</b>：<code>text:words.txt</code> 只收录文件里出现过的字符；<code>range:U+4E00-U+9FFF</code> 收录指定区段。</li>
      </ul>`,
    dynamic: async () => {
      const s = await fetchStats();
      const dense = s.indexed
        ? (s.dense_supported ? `可用，${fmt(s.dense_slots)} 个槽位` : `不可用：${esc(s.dense_reason)}`)
        : "可用（当前编码即密集布局）";
      return `
        <h3>当前配置</h3>
        <table class="kv">
          <tr><th>编码</th><td>${esc(s.encoding)}（id ${s.encoding_id}）</td></tr>
          <tr><th>码位 / 区间</th><td>${s.indexed ? `${fmt(s.count)} 个（UTF-8 索引）` : `${fmt(s.count)} 个 / ${s.ranges} 个区间`}</td></tr>
          <tr><th>布局</th><td>${s.mode}${s.indexed ? "" : "（DBCS 固定密集）"}</td></tr>
          <tr><th>体积估算</th><td>${sizeText(s)}</td></tr>
          <tr><th>密集布局</th><td>${dense}</td></tr>
        </table>
        <p>字符集里无法渲染的控制符会被去掉；生成时还会再检查字体是否真的有这个字形。</p>`;
    },
    note: "体积随字符数和点阵尺寸增长；先估一下再生成。"
  },

  layout: {
    title: "UTF-8 布局",
    body: `<p>只对 UTF-8 字符集生效；GB2312/GBK 等 DBCS 编码本身就是密集布局。</p>
      <ul>
        <li><b>indexed</b>：文件里存一张排序后的码位表，查找时二分。适合字符稀疏，或包含 BMP 以外字符（emoji）的情况。</li>
        <li><b>dense</b>：不存索引，用区间表 + 公式直接算偏移。查找只需 0 次索引读，但要求所有码位在 BMP 内、区间数不超过 8 个。</li>
        <li><b>auto</b>：生成时先渲染一遍，比较“跳过缺字后的索引体积”和“密集槽位体积”，谁小用谁。</li>
      </ul>
      <pre class="diagram">indexed:  key ──二分查找──▶ 码位表 ──▶ 字形偏移 ──▶ 字形
dense:    key ──区间公式──▶ 偏移 ──▶ 字形</pre>`,
    dynamic: async () => {
      const s = await fetchStats();
      if (!s.indexed) {
        return `<h3>当前配置</h3><p>当前字符集是 <b>${esc(s.encoding)}</b>，固定使用密集布局，预计 <b>${fmt(s.bytes)} B</b>。</p>`;
      }
      const reads = Math.max(1, Math.ceil(Math.log2(Math.max(2, s.count))));
      const denseBytes = s.dense_bytes == null ? "不支持" : fmt(s.dense_bytes) + " B";
      const chosen = $("layout").value;
      const chosenText = chosen === "auto"
        ? "生成时按实际缺字情况二选一"
        : (chosen === "indexed" ? "强制索引布局" : "强制密集布局");
      return `
        <h3>当前配置</h3>
        <table class="cmp">
          <tr><th></th><th>indexed</th><th>dense</th></tr>
          <tr><th>体积</th><td>${fmt(s.indexed_bytes)} B</td><td>${denseBytes}</td></tr>
          <tr><th>查找</th><td>约 ${reads} 次索引读 + 1 次字形读</td><td>0 次索引读 + 1 次字形读</td></tr>
          <tr><th>限制</th><td>无（支持非 BMP）</td><td>${s.dense_supported ? "BMP、≤ 8 区间" : esc(s.dense_reason)}</td></tr>
        </table>
        <p>当前选择 <b>${chosen}</b>：${chosenText}。</p>`;
    },
    note: "对 UTF-8 字符集，auto 的最终选择在“生成”时确定，预览里的数字只是估算。"
  },

  size: {
    title: "点阵尺寸",
    body: () => {
      const rows = [8, 12, 16, 24, 32].map((n) =>
        `<tr><th>${n}×${n}</th><td>每字 ${bpg(n)} 字节</td><td>${n <= 12 ? "小屏 / 低分辨率" : (n === 16 ? "最常用" : "高分辨率 / 更清晰")}</td></tr>`
      ).join("");
      return `<p>每个字都渲染到 <code>尺寸×尺寸</code> 的方格，所有字形等宽等高，运行时不需要测量宽度。</p>
        <p>尺寸翻倍，体积约变四倍：</p>
        <h3>每字体积</h3>
        <table class="kv">${rows}</table>
        <p>8/12 适合小屏，16 最常用，24/32 适合高分辨率或需要更清晰笔画的场景。</p>`;
    },
    compare: "size",
    note: "对比图把不同尺寸放大到接近的显示高度，方便看笔画细节。"
  },

  threshold: {
    title: "灰度阈值",
    body: `<p>字体先渲染成灰度图，再逐像素判断：灰度 ≥ 阈值记为 1（笔画），否则记为 0（背景）。</p>
      <p>因此阈值<b>越低</b>，越多灰色边缘被算成笔画，字越<b>粗</b>；阈值越高字越<b>细</b>，过高会断笔。</p>
      <p>默认 128。小字号笔画容易断，可适当降低；大字号想清爽一点可以调高。右侧预览保持灰度，方便看字形；生成时才按阈值二值化。</p>`,
    compare: "threshold",
    note: "三张图分别按 64 / 128 / 192 二值化，放大显示。"
  },

  offset: {
    title: "位置微调（X / Y 偏移）",
    body: `<p>渲染时整体平移字形，单位是像素。X 正数向右，Y 正数向下。</p>
      <p>不同字体的基线和边距不一样，出现顶部被裁、底部留白过多或整体偏左时，用这里修正。</p>`,
    compare: "offset",
    note: "对比图使用当前字体的实际字形；偏移过大可能把笔画裁掉。"
  },

  fit: {
    title: "单元格居中",
    body: `<p>默认按字体原始基线绘制，同一行文字共享度量，高低一致。</p>
      <p>勾选后对<b>每个字</b>单独计算墨迹外框并居中。适合单字、图标字；正文慎用，因为“一”和“国”会被各自居中，基线会跳动。</p>`,
    compare: "fit",
    note: "注意对比图里“一”的垂直位置和“A”的水平位置变化。"
  },

  invert: {
    title: "反色",
    body: `<p>决定字库里 bit=1 表示笔画还是背景。勾选后所有位取反，并在文件头写入 inverted 标志。</p>
      <p>很多单色 OLED/LCD 驱动把 1 当作“点亮”。如果你的屏是白底黑字、或驱动约定相反，就勾选它。</p>`,
    compare: "invert",
    note: "对比图模拟最终 1bpp 输出；实际显示颜色还取决于屏和驱动。"
  },

  lsb: {
    title: "低位在前（LSB first）",
    body: `<p>只影响每行像素打包成字节时的位序，不改变字形。</p>
      <h3>位序示例</h3>
      <table class="kv">
        <tr><th>一行像素</th><td><code>1 1 0 0 0 0 0 0</code></td></tr>
        <tr><th>高位在前</th><td><code>0xC0</code>（默认）</td></tr>
        <tr><th>低位在前</th><td><code>0x03</code></td></tr>
      </table>
      <p>和屏幕驱动或取模软件保持一致即可：SSD1306 常见低位在前，不少国产取模软件默认高位在前。文件头会记录这个标志，刷屏代码按标志取位。</p>`,
    note: "示例代码 example/selfLib.c 的 DraBitMap 已经按 lsb_first 参数处理。"
  },

  sample: {
    title: "预览文字",
    body: `<p>只用于右侧预览，<b>不会写进字库</b>。支持中文、英文、日文、韩文等，取前 64 个字符，换行忽略。</p>
      <p>留空时自动展示字符集里的前 32 个字形。</p>
      <p>边框颜色：<span class="ok">绿</span> = 正常收录；<span class="warn">黄</span> = 字体缺字；<span class="error">红</span> = 不在当前字符集。</p>`,
    note: "改预览文字不会改变生成文件；要改收录范围请换字符集。"
  },

  build: {
    title: "生成与输出",
    body: () => `<p>把当前配置渲染成 <code>.cbm</code> 字库，写到输出目录，并在下方给出下载链接。文件名是 <code>字符集_尺寸.cbm</code>。</p>
      <table class="kv">
        <tr><th>输出目录</th><td><code>${esc(outDir || "build/ui")}</code></td></tr>
        <tr><th>文件头</th><td>48 字节，含尺寸、编码、布局、字形数、CRC32</td></tr>
        <tr><th>布局</th><td>auto 生成时二选一；indexed 存码位表；dense 存区间表</td></tr>
        <tr><th>缺字</th><td>indexed 跳过；dense 写空白占位</td></tr>
        <tr><th>配套</th><td><code>src/cbm.c</code> + <code>src/cbm.h</code>，零 malloc</td></tr>
      </table>
      <p>生成后可以点“下载字库文件”保存，也可以直接到输出目录取用。</p>`,
    note: "生成过程只在本机进行，不会上传字体或字符集。"
  }
};

function openHelp(key) {
  const entry = HELP[key];
  if (!entry) return;
  helpKey = key;
  helpTrigger = document.activeElement;
  $("helpTitle").textContent = entry.title;
  const staticHtml = typeof entry.body === "function" ? entry.body() : (entry.body || "");
  $("helpBody").innerHTML = staticHtml +
    (entry.dynamic ? `<div id="helpDynamic"></div>` : "") +
    (entry.compare ? `<h3>对比示例</h3><div class="compare" id="helpCompare"></div>` : "") +
    (entry.note ? `<p class="note">${entry.note}</p>` : "");
  $("helpOverlay").hidden = false;
  document.body.style.overflow = "hidden";
  $("helpModal").focus();

  if (entry.dynamic) {
    const slot = $("helpDynamic");
    slot.innerHTML = `<div class="loading">正在计算…</div>`;
    entry.dynamic().then((html) => {
      if (helpKey === key) slot.innerHTML = html;
    }).catch((error) => {
      if (helpKey === key) slot.innerHTML = `<p class="error">${esc(error.message || error)}</p>`;
    });
  }
  if (entry.compare) loadCompare(entry.compare, key);
}

function closeHelp() {
  if ($("helpOverlay").hidden) return;
  $("helpOverlay").hidden = true;
  document.body.style.overflow = "";
  helpKey = "";
  if (helpTrigger && helpTrigger.focus) helpTrigger.focus();
}

async function loadCompare(kind, key) {
  const slot = $("helpCompare");
  slot.innerHTML = `<div class="loading">正在渲染对比图…</div>`;
  try {
    const response = await fetch("/api/compare", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...payload(), kind}),
    });
    const data = await response.json();
    if (helpKey !== key) return;
    if (!response.ok) throw new Error(data.error || "对比图生成失败");
    slot.innerHTML = data.cells.map((cell) =>
      `<figure><img src="${cell.png}" alt="${esc(cell.label)}">` +
      `<figcaption>${esc(cell.label)}</figcaption></figure>`
    ).join("");
  } catch (error) {
    if (helpKey === key) slot.innerHTML = `<p class="error">${esc(error.message || error)}</p>`;
  }
}

/* ------------------------------------------------------------------ */
/* 事件                                                                */
/* ------------------------------------------------------------------ */
$("threshold").addEventListener("input", (event) => {
  $("thresholdValue").textContent = event.target.value;
});
$("size").addEventListener("change", () => {
  updateSizeHint();
  scheduleStats();
  schedulePreview();
});
$("layout").addEventListener("change", () => {
  scheduleStats();
  schedulePreview();
});
$("charset").addEventListener("change", () => {
  const value = $("charset").value;
  const isCustom = value === "text:FILE" || value.startsWith("range:");
  $("customCharset").style.display = isCustom ? "block" : "none";
  if (isCustom) $("customCharset").value = value;
  if (value === "text:FILE") {
    $("charsetHint").textContent = "把 FILE 换成文本文件路径，例如 text:words.txt";
    return;
  }
  scheduleStats();
  schedulePreview();
});
$("customCharset").addEventListener("input", () => {
  if ($("customCharset").value === "text:FILE") return;
  scheduleStats();
});
$("customCharset").addEventListener("change", () => {
  if ($("customCharset").value === "text:FILE") return;
  schedulePreview(0);
});
$("xOffset").addEventListener("input", () => schedulePreview(300));
$("yOffset").addEventListener("input", () => schedulePreview(300));
$("fit").addEventListener("change", () => schedulePreview(0));
$("invert").addEventListener("change", () => schedulePreview(0));
$("sample").addEventListener("input", () => schedulePreview(400));
$("previewBtn").addEventListener("click", preview);
$("buildBtn").addEventListener("click", build);

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-help]");
  if (trigger) {
    event.preventDefault();
    openHelp(trigger.dataset.help);
  }
});
$("helpClose").addEventListener("click", closeHelp);
$("helpOverlay").addEventListener("click", (event) => {
  if (event.target === $("helpOverlay")) closeHelp();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("helpOverlay").hidden) closeHelp();
});

loadOptions().then(() => {
  refreshStats();
  preview();
}).catch((error) => showError("加载选项失败：" + error));
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChineseBitmap Web 配置界面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out-dir", default=str(ROOT / "build" / "ui"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    server = None
    port = args.port
    last_error: OSError | None = None
    for offset in range(20):
        port = args.port + offset
        try:
            server = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError as exc:  # 端口被占用或落在系统保留区
            last_error = exc
            server = None
    if server is None:
        raise SystemExit(f"无法绑定 {args.host} 上的端口: {last_error}")
    server.out_dir = out_dir  # type: ignore[attr-defined]

    url = f"http://{args.host}:{port}/"
    print(f"ChineseBitmap UI: {url}")
    print(f"输出目录: {out_dir}")
    print("按 Ctrl+C 退出。")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
