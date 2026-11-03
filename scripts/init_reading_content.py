#!/usr/bin/env python3
"""Prepare overview, explanation and a static Chinese method figure; defer translation."""
import argparse
import json
from pathlib import Path
import re

from content_contract import CONTRACT_VERSION

PRESETS = ("method", "theory", "survey", "system")
FRAMEWORK_ASSET = "assets/method-framework.svg"


def build_scaffold(paper_id, preset="method", title_en="", title_cn="", *, with_method_pipeline=None):
    """Default to a static figure; True/False retain the historical pipeline opt-in/opt-out."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", paper_id):
        raise ValueError("paper_id must be a safe paper directory ID")
    template_path = Path(__file__).resolve().parents[1] / "assets" / "content-templates" / (preset + ".json")
    if preset not in PRESETS:
        raise ValueError("unknown preset")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    checklist = []
    data = {"schema_version":3, "content_contract_version":CONTRACT_VERSION, "content_version":paper_id + "-draft-1",
        "content_status":"incomplete", "id":paper_id, "title_en":title_en, "title_cn":title_cn,
        "authors":[], "year":None, "venue":"", "level":"beginner",
        "learning_requirements":{"required_formula_ids":[]},
        "overview":{"sections":[]}, "explanation":{"sections":[]},
        "translation":{"status":"deferred"}, "sections":[]}
    if with_method_pipeline is not False:
        data["learning_requirements"]["overview_framework"] = {
            "section_id":"overview-method", "block_id":"overview-method-framework",
            "kind":"interactive_pipeline" if with_method_pipeline else "static_figure"}
    for view in ("overview", "explanation"):
        sections = data[view]["sections"]
        for item in template[view]:
            section_id = view + "-" + item["id"]
            block_id = section_id + "-p01"
            block = {"id":block_id, "type":"paragraph", "text_zh":"", "evidence_kind":"ai_explanation"}
            sections.append({"id":section_id,"title_cn":item["title"],"source_refs":[],"blocks":[block]})
            checklist.append({"view":view,"section_id":section_id,"block_id":block_id,"task":item["task"],"complete":False})
            if with_method_pipeline is not False and view == "overview" and item["id"] == "method":
                if with_method_pipeline:
                    sections[-1]["blocks"].append({"id":"overview-method-framework", "type":"learning_aid",
                        "placement":"body", "evidence_kind":"ai_explanation",
                        "aid":{"id":"overview-method-pipeline", "type":"method_pipeline", "version":1,
                            "title":"", "evidence_kind":"paper", "source_refs":[],
                            "config":{"summary":"", "nodes":[], "edges":[]}, "fallback_text":""}})
                    framework_task = "填写所需方法图的节点与连线，使用 prepare_method_pipeline.py 自动布局。"
                else:
                    sections[-1]["blocks"].insert(0, {"id":"overview-method-framework", "type":"figure",
                        "src":FRAMEWORK_ASSET, "alt":"论文完整方法架构图（中文，待按原文绘制）",
                        "caption_zh":"方法架构图占位：请依据原论文绘制完整中文框架，并补充来源页码。",
                        "evidence_kind":"paper", "source_refs":[]})
                    framework_task = "将 assets/method-framework.svg 替换为依据原论文绘制的完整静态中文架构图（也可改为 PNG 等本地图片），包含输入、主要模块、输出及真实分支或反馈，并补充来源。"
                checklist.append({"view":"overview", "section_id":section_id,"block_id":"overview-method-framework",
                    "task":framework_task, "complete":False})
    return data, {"paper_id":paper_id,"preset":preset,"status":"incomplete","authoring_note":template.get("authoring_note", ""),
        "workflow":"references/reader_workflow.md", "items":checklist,
        "component_suggestions":["method_pipeline"] if with_method_pipeline else []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-id", "--id", dest="paper_id", required=True)
    parser.add_argument("--preset", choices=PRESETS, default="method")
    parser.add_argument("--title-en", default="")
    parser.add_argument("--title-cn", "--title", dest="title_cn", default="")
    parser.add_argument("--output", type=Path, required=True, help="paper-data.js or editable JSON path")
    parser.add_argument("--force", action="store_true", help="explicitly overwrite an existing scaffold")
    parser.add_argument("--with-method-pipeline", action="store_true", default=None,
                        help="legacy opt-in: use an interactive method_pipeline instead of the default static figure")
    parser.add_argument("--write-todo", action="store_true", help="also write an optional authoring checklist")
    args = parser.parse_args(argv)
    data, todo = build_scaffold(args.paper_id, args.preset, args.title_en, args.title_cn,
                               with_method_pipeline=args.with_method_pipeline)
    todo_path = args.output.with_name(args.output.stem + "-todo.json")
    if not args.force and (args.output.exists() or (args.write_todo and todo_path.exists())):
        parser.error("output already exists; choose another location or explicitly use --force")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.with_method_pipeline:
        asset_path = args.output.parent / FRAMEWORK_ASSET
        if not asset_path.exists():
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_template = Path(__file__).resolve().parents[1] / "assets" / "content-templates" / "method-framework.svg"
            asset_path.write_text(asset_template.read_text(encoding="utf-8"), encoding="utf-8")
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output.suffix.lower() == ".js":
        serialized = "window.PAPER_DATA = " + serialized + ";"
    args.output.write_text(serialized + "\n", encoding="utf-8")
    if args.write_todo:
        todo_path.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Created incomplete scaffold: " + str(args.output.resolve()))
    print("Fill overview, explanation, the complete Chinese method figure and relevant figures/tables/formulas; set content_status to ready, then deploy.")
    if args.write_todo:
        print("Optional checklist: " + str(todo_path.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
