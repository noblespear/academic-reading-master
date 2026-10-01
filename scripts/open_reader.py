#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打开论文阅读器（默认走本地服务，零弹窗自动保存）
==================================================
两种模式:
  默认 --serve : 启动 serve_reader.py（127.0.0.1 本地服务，常驻），浏览器打开
                 http://127.0.0.1:<port>/reader.html —— 批注经 API 直写论文目录，
                 全程零弹窗零授权；同时在论文目录生成"打开阅读器.bat"供用户日后双击自启。
  --file       : 直接以 file:// 打开 reader.html（兜底；批注走 FSA 授权链路）。

用法:
  python open_reader.py --vault <PaperVault路径> <paper_id>          # 服务模式
  python open_reader.py --vault <PaperVault路径> <paper_id> --file   # 直开文件
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import webbrowser
import time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler

from deploy_reader import load_paper_data

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SKILL_SCRIPTS = Path(__file__).resolve().parent
DEFAULT_PORT = 8790
LOCAL_HTTP = build_opener(ProxyHandler({}))


def resolve_paper_dir(target: str, vault_dir: Path) -> Path:
    """target 可以是 paper_id，也可以是 reader.html 的路径。返回论文目录。"""
    p = Path(target)
    if p.exists() and p.is_file() and p.suffix.lower() == ".html":
        return p.resolve().parent
    candidate = vault_dir / "papers" / target
    if (candidate / "reader.html").exists():
        return candidate
    print(f"⚠ 未找到论文伴读页面: {candidate / 'reader.html'}")
    print("  请先运行 deploy_reader.py 部署阅读器，或直接传入 reader.html 的路径")
    return None


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def write_bat(paper_dir: Path, vault_dir: Path, paper_id: str):
    """论文目录下生成双击自启脚本（用户不开 AI 也能进入零弹窗模式）。"""
    bat = paper_dir / "打开阅读器.bat"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "title 论文阅读器启动器",
        f'"{sys.executable}" "{SKILL_SCRIPTS / "open_reader.py"}" --serve --vault "{vault_dir}" {paper_id}',
        "exit /b",
    ]
    bat.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    print(f"  ✓ 已生成自启脚本: {bat.name}")


def open_serve(paper_dir: Path, vault_dir: Path, paper_id: str) -> int:
    serve_script = SKILL_SCRIPTS / "serve_reader.py"
    if not serve_script.is_file():
        print(f"✗ 缺少 {serve_script}，改用 file:// 模式")
        return open_file(paper_dir)
    write_bat(paper_dir, vault_dir, paper_id)
    port = DEFAULT_PORT if port_free(DEFAULT_PORT) else 0
    cmd = [sys.executable, str(serve_script), "--vault", str(vault_dir), "--paper", paper_id, "--port", str(port)]
    print(f"🌐 服务模式启动（端口 {'自动' if port == 0 else port}），浏览器将自动打开…")
    # 常驻子进程：由调用方（run_in_background / bat）维持生命周期
    proc = subprocess.run(cmd)
    return proc.returncode


def live_v3_state(vault_dir):
    """Reuse only a verified loopback v3 service for this vault."""
    path = vault_dir / ".reader" / "server.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        from urllib.parse import urlparse
        parsed = urlparse(state.get("url", ""))
        if state.get("stopped") or parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            return None
        with LOCAL_HTTP.open(state["url"] + "/api/ping", timeout=1) as response:
            ping = json.load(response)
        return state if ping.get("api_version") == 3 else None
    except (ValueError, OSError, KeyError):
        return None


def stop_v3(vault_dir):
    state = live_v3_state(vault_dir)
    if not state:
        print("阅读器 v3 服务已停止。")
        return 0
    request = Request(state["url"] + "/api/stop", data=b"{}", method="POST",
        headers={"Content-Type": "application/json", "X-Reader-Token": state["token"]})
    with LOCAL_HTTP.open(request, timeout=5) as response:
        json.load(response)
    print("已要求服务停止；当前模型 turn 将先 interrupt，随后退出 bridge 和服务。")
    return 0


def open_v3(paper_dir, vault_dir, args):
    from serve_reader_v3 import hidden_flags
    write_bat(paper_dir, vault_dir, paper_dir.name)
    state = live_v3_state(vault_dir)
    if not state:
        state_dir = vault_dir / ".reader"
        state_dir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(SKILL_SCRIPTS / "serve_reader_v3.py"), "--vault", str(vault_dir),
               "--paper", paper_dir.name, "--port", str(args.port), "--bridge", args.bridge, "--no-open"]
        for key in ("model", "effort", "codex_bin"):
            value = getattr(args, key)
            if value:
                cmd += ["--" + key.replace("_", "-"), value]
        if args.foreground:
            if not args.no_open:
                cmd.remove("--no-open")
            return subprocess.call(cmd)
        # OS launcher -> detached service supervisor -> bridge -> app-server.
        # No pipe or tool session keeps any process alive after this returns.
        with (state_dir / "service.log").open("ab", buffering=0) as log:
            child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=hidden_flags(detach=True), start_new_session=(os.name != "nt"), close_fds=True)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            state = live_v3_state(vault_dir)
            if state:
                break
            if child.poll() is not None:
                print("阅读器启动失败，查看:", state_dir / "service.log")
                return 1
            time.sleep(0.15)
        if not state:
            print("服务尚未就绪，查看:", state_dir / "service.log")
            return 1
    url = state["url"] + "/papers/" + paper_dir.name + "/reader.html"
    if not args.no_open:
        webbrowser.open(url)
    print("阅读器 v3:", url)
    print("后台服务和 bridge 会在聊天/网页关闭后继续；停止命令: python open_reader.py --vault", str(vault_dir), "--stop")
    return 0


def open_file(paper_dir: Path) -> int:
    target_html = paper_dir / "reader.html"
    print(f"🌐 正在以文件模式打开双语可交互伴读页面: {target_html}")
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target_html))
        else:
            webbrowser.open(target_html.as_uri())
        print("✓ 浏览器已成功调起！（file:// 模式：首次保存批注需按提示授权一次）")
        return 0
    except Exception as e:
        print(f"⚠ 调起浏览器失败: {e}，尝试使用 webbrowser.open...")
        webbrowser.open(target_html.as_uri())
        return 0


def main():
    parser = argparse.ArgumentParser(description="打开论文双语伴读阅读器（默认本地服务模式）")
    parser.add_argument("target", nargs="?", help="paper_id 或 reader.html 的路径")
    parser.add_argument("--vault", default=None, help="PaperVault 根目录（默认: ./PaperVault）")
    parser.add_argument("--file", action="store_true", help="跳过本地服务，直接以 file:// 打开（兜底）")
    parser.add_argument("--serve", action="store_true", help="兼容旧命令：默认即服务模式")
    parser.add_argument("--stop", action="store_true", help="停止此vault的v3服务、bridge与当前模型turn")
    parser.add_argument("--foreground", action="store_true", help="调试：前台运行v3服务")
    parser.add_argument("--no-open", action="store_true", help="启动但不打开浏览器")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--bridge", choices=("codex", "mock", "none"), default="codex")
    parser.add_argument("--model", help="可选宿主模型覆盖；默认继承宿主配置")
    parser.add_argument("--effort", help="可选推理强度覆盖；默认继承宿主配置")
    parser.add_argument("--codex-bin", help="可选codex可执行文件路径")
    args = parser.parse_args()

    vault_dir = (Path(args.vault).resolve() if args.vault else Path.cwd() / "PaperVault")
    if args.stop:
        return sys.exit(stop_v3(vault_dir))
    if not args.target:
        parser.error("需要target（或使用--stop）")
    paper_dir = resolve_paper_dir(args.target, vault_dir)
    if paper_dir is None:
        sys.exit(1)

    data_path = paper_dir / "paper-data.js"
    version = load_paper_data(data_path).get("schema_version", 1) if data_path.exists() else 1
    code = open_file(paper_dir) if args.file else (open_v3(paper_dir, vault_dir, args) if version >= 3 else open_serve(paper_dir, vault_dir, paper_dir.name))
    sys.exit(code)


if __name__ == "__main__":
    main()
