#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阅读器部署与数据校验工具
========================
职责：
1. 校验论文目录下的 paper-data.js 是合法 JSON 且结构合规（结构规范见 references/paper_data_schema.md）
2. 把 skill 内置的固定阅读器三件套（reader.html / reader.js / styles.css）复制到论文目录
   —— 模板更新后重跑本脚本即可覆盖升级，AI 永远不需要重写 HTML

用法:
  python deploy_reader.py --vault <PaperVault路径> --paper <paper_id> [--skill-dir <skill根目录>]
"""

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TEMPLATE_FILES = ["reader.html", "reader.js", "styles.css"]
V3_TEMPLATE_FILES = {"reader-v3.html": "reader.html", "reader-v3.js": "reader-v3.js", "styles-v3.css": "styles-v3.css"}
VALID_BLOCK_TYPES = {"para", "formula", "figure", "table", "list", "algorithm", "diagram"}
VALID_ANN_TYPES = {"concept", "key-conclusion", "method", "formula", "figure",
                   "experiment", "reading-reminder", "critical-thinking"}


def load_paper_data(js_path: Path):
    """Strict JSON assignment parser, shared by HTTP launcher and bridge."""
    text = Path(js_path).read_text(encoding="utf-8-sig")
    match = re.match(r"^\s*window\.PAPER_DATA\s*=\s*", text)
    if not match:
        raise ValueError("paper-data.js must contain one window.PAPER_DATA JSON assignment")
    body = text[match.end():].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("PAPER_DATA must be an object")
    return data


def validate_v3(data, paper_dir):
    errors, warnings = [], []
    for key in ("id", "content_version", "overview", "explanation", "sections"):
        if key not in data:
            errors.append("v3 缺少字段: " + key)
    if not data.get("title_en") and not data.get("title_cn"):
        errors.append("title_en / title_cn 至少要有一个")
    views = {"body": data.get("sections", [])}
    for name in ("overview", "explanation"):
        view = data.get(name, {})
        if not isinstance(view, dict):
            errors.append(name + " 必须是对象")
            continue
        views[name] = view.get("sections", [])
    seen = set()
    for view_name, sections in views.items():
        if not isinstance(sections, list) or not sections:
            errors.append(view_name + ".sections 不能为空")
            continue
        section_ids = set()
        for section in sections:
            if not isinstance(section, dict):
                errors.append(view_name + " section 必须是对象")
                continue
            sid = section.get("id")
            if not sid or sid in section_ids:
                errors.append(view_name + " section id 缺失或重复: " + str(sid))
            section_ids.add(sid)
            for block in section.get("blocks", []):
                if not isinstance(block, dict):
                    errors.append("block 必须是对象")
                    continue
                bid = block.get("id")
                # IDs are global: anchors/history need deterministic lookup across views.
                if not bid or bid in seen:
                    errors.append("v3 block id 缺失或跨视图重复: " + str(bid))
                seen.add(bid)
                btype = block.get("type")
                if btype not in VALID_BLOCK_TYPES | {"paragraph", "note", "heading"}:
                    errors.append("block 类型非法: " + str(bid) + " / " + str(btype))
                if btype == "formula":
                    latex = block.get("latex")
                    if not isinstance(latex, str) or not latex.strip():
                        errors.append("formula " + str(bid) + " 必须提供可渲染 latex（图片不能替代）")
                    else:
                        errors.extend("formula " + str(bid) + ": " + issue for issue in validate_latex_structure(latex))
                if view_name == "body" and btype in ("para", "paragraph"):
                    for field in ("text_en", "text_zh"):
                        if not block.get(field):
                            errors.append("原文 block " + str(bid) + " 缺少 " + field)
                if view_name in ("overview", "explanation") and not block.get("source_refs") and not block.get("legacy"):
                    warnings.append("AI讲解 block " + str(bid) + " 无 source_refs")
                refs = block.get("source_refs", [])
                if not isinstance(refs, list):
                    errors.append("block " + str(bid) + " source_refs 必须是数组")
                else:
                    for ref in refs:
                        if not isinstance(ref, dict) or (ref.get("page") is not None and (type(ref["page"]) is not int or ref["page"] < 1)):
                            errors.append("block " + str(bid) + " source_refs 页码非法")
                for field in ("asset", "src"):
                    relative = block.get(field)
                    if not relative:
                        continue
                    if not isinstance(relative, str):
                        errors.append("block 图片路径必须是字符串: " + str(bid))
                        continue
                    target = (paper_dir / relative).resolve()
                    try:
                        target.relative_to(paper_dir.resolve())
                    except ValueError:
                        errors.append("block 图片路径越出论文目录: " + str(bid))
                        continue
                    if not target.is_file():
                        errors.append("block 图片不存在: " + str(bid) + " / " + relative)
    return errors, warnings


def validate_latex_structure(latex):
    """Basic structural checks only. Browser math rendering is still required QA."""
    issues, balance = [], 0
    for match in re.finditer(r"(?<!\\)[{}]", latex):
        balance += 1 if match[0] == "{" else -1
        if balance < 0:
            issues.append("LaTeX 花括号闭合先于开启")
            break
    if balance != 0:
        issues.append("LaTeX 花括号不平衡")
    for opening, closing in ((r"\left", r"\right"), (r"\(", r"\)"), (r"\[", r"\]")):
        def count(command):
            if command in (r"\left", r"\right"):
                return len(re.findall(re.escape(command) + r"(?![A-Za-z])", latex))
            return latex.count(command)
        if count(opening) != count(closing):
            issues.append("LaTeX 分隔符不平衡: " + opening + "/" + closing)
    if re.search(r"(?<!\\)\$", latex) and len(re.findall(r"(?<!\\)\$", latex)) % 2:
        issues.append("LaTeX $ 分隔符不平衡")
    return issues


def validate_paper_data(js_path: Path, paper_dir: Path):
    """返回 (errors, warnings)。errors 非空则部署失败。"""
    errors, warnings = [], []
    text = js_path.read_text(encoding="utf-8")

    m = re.match(r"^\s*window\.PAPER_DATA\s*=\s*", text)
    if not m:
        errors.append("文件必须以 `window.PAPER_DATA = {` 开头（只允许这一条语句）")
        return errors, warnings
    body = text[m.end():].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()

    prefix_lines = text[: m.end()].count("\n")  # 前缀通常 0 行
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        line = prefix_lines + e.lineno
        errors.append(
            f"JSON 解析失败（约第 {line} 行, 第 {e.colno} 列）: {e.msg}\n"
            f"  常见原因: ① LaTeX 反斜杠未双写（\\frac 应写成 \\\\frac）；② 数组/对象末尾多了逗号；③ 字符串里的双引号未转义"
        )
        return errors, warnings

    # 顶层字段
    if data.get("schema_version", 1) >= 3:
        return validate_v3(data, paper_dir)
    for k in ("id", "sections"):
        if k not in data:
            errors.append(f"缺少顶层必填字段: {k}")
    if not data.get("title_en") and not data.get("title_cn"):
        errors.append("title_en / title_cn 至少要有一个")
    if data.get("id") and paper_dir.name and data["id"] != paper_dir.name:
        warnings.append(f"数据 id ({data['id']}) 与文件夹名 ({paper_dir.name}) 不一致，建议统一")

    sections = data.get("sections", [])
    if not isinstance(sections, list) or not sections:
        errors.append("sections 不能为空——双语正文必须覆盖论文全部章节")

    # pipeline（v2：goal/input/tech 黑盒/gain 必填，why 可选）
    pipeline = data.get("pipeline", [])
    if not pipeline:
        warnings.append("pipeline 为空：方法流水线通俗拆解缺失（Pass 2 核心要求）")
    for i, p in enumerate(pipeline, 1):
        for k in ("name", "goal", "input", "tech", "gain"):
            if not p.get(k):
                warnings.append(f"pipeline 节点 {i} 缺少 {k}")
        if p.get("how") or p.get("pitfalls"):
            warnings.append(f"pipeline 节点 {i} 含 v1 字段 how/pitfalls（v2 已废除：技术黑盒原则，注意事项用 reading-reminder 批注承载）")

    if not data.get("novelty"):
        warnings.append("novelty 缺失：一句话创新点是大致理解的关键锚点")
    if not data.get("self_check"):
        warnings.append("self_check 缺失：复述自测题是 Pass 2 '大致理解'的验收工具")

    # level（挡位：novice 新手档 / expert 熟练档，生成时记录）
    if "level" in data and data["level"] not in ("novice", "expert"):
        errors.append(f"level 非法: {data['level']!r}（允许 novice / expert）")

    # pass3（第三遍产出：边界 + Gap，宽松校验）
    p3 = data.get("pass3")
    if p3 is not None:
        if not isinstance(p3, dict):
            errors.append("pass3 必须是对象（boundary / gaps）")
        else:
            b = p3.get("boundary")
            if b is not None and not isinstance(b, dict):
                errors.append("pass3.boundary 必须是对象（assumptions / failure_cases / cost 均为字符串数组）")
            gaps = p3.get("gaps")
            if gaps is not None:
                if not isinstance(gaps, list):
                    errors.append("pass3.gaps 必须是数组")
                else:
                    for gi, g in enumerate(gaps, 1):
                        if not isinstance(g, dict) or not g.get("idea"):
                            errors.append(f"pass3.gaps 第 {gi} 项缺少 idea 字段")

    # sections / blocks
    seen_ids = set()
    sec_ids = set()
    for si, sec in enumerate(sections, 1):
        sid = sec.get("id")
        if not sid:
            errors.append(f"第 {si} 个 section 缺少 id")
        elif sid in sec_ids:
            errors.append(f"section id 重复: {sid}")
        else:
            sec_ids.add(sid)
        if sec.get("page") is not None and not isinstance(sec.get("page"), (int, float)):
            warnings.append(f"section [{sid}] 的 page 应为数字（原文 PDF 页码，用于侧栏联动跳页）")
        blocks = sec.get("blocks", [])
        if not blocks:
            warnings.append(f"section [{sid}] 没有任何 block")
        for bi, b in enumerate(blocks, 1):
            bid = b.get("id")
            if not bid:
                errors.append(f"section [{sid}] 第 {bi} 个 block 缺少 id")
            elif bid in seen_ids:
                errors.append(f"block id 重复: {bid}（重复 ID 会让已标记的疑问指向错误内容）")
            else:
                seen_ids.add(bid)
            btype = b.get("type")
            if btype not in VALID_BLOCK_TYPES:
                errors.append(f"block [{bid}] 类型非法: {btype}（允许: {sorted(VALID_BLOCK_TYPES)}）")
            if btype == "formula" and not b.get("latex"):
                errors.append(f"formula block [{bid}] 缺少 latex 字段")
            if btype == "para" and not b.get("text_zh"):
                errors.append(f"para block [{bid}] 缺少 text_zh（中文译文）")
            if btype == "table" and not b.get("table_html"):
                errors.append(f"table block [{bid}] 缺少 table_html")
            if btype == "algorithm" and not b.get("algo_lines"):
                errors.append(f"algorithm block [{bid}] 缺少 algo_lines")
            if btype == "diagram":
                svg = b.get("svg", "")
                if not svg:
                    errors.append(f"diagram block [{bid}] 缺少 svg 字段（内联 SVG 字符串）")
                elif not str(svg).lstrip().lower().startswith("<svg"):
                    errors.append(f"diagram block [{bid}] 的 svg 字段必须以 <svg 开头（不支持其他标记）")
            if btype == "figure":
                asset = b.get("asset")
                if asset and not (paper_dir / asset).exists():
                    warnings.append(f"figure block [{bid}] 引用的图片不存在: {asset}")
            for ai, a in enumerate(b.get("annotations", []), 1):
                if a.get("type") not in VALID_ANN_TYPES:
                    errors.append(f"block [{bid}] 第 {ai} 条批注类型非法: {a.get('type')}")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description="部署阅读器三件套并校验 paper-data.js")
    parser.add_argument("--vault", required=True, help="PaperVault 根目录路径")
    parser.add_argument("--paper", required=True, help="paper_id（论文文件夹名）")
    parser.add_argument("--skill-dir", default=None, help="skill 根目录（默认: 本脚本上上级）")
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    assets_dir = skill_dir / "assets"

    paper_dir = Path(args.vault).resolve() / "papers" / args.paper
    if not paper_dir.is_dir():
        print(f"✗ 论文目录不存在: {paper_dir}")
        sys.exit(1)

    js_path = paper_dir / "paper-data.js"
    if not js_path.is_file():
        print(f"✗ 未找到 {js_path}\n  请先按 references/paper_data_schema.md 生成 paper-data.js 再部署")
        sys.exit(1)

    errors, warnings = validate_paper_data(js_path, paper_dir)
    for w in warnings:
        print(f"  ⚠ {w}")
    if errors:
        print(f"\n✗ paper-data.js 校验失败（{len(errors)} 个错误）：")
        for e in errors:
            print(f"  ✗ {e}")
        print("\n修复后重新运行本脚本。阅读器未部署。")
        sys.exit(1)

    data = json.loads(re.sub(r"^\s*window\.PAPER_DATA\s*=\s*", "", js_path.read_text(encoding="utf-8")).strip().rstrip(";"))
    n_blocks = sum(len(s.get("blocks", [])) for s in data.get("sections", []))
    print(f"✓ paper-data.js 校验通过: {len(data.get('sections', []))} 章节 / {n_blocks} blocks / pipeline {len(data.get('pipeline', []))} 节点")

    template_map = V3_TEMPLATE_FILES if data.get("schema_version", 1) >= 3 else {name: name for name in TEMPLATE_FILES}
    for source_name, output_name in template_map.items():
        src, dst = assets_dir / source_name, paper_dir / output_name
        if not src.is_file():
            print(f"✗ 模板缺失: {src}")
            sys.exit(1)
        shutil.copyfile(src, dst)
        if output_name == "reader.html":
            # 缓存粉碎：给三件套引用打上部署时间戳版本号，浏览器必取新版
            ts = str(int(time.time()))
            html = dst.read_text(encoding="utf-8")
            html = re.sub(r"(paper-data\.js|reader(?:-v3)?\.js|styles(?:-v3)?\.css)(\?v=\d+)?", r"\1?v=" + ts, html)
            dst.write_text(html, encoding="utf-8")
        print(f"  ✓ 已部署 {output_name}")

    if data.get("schema_version", 1) >= 3:
        math_dir = assets_dir / "math"
        if math_dir.is_dir():
            shutil.copytree(math_dir, paper_dir / "math", dirs_exist_ok=True)
            print("  ✓ 已部署本地 math 静态依赖")
        else:
            print("  ⚠ assets/math 暂未提供：公式视觉渲染仍需验证，结构校验不是渲染校验")

    # 批注文件桩：不存在则自动创建（AI 随时可 Read；已存在则不动，保住用户批注）
    ann_path = paper_dir / "annotations.json"
    if not ann_path.exists():
        stub = {"paper_id": args.paper, "updated_at": "", "annotations": []}
        ann_path.write_text(json.dumps(stub, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  ✓ 已创建批注桩文件 annotations.json")

    print(f"\n🌐 阅读器就绪: {paper_dir / 'reader.html'}")
    print("   打开方式: python open_reader.py --vault <库路径> " + args.paper)


if __name__ == "__main__":
    main()
