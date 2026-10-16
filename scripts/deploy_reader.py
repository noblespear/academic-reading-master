#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阅读器部署与数据校验工具
========================
职责：
1. 校验论文目录下的 paper-data.js 是合法 JSON 且结构合规（结构规范见 references/paper_data_schema.md）
2. 把 skill 内置阅读器及共享内容、学习组件、数学资源复制到论文目录
   —— 模板更新后重跑本脚本即可覆盖升级，AI 永远不需要重写 HTML

用法:
  python deploy_reader.py --vault <PaperVault路径> --paper <paper_id> [--skill-dir <skill根目录>]
"""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TEMPLATE_FILES = ["reader.html", "reader.js", "styles.css"]
V3_TEMPLATE_FILES = {
    "reader-v3.html": "reader.html",
    "reader-v3.js": "reader-v3.js",
    "styles-v3.css": "styles-v3.css",
    "content-renderer.js": "content-renderer.js",
    "learning-components.js": "learning-components.js",
    "learning-visuals.css": "learning-visuals.css",
}
READER_VERSION = "3.1.0"
MANIFEST_NAME = "reader-manifest.json"
RICH_BLOCK_TYPES = {"code", "quote", "details", "learning_aid"}
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


def validate_v3(data, paper_dir, preview=False):
    from content_contract import is_translation_deferred
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
        if not isinstance(sections, list):
            errors.append(view_name + ".sections 必须是数组")
            continue
        if not sections:
            if not preview and not (view_name == "body" and is_translation_deferred(data)):
                errors.append(view_name + ".sections 不能为空")
            continue
        section_ids = set()
        for section in sections:
            if not isinstance(section, dict):
                errors.append(view_name + " section 必须是对象")
                continue
            sid = section.get("id")
            if not isinstance(sid, str) or not sid.strip() or sid in section_ids:
                errors.append(view_name + " section id 缺失或重复: " + str(sid))
            if isinstance(sid, str):
                section_ids.add(sid)
            blocks = section.get("blocks", [])
            if not isinstance(blocks, list):
                errors.append(view_name + " section.blocks 必须是数组")
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    errors.append("block 必须是对象")
                    continue
                bid = block.get("id")
                # IDs are global: anchors/history need deterministic lookup across views.
                if not isinstance(bid, str) or not bid.strip() or bid in seen:
                    errors.append("v3 block id 缺失或跨视图重复: " + str(bid))
                if isinstance(bid, str):
                    seen.add(bid)
                btype = block.get("type")
                if not isinstance(btype, str) or btype not in VALID_BLOCK_TYPES | {"paragraph", "note", "heading"} | RICH_BLOCK_TYPES:
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
                if view_name in ("overview", "explanation") and str(data.get("content_contract_version", "")) != "3.2" and not block.get("source_refs") and not block.get("legacy"):
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
    from content_contract import validate_learning_content
    contract_errors, contract_warnings = validate_learning_content(data, paper_dir, preview=preview)
    errors.extend(contract_errors)
    warnings.extend(contract_warnings)
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


def validate_paper_data(js_path: Path, paper_dir: Path, preview=False):
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
    if not isinstance(data, dict):
        return ["PAPER_DATA 必须是对象"], warnings
    schema_version = data.get("schema_version", 1)
    if type(schema_version) is not int or schema_version not in (1, 2, 3):
        return ["不支持的 schema_version；当前阅读器支持 1、2、3。"], warnings
    if schema_version == 3:
        return validate_v3(data, paper_dir, preview=preview)
    if preview:
        return ["隔离草稿预览要求 schema_version 3；请先迁移旧阅读器。"], warnings
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


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def normalized_template(content, name):
    """Ignore the cache query generated by old deployers, not user HTML edits."""
    if name == "reader.html":
        text = content.decode("utf-8-sig")
        text = re.sub(r"(?<=\.js)\?v=[a-zA-Z0-9.-]+|(?<=\.css)\?v=[a-zA-Z0-9.-]+", "", text)
        return text.replace("\r\n", "\n").encode("utf-8")
    return content.replace(b"\r\n", b"\n")


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def deploy_templates(paper_dir, skill_dir, data, replace_custom=False):
    """Prepare every asset before writes, protect local edits, roll back failed writes."""
    paper_dir, skill_dir = Path(paper_dir).resolve(), Path(skill_dir).resolve()
    assets_dir = skill_dir / "assets"
    is_v3 = data.get("schema_version") == 3
    template_map = V3_TEMPLATE_FILES if is_v3 else {name: name for name in TEMPLATE_FILES}
    prepared = {}
    for source_name, output_name in template_map.items():
        source = assets_dir / source_name
        if not source.is_file():
            raise ValueError("模板缺失: " + str(source))
        prepared[output_name] = source.read_bytes()
    if is_v3:
        math_dir = assets_dir / "math"
        if not (math_dir / "katex.min.js").is_file():
            raise ValueError("缺少本地数学渲染依赖 assets/math/katex.min.js")
        for source in sorted(math_dir.rglob("*")):
            if source.is_symlink():
                raise ValueError("数学依赖不允许符号链接: " + str(source))
            if source.is_file():
                prepared[source.relative_to(assets_dir).as_posix()] = source.read_bytes()
    content_hash = sha256_bytes((paper_dir / "paper-data.js").read_bytes())
    cache_hash = sha256_bytes(b"".join(prepared[name] for name in sorted(prepared)) + content_hash.encode())[:16]
    html = prepared["reader.html"].decode("utf-8-sig")
    cache_names = list(template_map.values()) + ["paper-data.js"]
    pattern = r"(" + "|".join(re.escape(name) for name in cache_names if name.endswith((".js", ".css"))) + r")(?:\?v=[\w.-]+)?"
    prepared["reader.html"] = re.sub(pattern, lambda match: match[1] + "?v=" + cache_hash, html).encode("utf-8")

    manifest_path = paper_dir / MANIFEST_NAME
    previous = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(previous.get("files"), dict):
                raise ValueError("missing file hashes")
        except (ValueError, TypeError) as error:
            if not replace_custom:
                raise ValueError("阅读器清单不可读；检查后可使用 --replace-custom-templates。") from error
            previous = {}
    legacy_path = assets_dir / "legacy-template-hashes.json"
    legacy = json.loads(legacy_path.read_text(encoding="utf-8")) if legacy_path.is_file() else {}
    changed = []
    for name, content in prepared.items():
        target = (paper_dir / name).resolve()
        if not target.is_relative_to(paper_dir):
            raise ValueError("模板目标越出论文目录: " + name)
        if not target.is_file():
            continue
        old = target.read_bytes()
        if old == content:
            continue
        recorded = previous.get("files", {}).get(name)
        known_legacy = sha256_bytes(normalized_template(old, name)) in legacy.get(name, [])
        same_template = normalized_template(old, name) == normalized_template(content, name)
        if (recorded and recorded != sha256_bytes(old)) or (not recorded and not known_legacy and not same_template):
            changed.append(name)
    if changed and not replace_custom:
        raise ValueError("发现自定义或来源不明的阅读器文件，未覆盖: " + ", ".join(changed) +
                         "。检查差异后可使用 --replace-custom-templates；原文件会留存备份。")
    if changed:
        import zipfile
        backup_id = sha256_bytes(b"".join((paper_dir / name).read_bytes() for name in sorted(changed)))[:16]
        backup = paper_dir / (".reader-template-backup-" + backup_id + ".zip")
        if not backup.exists():
            with zipfile.ZipFile(backup, "x", zipfile.ZIP_DEFLATED) as archive:
                for name in changed:
                    archive.writestr(name, (paper_dir / name).read_bytes())

    manifest = {
        "reader_version": READER_VERSION,
        "schema_version": data.get("schema_version", 1),
        "content_contract_version": data.get("content_contract_version"),
        "content_version": data.get("content_version"),
        "paper_data_sha256": content_hash,
        "capabilities": ["learning", "rich_content", "formula_explorer", "assistant_reconnect", "narrative_learning", "graph_walkthrough", "comparison_chart", "weighted_degree", "body_learning_aids"] if is_v3 else [],
        "files": {name: sha256_bytes(value) for name, value in prepared.items()},
    }
    prepared[MANIFEST_NAME] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    originals = {name: (paper_dir / name).read_bytes() if (paper_dir / name).is_file() else None for name in prepared}
    applied = []
    try:
        for name, content in prepared.items():
            atomic_write(paper_dir / name, content)
            applied.append(name)
    except Exception:
        for name in reversed(applied):
            target = (paper_dir / name).resolve()
            if not target.is_relative_to(paper_dir):
                raise ValueError("回退目标越出论文目录")
            if originals[name] is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write(target, originals[name])
        raise
    return manifest


def deploy_preview(paper_dir, skill_dir, preview_dir=None):
    """Build a static draft in an isolated directory; never touch the paper/state.

    An occupied destination gets a fresh sibling. An existing preview remains a
    reviewable snapshot instead of being silently changed beneath its reviewer.
    Only validated active assets and the confined source PDF are copied.
    """
    paper_dir, skill_dir = Path(paper_dir).resolve(), Path(skill_dir).resolve()
    js_path = paper_dir / "paper-data.js"
    errors, warnings = validate_paper_data(js_path, paper_dir, preview=True)
    if errors:
        raise ValueError("草稿结构/资源/安全校验失败: " + "; ".join(errors))
    source_data = load_paper_data(js_path)
    assets = set()
    from content_contract import _local_asset
    catalog = source_data.get("learning_catalog", {})
    aids = {aid["id"]:aid for aid in catalog.get("aids", [])}
    visited_aids = set()
    def register_image(src):
        issues = []
        _local_asset(src, "preview.image", issues, paper_dir)
        if issues:
            raise ValueError("; ".join(issues))
        target = (paper_dir / src).resolve()
        resolved_parts = target.relative_to(paper_dir).parts
        if (any(part.startswith(".") for part in resolved_parts) or
                target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}):
            raise ValueError("预览图像不能指向隐藏目录、批注或状态文件: " + src)
        assets.add((src.replace("\\", "/"), target))
    def collect_aid(aid):
        if not isinstance(aid, dict) or id(aid) in visited_aids:
            return
        visited_aids.add(id(aid))
        fallback = aid.get("fallback")
        if isinstance(fallback, dict):
            collect_blocks(fallback.get("blocks", []))
        if aid.get("type") in ("steps", "step_walkthrough"):
            for step in aid.get("config", {}).get("steps", []):
                if isinstance(step, dict):
                    collect_blocks(step.get("blocks", []))
    def collect_blocks(blocks):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind in ("figure", "diagram"):
                register_image(block.get("src", block.get("asset")))
            if kind in ("details", "quote"):
                collect_blocks(block.get("blocks", []))
            if kind == "learning_aid":
                collect_aid(block.get("aid") or aids.get(block.get("aid_id")))
            # Legacy learning is part of the validated old rendering contract.
            learning = block.get("learning")
            if isinstance(learning, dict):
                collect_blocks(learning.get("blocks", []))
    for view in (source_data.get("overview", {}), source_data.get("explanation", {})):
        for section in view.get("sections", []):
            collect_blocks(section.get("blocks", []))
    for section in source_data.get("sections", []):
        collect_blocks(section.get("blocks", []))
    for aid in aids.values():
        collect_aid(aid)
    pdf = paper_dir / "source.pdf"
    if pdf.exists():
        resolved_pdf = pdf.resolve()
        if (not resolved_pdf.is_relative_to(paper_dir) or not pdf.is_file() or
                resolved_pdf.suffix.lower() != ".pdf" or
                any(part.startswith(".") for part in resolved_pdf.relative_to(paper_dir).parts)):
            raise ValueError("预览 source.pdf 必须位于论文目录内")
        assets.add(("source.pdf", pdf))
    # Prepare bytes before creating/writing the destination.
    resources = {name: target.read_bytes() for name, target in assets}
    if preview_dir is None:
        # Do not call mkdtemp/gettempdir before isolation checks: even Python's
        # temp-root discovery may probe writability. Honor cached tempdir and
        # environment hints without writing anything into a protected vault.
        temp_hint = tempfile.tempdir
        if temp_hint is None:
            temp_hint = next((os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")
                if os.environ.get(name)), None)
        if temp_hint is None:
            temp_hint = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Temp"
                if os.name == "nt" else Path("/tmp"))
        temp_root = Path(os.fsdecode(temp_hint)).resolve()
        output = (temp_root / ("arm-draft-preview-" + uuid.uuid4().hex)).resolve()
    else:
        output = Path(preview_dir).resolve()
    source_vault = paper_dir.parent.parent
    for protected in (source_vault, skill_dir):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("预览目录必须与整个源文献库和 skill 目录隔离；请指定安全的 --preview-dir")
    requested_output = output
    while True:
        # Exclusive creation also handles concurrent revision/preview commands.
        # Never reuse or clear a destination, including unknown files/directories.
        try:
            output.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            output = requested_output.with_name(requested_output.name + "-" + uuid.uuid4().hex)
    draft = copy.deepcopy(source_data)
    original_id = draft["id"]
    draft["id"] = "preview-" + uuid.uuid4().hex
    draft["content_status"] = "incomplete"
    draft["preview_source_id"] = original_id
    atomic_write(output / "paper-data.js", ("window.PAPER_DATA = " +
        json.dumps(draft, ensure_ascii=False, indent=2) + ";\n").encode("utf-8"))
    for name, value in resources.items():
        atomic_write(output / name, value)
    manifest = deploy_templates(output, skill_dir, draft)
    config = b"window.READER_PREVIEW = true;\n"
    atomic_write(output / "preview-config.js", config)
    html = (output / "reader.html").read_text(encoding="utf-8")
    html = html.replace("<body>", '<body data-reader-preview="true"><div role="status" '
        'style="position:fixed;bottom:0;left:0;right:0;z-index:9999;padding:8px;background:#fff3cd;'
        'color:#664d03;text-align:center">草稿预览 · 尚未完成内容与事实审阅 · 批注与答疑停用</div>', 1)
    html = re.sub(r'(<script\s+src="paper-data\.js)', '<script src="preview-config.js"></script>\n  \\1', html, count=1)
    html = html.replace("</head>", '<style>body[data-reader-preview] #annotations-toggle,'
        'body[data-reader-preview] #selection-action,body[data-reader-preview] #stop-service,'
        'body[data-reader-preview] #assistant-actions,body[data-reader-preview] #service-help'
        '{display:none!important}body[data-reader-preview] .paper-footer{visibility:hidden}</style></head>', 1)
    atomic_write(output / "reader.html", html.encode("utf-8"))
    manifest.update({"reader_preview":True, "content_status":"incomplete", "source_paper_id":original_id,
        "source_data_sha256":sha256_bytes(js_path.read_bytes()), "warnings":warnings})
    manifest["files"].update({"reader.html":sha256_bytes(html.encode("utf-8")),
        "preview-config.js":sha256_bytes(config), "paper-data.js":sha256_bytes((output / "paper-data.js").read_bytes()),
        **{name:sha256_bytes(value) for name, value in resources.items()}})
    atomic_write(output / MANIFEST_NAME, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return output, manifest


def main():
    parser = argparse.ArgumentParser(description="部署阅读器资源并校验 paper-data.js")
    parser.add_argument("--vault", required=True, help="PaperVault 根目录路径")
    parser.add_argument("--paper", required=True, help="paper_id（论文文件夹名）")
    parser.add_argument("--skill-dir", default=None, help="skill 根目录（默认: 本脚本上上级）")
    parser.add_argument("--replace-custom-templates", action="store_true", help="备份后替换已检测到的本地模板改动")
    parser.add_argument("--preview", action="store_true", help="在隔离目录生成草稿预览，只跳过完成状态与完成审阅门槛")
    parser.add_argument("--preview-dir", type=Path, help="隔离预览目录；已存在时自动新建唯一同级目录（默认使用系统临时目录）")
    args = parser.parse_args()
    if args.preview_dir is not None and not args.preview:
        parser.error("--preview-dir 只能与 --preview 一起使用")

    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    assets_dir = skill_dir / "assets"

    papers_dir = Path(args.vault).resolve() / "papers"
    paper_dir = (papers_dir / args.paper).resolve()
    if paper_dir.parent != papers_dir.resolve():
        parser.error("--paper 必须是库内一个论文文件夹名")
    if not paper_dir.is_dir():
        print(f"✗ 论文目录不存在: {paper_dir}")
        sys.exit(1)

    js_path = paper_dir / "paper-data.js"
    if not js_path.is_file():
        print(f"✗ 未找到 {js_path}\n  请先按 references/paper_data_schema.md 生成 paper-data.js 再部署")
        sys.exit(1)

    if args.preview:
        try:
            output, manifest = deploy_preview(paper_dir, skill_dir, args.preview_dir)
        except (ValueError, OSError) as error:
            print("✗ 草稿预览未生成: " + str(error))
            sys.exit(1)
        for warning in manifest["warnings"]:
            print("  ⚠ " + warning)
        print("✓ 草稿预览（尚未完成审阅，非正式发布）: " + str(output / "reader.html"))
        print("  内容/资源/安全结构已校验；原论文与用户状态未修改。")
        print("  使用静态服务器或直接打开 HTML；预览不连接答疑 bridge。")
        return

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

    try:
        manifest = deploy_templates(paper_dir, skill_dir, data, args.replace_custom_templates)
    except (ValueError, OSError) as error:
        print("✗ 阅读器未部署: " + str(error))
        sys.exit(1)
    print(f"  ✓ 已部署 {len(manifest['files'])} 个文件，阅读器版本 {manifest['reader_version']}")

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
