#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PaperVault 资产库管理器
=======================
用于管理科研论文阅读沉淀的资产库（PaperVault），支持：
1. 初始化工作区 PaperVault/ 资产库
2. 登记/更新文献阅读状态 (unread | triage_passed | rejected | reading | completed)
3. 跨设备导出完整资产包 (zip)
4. 跨设备导入与无损合并去重
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

# Windows 控制台编码保护
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def get_default_vault_dir(base_dir: Path = None) -> Path:
    """获取默认的 PaperVault 路径（当前工作区/PaperVault）"""
    if base_dir is None:
        base_dir = Path.cwd()
    return Path(base_dir) / "PaperVault"


def init_vault(vault_dir: Path) -> dict:
    """初始化 PaperVault 目录与 library.json"""
    vault_dir = Path(vault_dir)
    papers_dir = vault_dir / "papers"
    exports_dir = vault_dir / "exports"
    papers_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)

    lib_file = vault_dir / "library.json"
    if not lib_file.exists():
        initial_data = {
            "version": "1.0.0",
            "vault_name": "Academic PaperVault",
            "created_at": datetime.datetime.now().isoformat(),
            "updated_at": datetime.datetime.now().isoformat(),
            "stats": {
                "total_papers": 0,
                "completed": 0,
                "reading": 0,
                "triage_passed": 0,
                "rejected": 0,
                "unread": 0
            },
            "papers": []
        }
        with open(lib_file, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=2)
        print(f"✓ 已成功初始化文献资产库: {vault_dir}")
        return initial_data
    else:
        with open(lib_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data


def load_library(vault_dir: Path) -> dict:
    """加载 library.json，如果不存在则自动初始化"""
    lib_file = Path(vault_dir) / "library.json"
    if not lib_file.exists():
        return init_vault(vault_dir)
    with open(lib_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_library(vault_dir: Path, data: dict):
    """保存 library.json 并更新统计数据"""
    vault_dir = Path(vault_dir)
    lib_file = vault_dir / "library.json"
    
    # 更新统计指标
    stats = {
        "total_papers": len(data.get("papers", [])),
        "completed": 0,
        "reading": 0,
        "triage_passed": 0,
        "rejected": 0,
        "unread": 0
    }
    for p in data.get("papers", []):
        st = p.get("status", "unread")
        if st in stats:
            stats[st] += 1
    data["stats"] = stats
    data["updated_at"] = datetime.datetime.now().isoformat()

    with open(lib_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def upsert_paper(vault_dir: Path, paper_info: dict) -> dict:
    """添加或更新文献条目"""
    vault_dir = Path(vault_dir)
    data = load_library(vault_dir)
    paper_id = paper_info.get("id")
    if not paper_id:
        raise ValueError("paper_info 必须包含唯一 'id' 字段")

    # 创建该论文专属目录
    paper_folder = vault_dir / "papers" / paper_id
    paper_folder.mkdir(parents=True, exist_ok=True)
    (paper_folder / "assets").mkdir(exist_ok=True)

    # 检查是否已存在
    existing_idx = None
    for idx, p in enumerate(data.get("papers", [])):
        if p.get("id") == paper_id or (p.get("doi") and p.get("doi") == paper_info.get("doi")):
            existing_idx = idx
            break

    now_str = datetime.datetime.now().isoformat()
    if existing_idx is not None:
        # 合并更新
        old_item = data["papers"][existing_idx]
        old_item.update(paper_info)
        old_item["updated_at"] = now_str
        data["papers"][existing_idx] = old_item
        print(f"✓ 已更新文献条目: [{paper_id}] {old_item.get('title', '')}")
    else:
        paper_info["created_at"] = now_str
        paper_info["updated_at"] = now_str
        data["papers"].append(paper_info)
        print(f"✓ 已新增文献条目: [{paper_id}] {paper_info.get('title', '')}")

    # 保存单篇 metadata.json
    meta_file = paper_folder / "metadata.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(paper_info, f, ensure_ascii=False, indent=2)

    save_library(vault_dir, data)
    return paper_info


def update_status(vault_dir: Path, paper_id: str, new_status: str):
    """更新文献状态"""
    vault_dir = Path(vault_dir)
    data = load_library(vault_dir)
    found = False
    for p in data.get("papers", []):
        if p.get("id") == paper_id:
            p["status"] = new_status
            p["updated_at"] = datetime.datetime.now().isoformat()
            found = True
            break
    if not found:
        print(f"⚠ 未找到 ID 为 {paper_id} 的文献")
        return False
    save_library(vault_dir, data)
    print(f"✓ 文献 [{paper_id}] 状态已更新为: {new_status}")
    return True


def export_vault(vault_dir: Path, output_zip: Path = None) -> Path:
    """将整个 PaperVault 打包导出为 zip 压缩包"""
    vault_dir = Path(vault_dir)
    if not vault_dir.exists():
        raise FileNotFoundError(f"PaperVault 目录不存在: {vault_dir}")

    if output_zip is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_zip = vault_dir.parent / f"PaperVault_Backup_{ts}.zip"
    else:
        output_zip = Path(output_zip)

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(vault_dir):
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(vault_dir.parent)
                zf.write(file_path, rel_path)

    print(f"✓ 文献资产包已成功打包导出至: {output_zip.resolve()}")
    return output_zip


def import_vault(source_path: Path, target_vault_dir: Path):
    """从 zip 压缩包或文件夹导入并无损合并到 target_vault_dir"""
    source_path = Path(source_path)
    target_vault_dir = Path(target_vault_dir)
    init_vault(target_vault_dir)

    temp_import_dir = None
    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        import tempfile
        temp_dir = Path(tempfile.mkdtemp(prefix="papervault_import_"))
        temp_import_dir = temp_dir
        with zipfile.ZipFile(source_path, "r") as zf:
            zf.extractall(temp_dir)
        # 查找解压后的 PaperVault 根目录
        candidate = temp_dir / "PaperVault"
        if candidate.exists():
            import_root = candidate
        else:
            import_root = temp_dir
    elif source_path.is_dir():
        import_root = source_path if (source_path / "library.json").exists() else (source_path / "PaperVault")
    else:
        raise FileNotFoundError(f"找不到有效的导入源: {source_path}")

    # 读取导入源的 library.json
    source_lib_file = import_root / "library.json"
    if not source_lib_file.exists():
        print(f"⚠ 警告: 导入源中未找到 library.json，尝试直接复制 papers 目录")
        source_papers = []
    else:
        with open(source_lib_file, "r", encoding="utf-8") as f:
            source_data = json.load(f)
            source_papers = source_data.get("papers", [])

    # 复制 papers 目录及文件
    source_papers_dir = import_root / "papers"
    target_papers_dir = target_vault_dir / "papers"
    if source_papers_dir.exists():
        for paper_folder in source_papers_dir.iterdir():
            if paper_folder.is_dir():
                dest_folder = target_papers_dir / paper_folder.name
                shutil.copytree(paper_folder, dest_folder, dirs_exist_ok=True)

    # 合并 library.json 条目
    for paper in source_papers:
        upsert_paper(target_vault_dir, paper)

    if temp_import_dir and temp_import_dir.exists():
        shutil.rmtree(temp_import_dir, ignore_errors=True)

    print(f"✓ 成功合并导入 {len(source_papers)} 篇文献到: {target_vault_dir.resolve()}")


def set_level(vault_dir: Path, level: str) -> bool:
    """设置整库阅读挡位（novice 新手档 / expert 熟练档）"""
    if level not in ("novice", "expert"):
        print("✗ level 必须是 novice（新手档）或 expert（熟练档）")
        return False
    data = load_library(vault_dir)
    data["level"] = level
    save_library(vault_dir, data)
    label = "新手档 (novice)" if level == "novice" else "熟练档 (expert)"
    print(f"✓ 整库阅读挡位已设为: {label}（新论文按此生成；旧论文更新需重跑 Pass 2）")
    return True


def list_papers(vault_dir: Path):
    """打印当前文献库的所有条目清单"""
    vault_dir = Path(vault_dir)
    data = load_library(vault_dir)
    papers = data.get("papers", [])
    stats = data.get("stats", {})
    level_map = {"novice": "新手档", "expert": "熟练档"}

    print("\n" + "=" * 70)
    print(f" 📚 PaperVault 文献资产库总览 (共 {len(papers)} 篇)")
    print(f" 状态分布: 在读 {stats.get('reading',0)} | 已读完 {stats.get('completed',0)} | 初筛通过 {stats.get('triage_passed',0)} | 淘汰 {stats.get('rejected',0)} | 未读 {stats.get('unread',0)}")
    lv = data.get("level")
    if lv:
        print(f" 阅读挡位: {level_map.get(lv, lv)}（切换: --set-level novice|expert）")
    print("=" * 70)
    
    if not papers:
        print(" (当前文献库为空)")
        print("=" * 70 + "\n")
        return

    for idx, p in enumerate(papers, 1):
        status_tag = f"[{p.get('status','unread')}]"
        priority_tag = f"<{p.get('priority','Medium')}>"
        title = p.get("title", "未知标题")
        authors = p.get("authors", "未知作者")
        year = p.get("year", "")
        pid = p.get("id", "")
        print(f" {idx:2d}. {status_tag:<16} {priority_tag:<8} {title} ({year})")
        print(f"     ID: {pid} | 作者: {authors}")
        if p.get("p1_summary"):
            s = p.get("p1_summary", {})
            print(f"     方向: {s.get('domain','')} | 核心问题: {s.get('core_problem','')[:50]}...")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="PaperVault 文献资产库管理工具")
    parser.add_argument("--vault", default=None, help="PaperVault 根目录路径 (默认: ./PaperVault)")
    parser.add_argument("--init", action="store_true", help="初始化文献库")
    parser.add_argument("--list", action="store_true", help="列出所有文献")
    parser.add_argument("--status", nargs=2, metavar=("PAPER_ID", "STATUS"), help="更新指定文献状态 (unread/triage_passed/rejected/reading/completed)")
    parser.add_argument("--set-level", metavar="LEVEL", help="设置整库阅读挡位 (novice=新手档 | expert=熟练档)")
    parser.add_argument("--get-level", action="store_true", help="查看整库阅读挡位")
    parser.add_argument("--export", nargs="?", const="", metavar="ZIP_PATH", help="打包导出完整资产库为 zip")
    parser.add_argument("--import-vault", metavar="SOURCE", help="从 zip 或文件夹导入文献并合并")
    args = parser.parse_args()

    vault_dir = Path(args.vault) if args.vault else get_default_vault_dir()

    if args.init:
        init_vault(vault_dir)
    elif args.list:
        list_papers(vault_dir)
    elif args.status:
        update_status(vault_dir, args.status[0], args.status[1])
    elif args.set_level:
        set_level(vault_dir, args.set_level)
    elif args.get_level:
        data = load_library(vault_dir)
        print(data.get("level") or "未设置")
    elif args.export is not None:
        out_zip = Path(args.export) if args.export else None
        export_vault(vault_dir, out_zip)
    elif args.import_vault:
        import_vault(Path(args.import_vault), vault_dir)
    else:
        list_papers(vault_dir)


if __name__ == "__main__":
    main()
