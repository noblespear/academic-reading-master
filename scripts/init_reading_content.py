#!/usr/bin/env python3
"""Prepare an incomplete body scaffold with explicitly deferred translation."""
import argparse
import json
from pathlib import Path
import re

from content_contract import CONTRACT_VERSION, FACTUAL_GATES

PRESETS = ("method", "theory", "survey", "system", "empirical", "argument")


def build_scaffold(paper_id, preset="method", title_en="", title_cn=""):
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
        "content_review":{"reviewer":"",
            "factual_gates":{key:False for key in FACTUAL_GATES}, "note":"待实际审阅者对照原文和示例，检查隐含前提、因果链条、选择理由及概念到数学的过渡，完成六项事实核验并如实署名；此记录不是自动认证。"},
        "learning_requirements":{"required_formula_ids":[],
            "overview_framework":{"section_id":"overview-method", "block_id":"overview-method-framework", "kind":"interactive_pipeline"}},
        "overview":{"sections":[]}, "explanation":{"sections":[]},
        "translation":{"status":"deferred"}, "sections":[]}
    for view in ("overview", "explanation"):
        sections = data[view]["sections"]
        for item in template[view]:
            section_id = view + "-" + item["id"]
            block_id = section_id + "-p01"
            block = {"id":block_id, "type":"paragraph", "text_zh":"", "evidence_kind":"ai_explanation"}
            sections.append({"id":section_id,"title_cn":item["title"],"source_refs":[],"blocks":[block]})
            checklist.append({"view":view,"section_id":section_id,"block_id":block_id,"task":item["task"],"complete":False})
            if view == "overview" and item["id"] == "method":
                sections[-1]["blocks"].append({"id":"overview-method-framework", "type":"learning_aid",
                    "placement":"body", "evidence_kind":"ai_explanation",
                    "aid":{"id":"overview-method-pipeline", "type":"method_pipeline", "version":1,
                        "title":"", "evidence_kind":"paper", "source_refs":[],
                        "config":{"summary":"", "nodes":[], "edges":[]}, "fallback_text":""}})
                checklist.append({"view":"overview", "section_id":section_id,"block_id":"overview-method-framework",
                    "task":"填写正文 method_pipeline：完整研究路径或论证/组织关系；节点说明含义、前提或材料及形成的认识，边说明实际关联与依据；用自动布局 helper 生成 row/column，审阅实际呈现。", "complete":False})
    checklist.extend([
        {"task":"更新 intermediate/reading-plan.md 的主线、疑点与实际修订。", "complete":False},
        {"task":"按短指南完成隔离草稿预览及本次范围的独立验收，如实填写审阅结果后交付。", "complete":False}])
    return data, {"paper_id":paper_id,"preset":preset,"status":"incomplete","authoring_note":template.get("authoring_note", ""),
        "workflow":"references/reader_workflow.md", "items":checklist,"component_suggestions":template.get("component_suggestions", [])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-id", "--id", dest="paper_id", required=True)
    parser.add_argument("--preset", choices=PRESETS, default="method")
    parser.add_argument("--title-en", default="")
    parser.add_argument("--title-cn", "--title", dest="title_cn", default="")
    parser.add_argument("--output", type=Path, required=True, help="paper-data.js or editable JSON path")
    parser.add_argument("--force", action="store_true", help="explicitly overwrite an existing scaffold")
    args = parser.parse_args(argv)
    data, todo = build_scaffold(args.paper_id, args.preset, args.title_en, args.title_cn)
    todo_path = args.output.with_name(args.output.stem + "-todo.json")
    if not args.force and (args.output.exists() or todo_path.exists()):
        parser.error("output already exists; choose another location or explicitly use --force")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output.suffix.lower() == ".js":
        serialized = "window.PAPER_DATA = " + serialized + ";"
    args.output.write_text(serialized + "\n", encoding="utf-8")
    todo_path.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Created incomplete scaffold: " + str(args.output.resolve()))
    print("Fill and review before deployment; checklist: " + str(todo_path.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
