#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 资源与高清图表提取工具
==========================
功能：
1. 自动从论文 PDF 提取所有内嵌插图到 assets 目录
2. 过滤过小图标/Logo
3. 支持对指定矢量图或表格区域进行高分辨率（3x 超采样）截图
"""

import argparse
import os
import sys
from pathlib import Path

# Windows 控制台编码保护
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def extract_images(pdf_path: str, output_dir: str, min_w: int = 100, min_h: int = 100) -> int:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("⚠ 缺少 PyMuPDF 依赖，请运行: pip install pymupdf")
        return 0

    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    img_count = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)
        page_imgs = 0

        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception as e:
                print(f"  [跳过] 第{page_num + 1}页 xref={xref} 提取异常: {e}")
                continue

            image_bytes = base_image.get("image")
            image_ext = base_image.get("ext", "png")
            w = base_image.get("width", 0)
            h = base_image.get("height", 0)

            # 过滤微小图标
            if w < min_w or h < min_h:
                continue

            img_count += 1
            page_imgs += 1

            if len(image_list) > 1:
                filename = f"figure_p{page_num+1}_{page_imgs}.{image_ext}"
            else:
                filename = f"figure_{img_count}.{image_ext}"

            filepath = os.path.join(output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)
            print(f"  ✓ [已提取] {filename} (第{page_num + 1}页, {w}x{h})")

    print(f"\n共成功提取 {img_count} 张图片 -> {output_dir}/")
    if img_count == 0:
        print("  ℹ 提示: PDF 未包含位图或主要使用矢量图。可通过 screenshot_page_region 进行高清区域截图。")

    doc.close()
    return img_count


def screenshot_page_region(pdf_path: str, page_num: int, rect: tuple, output_file: str, scale: float = 3.0):
    """
    对 PDF 指定页面的指定矩形区域进行 3x 超采样高清截图（适用于矢量框架图或复杂大表）
    rect: (x0, y0, x1, y1)
    """
    try:
        import fitz
    except ImportError:
        print("⚠ 缺少 PyMuPDF 依赖，请运行: pip install pymupdf")
        return False

    doc = fitz.open(pdf_path)
    if page_num < 1 or page_num > len(doc):
        print(f"⚠ 页码越界: {page_num} (总页数 {len(doc)})")
        return False

    page = doc[page_num - 1]
    clip = fitz.Rect(*rect)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
    
    out_p = Path(output_file)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_p))
    print(f"  ✓ [区域高清截图已保存] {output_file} (第{page_num}页)")
    doc.close()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF 资源与高清图表提取工具")
    parser.add_argument("pdf", help="输入的 PDF 文件路径")
    parser.add_argument("output_dir", nargs="?", default="assets", help="图片保存目录 (默认: assets)")
    parser.add_argument("--min-width", type=int, default=100, help="最小宽度过滤阈值")
    parser.add_argument("--min-height", type=int, default=100, help="最小高度过滤阈值")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"错误: 找不到指定 PDF 文件 - {args.pdf}")
        sys.exit(1)

    extract_images(args.pdf, args.output_dir, args.min_width, args.min_height)
