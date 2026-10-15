#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阅读器本地服务
==============
以 http://127.0.0.1:<port> 提供论文目录的静态文件，并通过固定 API 读写
annotations.json——阅读器里的批注零弹窗自动保存（浏览器对 file:// 页面写盘
必须用户逐次授权，换成同源 API 即可完全绕开）。

用法:
  python serve_reader.py --vault <PaperVault路径> --paper <paper_id> [--port 0] [--no-open]

API（仅限同源，写路径服务端写死）:
  GET  /api/ping          -> {"ok": true, "paper_id": "..."}
  GET  /api/annotations   -> {"mtime": <float>, "data": {...}}   # 磁盘无文件时返回空结构
  PUT  /api/annotations   -> 原子写 <paper_dir>/annotations.json

安全边界: 仅绑定 127.0.0.1；只服务该论文目录；PUT 只写死路径 annotations.json。
"""

import argparse
import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MAX_BODY = 10 * 1024 * 1024  # 10MB 上限，防异常载荷


def empty_annotations(paper_id: str) -> dict:
    return {"paper_id": paper_id, "updated_at": "", "annotations": []}


def read_annotations(path: Path, paper_id: str):
    """返回 (mtime, data)。文件缺失/损坏时返回空结构（mtime=0，让客户端感知"初始"）。"""
    try:
        mtime = path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
        return mtime, data
    except Exception:
        return 0.0, empty_annotations(paper_id)


def atomic_write(path: Path, raw: bytes):
    """先写临时文件再替换，避免写一半被读到。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


class ReaderHandler(BaseHTTPRequestHandler):
    # 由 main() 注入
    paper_dir: Path = None
    paper_id: str = ""
    ann_path: Path = None

    def log_message(self, fmt, *args):
        pass  # 安静模式：不刷屏（保存很频繁）

    # ---------- 工具 ----------

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 路由 ----------

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/ping":
            return self._json(200, {"ok": True, "paper_id": self.paper_id})
        if path == "/api/annotations":
            mtime, data = read_annotations(self.ann_path, self.paper_id)
            return self._json(200, {"mtime": mtime, "data": data})
        return self._serve_static(path)

    def do_PUT(self):
        if urlparse(self.path).path != "/api/annotations":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._json(400, {"ok": False, "error": "bad length"})
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict) or not isinstance(obj.get("annotations", []), list):
                raise ValueError("annotations must be a list")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"invalid json: {e}"})
        try:
            atomic_write(self.ann_path, raw)
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"write failed: {e}"})
        return self._json(200, {"ok": True, "mtime": self.ann_path.stat().st_mtime})

    def do_POST(self):
        self.do_PUT()  # 宽容动词差异

    def do_HEAD(self):
        """缓存协商探活：只回头部不回正文"""
        path = unquote(urlparse(self.path).path)
        target = (self.paper_dir / path.lstrip("/")).resolve() if path not in ("", "/") else self.paper_dir / "reader.html"
        try:
            target.relative_to(self.paper_dir.resolve())
        except ValueError:
            return self._json(403, {"ok": False, "error": "forbidden"})
        if not target.is_file():
            return self._json(404, {"ok": False, "error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    # ---------- 静态文件 ----------

    def _serve_static(self, path: str):
        if path in ("", "/"):
            path = "/reader.html"
        target = (self.paper_dir / path.lstrip("/")).resolve()
        try:
            target.relative_to(self.paper_dir.resolve())  # 防目录穿越
        except ValueError:
            return self._json(403, {"ok": False, "error": "forbidden"})
        if not target.is_file():
            return self._json(404, {"ok": False, "error": "not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description="阅读器本地服务（零弹窗自动保存批注）")
    parser.add_argument("--vault", required=True, help="PaperVault 根目录路径")
    parser.add_argument("--paper", required=True, help="paper_id（论文文件夹名）")
    parser.add_argument("--port", type=int, default=0, help="端口（0 = 自动分配空闲端口）")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器（供探活/代理调用）")
    args = parser.parse_args()

    paper_dir = Path(args.vault).resolve() / "papers" / args.paper
    if not paper_dir.is_dir():
        print(f"✗ 论文目录不存在: {paper_dir}")
        sys.exit(1)

    ReaderHandler.paper_dir = paper_dir
    ReaderHandler.paper_id = args.paper
    ReaderHandler.ann_path = paper_dir / "annotations.json"

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), ReaderHandler)
    except OSError as e:
        print(f"✗ 端口 {args.port} 绑定失败: {e}（可用 --port 0 让系统自动分配）")
        sys.exit(1)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/reader.html"

    print(f"✓ 阅读器服务就绪: {url}")
    print(f"  服务目录: {paper_dir}")
    print("  批注经 API 自动写入 annotations.json（零弹窗）；Ctrl+C 停止服务")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == "__main__":
    main()
