#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML 转高保真矢量 PDF 转换器
============================
功能：
1. 调用 Playwright / Headless Chrome 将 HTML 渲染导出为 A4 打印级矢量 PDF
2. 确保 MathJax 公式渲染完毕、图片加载完毕后再进行 PDF 导出
3. 避免任何跨平台公式与排版变形
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Windows 控制台编码保护
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


async def render_pdf(html_path: str, pdf_path: str):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("⚠ 缺少 playwright 依赖，请运行: pip install playwright && playwright install chromium")
        return False

    html_file = Path(html_path).resolve()
    pdf_file = Path(pdf_path).resolve()
    pdf_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"  正在启动无头浏览器渲染 HTML -> PDF: {html_file.name}")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # 加载 HTML
        await page.goto(f"file:///{html_file.as_posix()}", wait_until="networkidle")

        # 等待 MathJax 渲染完成
        try:
            await page.wait_for_function(
                "() => window.MathJax && window.MathJax.startup && window.MathJax.startup.promise ? window.MathJax.startup.promise.then(() => true) : true",
                timeout=10000
            )
        except Exception:
            pass

        # 稍作等待以确保图片/字体完全解算
        await asyncio.sleep(1.0)

        # 导出 PDF
        await page.pdf(
            path=str(pdf_file),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"}
        )

        await browser.close()

    print(f"✓ 高保真 PDF 已生成: {pdf_file}")
    return True


def main():
    parser = argparse.ArgumentParser(description="HTML 转高保真 PDF 脚本")
    parser.add_argument("html", help="输入的 HTML 文件路径")
    parser.add_argument("pdf", nargs="?", help="输出的 PDF 文件路径 (默认同名 .pdf)")
    args = parser.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"错误: HTML 文件不存在 - {args.html}")
        sys.exit(1)

    pdf_path = Path(args.pdf) if args.pdf else html_path.with_suffix(".pdf")
    asyncio.run(render_pdf(str(html_path), str(pdf_path)))


if __name__ == "__main__":
    main()
