"""Additive overview method-framework registration and scaffold regressions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "assets" / "content-templates"
sys.path.insert(0, str(ROOT / "scripts"))
from content_contract import validate_learning_content
from init_reading_content import build_scaffold, PRESETS

REQUIREMENT_PATH = "learning_requirements.overview_framework"


def graph():
    return {"id":"method-graph", "type":"graph_walkthrough", "version":1,
        "evidence_kind":"teaching_example", "fallback_text":"输入经过处理得到输出。",
        "config":{"nodes":[{"id":"input","label":"输入"},{"id":"output","label":"输出"}],
            "edges":[{"id":"process","from":"input","to":"output"}],
            "stages":[{"id":"all","title":"处理","text":"沿连线查看输入到输出的关系。",
                "active_node_ids":["input","output"],"active_edge_ids":["process"]}]}}


def figure(kind="diagram"):
    return {"id":"overview-method-framework", "type":kind,
        "src":"example-assets/lru-mechanism.svg", "evidence_kind":"ai_explanation",
        "caption_zh":"方法处理关系。"}


def body_graph(catalog=False):
    block = {"id":"overview-method-framework", "type":"learning_aid",
        "placement":"body", "evidence_kind":"ai_explanation"}
    block.update({"aid_id":"method-graph"} if catalog else {"aid":graph()})
    return block


def paper(block=None):
    data = json.loads((TEMPLATES / "minimal-valid-example.json").read_text(encoding="utf-8"))
    data["learning_requirements"]["overview_framework"] = {
        "section_id":"overview-method", "block_id":"overview-method-framework"}
    data["overview"]["sections"].append({"id":"overview-method", "title_cn":"方法框架",
        "blocks":[figure() if block is None else block]})
    return data


class OverviewFrameworkTests(unittest.TestCase):
    def assert_error(self, data, message, paper_dir=TEMPLATES):
        errors, _ = validate_learning_content(data, paper_dir)
        self.assertTrue(any(message in error for error in errors), errors)

    def test_older_ready_contracts_and_legacy_papers_do_not_require_registration(self):
        for filename in ("minimal-valid-example.json", "complete-cache-example.json"):
            with self.subTest(filename=filename):
                data = json.loads((TEMPLATES / filename).read_text(encoding="utf-8"))
                self.assertNotIn("overview_framework", data.get("learning_requirements", {}))
                self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))
        legacy = {"schema_version":3, "id":"legacy", "overview":{"sections":[]},
            "explanation":{"sections":[]}, "sections":[]}
        self.assertEqual(validate_learning_content(legacy), ([], []))

    def test_registered_local_figures_diagrams_and_asset_alias_are_valid(self):
        for kind in ("figure", "diagram"):
            for asset_alias in (False, True):
                with self.subTest(kind=kind, asset_alias=asset_alias):
                    block = figure(kind)
                    if asset_alias:
                        block["asset"] = block.pop("src")
                    data = paper(block)
                    before = copy.deepcopy(data)
                    self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))
                    self.assertEqual(data, before)

    def test_inline_and_catalog_graphs_require_default_visible_body_placement(self):
        for catalog in (False, True):
            with self.subTest(catalog=catalog):
                block = body_graph(catalog)
                data = paper(block)
                if catalog:
                    data["learning_catalog"]["aids"].append(graph())
                self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))
                del block["placement"]
                self.assert_error(data, REQUIREMENT_PATH + ".block_id: framework learning_aid requires placement body")

    def test_registration_shape_and_ids_fail_cleanly(self):
        cases = (None, False, [], "method", {}, {"section_id":"overview-method"},
            {"block_id":"overview-method-framework"}, {"section_id":[],"block_id":"figure"},
            {"section_id":"overview-method","block_id":False},
            {"section_id":"bad section","block_id":"overview-method-framework"})
        for value in cases:
            with self.subTest(value=value):
                data = paper()
                data["learning_requirements"]["overview_framework"] = value
                self.assert_error(data, REQUIREMENT_PATH)

    def test_registered_section_is_an_overview_section_not_another_view(self):
        for view in ("explanation", "translation"):
            with self.subTest(view=view):
                data = paper()
                section = data["overview"]["sections"].pop()
                target = data[view]["sections"] if view != "translation" else data["sections"]
                target.append(section)
                self.assert_error(data, REQUIREMENT_PATH + ".section_id")
        data = paper()
        data["learning_requirements"]["overview_framework"]["section_id"] = "missing"
        self.assert_error(data, REQUIREMENT_PATH + ".section_id")

    def test_missing_wrong_section_or_ambiguous_registered_block_is_rejected(self):
        data = paper()
        section = data["overview"]["sections"][-1]
        block = section["blocks"].pop()
        self.assert_error(data, REQUIREMENT_PATH + ".block_id")
        data["overview"]["sections"][0]["blocks"].append(block)
        self.assert_error(data, REQUIREMENT_PATH + ".block_id")
        section["blocks"] = [block, copy.deepcopy(block)]
        self.assert_error(data, REQUIREMENT_PATH + ".block_id")
        data = paper()
        data["overview"]["sections"].append(copy.deepcopy(data["overview"]["sections"][-1]))
        self.assert_error(data, REQUIREMENT_PATH + ".section_id")

    def test_nested_learning_details_quote_and_component_fallback_do_not_count(self):
        for wrapper in ("learning", "details", "open-details", "quote", "fallback"):
            with self.subTest(wrapper=wrapper):
                data = paper()
                section = data["overview"]["sections"][-1]
                block = section["blocks"].pop()
                if wrapper == "learning":
                    data["overview"]["sections"][0]["blocks"][0]["learning"]["blocks"] = [block]
                elif wrapper == "fallback":
                    aid = graph()
                    aid.pop("fallback_text")
                    aid["fallback"] = {"version":1,"blocks":[block]}
                    section["blocks"] = [dict(body_graph(), id="graph-container", aid=aid)]
                else:
                    container = {"id":"container", "type":"quote" if wrapper == "quote" else "details",
                        "evidence_kind":"ai_explanation", "summary":"展开", "blocks":[block]}
                    if wrapper == "open-details":
                        container["open"] = True
                    section["blocks"] = [container]
                self.assert_error(data, REQUIREMENT_PATH + ".block_id")

    def test_non_visual_blocks_and_non_graph_interactions_do_not_count(self):
        for kind in ("paragraph", "formula", "table", "algorithm", "code", "heading", "details"):
            with self.subTest(kind=kind):
                block = {"id":"overview-method-framework", "type":kind,
                    "evidence_kind":"ai_explanation", "text_zh":"说明", "latex":"x=1",
                    "rows":[["输入","输出"]], "code":"process", "summary":"展开", "blocks":[figure()]}
                self.assert_error(paper(block), REQUIREMENT_PATH + ".block_id: framework must be")
        data = json.loads((TEMPLATES / "complete-cache-example.json").read_text(encoding="utf-8"))
        for aid in data["learning_catalog"]["aids"]:
            with self.subTest(aid=aid["type"]):
                wrong = copy.deepcopy(data)
                wrong["learning_requirements"]["overview_framework"] = {
                    "section_id":"overview-method", "block_id":"overview-method-framework"}
                wrong["overview"]["sections"].append({"id":"overview-method", "blocks":[
                    dict(body_graph(catalog=True), aid_id=aid["id"])]})
                self.assert_error(wrong, REQUIREMENT_PATH + ".block_id: framework learning_aid must resolve to graph_walkthrough")

    def test_registered_assets_still_require_safe_existing_local_files(self):
        for src, expected in (("", "local asset path"), ("../outside.svg", "confined"),
                ("https://example.test/flow.svg", "confined"), ("assets/missing.svg", "does not exist")):
            with self.subTest(src=src):
                block = figure()
                block["src"] = src
                self.assert_error(paper(block), expected)
        data = paper()
        data["overview"]["sections"][-1]["blocks"][0]["svg"] = "<svg/>"
        self.assert_error(data, ".svg: use a registered local static asset")
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = Path(tmp) / "unsafe.svg"
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>', encoding="utf-8")
            block = figure()
            block["src"] = "unsafe.svg"
            self.assert_error(paper(block), "static safe primitives", Path(tmp))

    def test_registered_graphs_keep_normal_config_reference_and_safety_checks(self):
        mutations = ((lambda b:b["aid"]["config"]["edges"][0].update(to="missing"), "unknown node"),
            (lambda b:b["aid"].pop("fallback_text"), "static rich fallback"),
            (lambda b:b["aid"]["config"].update(script="never execute"), "executable/HTML"),
            (lambda b:b.update(aid_id="missing"), "exactly one aid or aid_id"))
        for mutate, message in mutations:
            with self.subTest(message=message):
                block = body_graph()
                mutate(block)
                self.assert_error(paper(block), message)
        data = paper(body_graph(catalog=True))
        self.assert_error(data, "unknown catalog component")
        self.assert_error(data, REQUIREMENT_PATH + ".block_id: framework learning_aid must resolve")

    def test_explicit_registration_is_checked_even_on_legacy_contract_path(self):
        data = paper()
        del data["content_contract_version"]
        self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))
        data["learning_requirements"]["overview_framework"]["block_id"] = "missing"
        self.assert_error(data, REQUIREMENT_PATH + ".block_id")

    def test_all_presets_register_an_empty_incomplete_interactive_pipeline_and_review_tasks(self):
        self.assertEqual(set(PRESETS), {"method", "theory", "survey", "system"})
        for preset in PRESETS:
            with self.subTest(preset=preset):
                data, todo = build_scaffold("framework-test", preset)
                requirement = data["learning_requirements"]["overview_framework"]
                section = next(s for s in data["overview"]["sections"] if s["id"] == requirement["section_id"])
                block = next(b for b in section["blocks"] if b["id"] == requirement["block_id"])
                self.assertEqual(requirement, {"section_id":"overview-method", "block_id":"overview-method-framework", "kind":"interactive_pipeline"})
                self.assertEqual(block["type"], "learning_aid")
                self.assertEqual(block["placement"], "body")
                self.assertEqual(block["aid"]["type"], "method_pipeline")
                self.assertEqual(block["aid"]["config"], {"summary":"", "nodes":[], "edges":[]})
                self.assertEqual(block["aid"]["fallback_text"], "")
                self.assertNotIn("learning", block)
                self.assertEqual((data["content_status"], todo["status"]), ("incomplete", "incomplete"))
                self.assertTrue(all(item["complete"] is False for item in todo["items"]))
                errors, _ = validate_learning_content(data)
                self.assertTrue(any("unfinished scaffold" in e for e in errors), errors)
                self.assertTrue(any("requires 2..40 stages" in e for e in errors), errors)
                self.assertEqual(data["content_contract_version"], "3.2")
                self.assertEqual(todo["workflow"], "references/reader_workflow.md")
                # Todo targets must still resolve after instruction deduplication;
                # authoring quality is evaluated with source-based forward use.
                block_ids = {b["id"] for view in ("overview", "explanation")
                             for section in data[view]["sections"] for b in section["blocks"]}
                for item in todo["items"]:
                    self.assertTrue(item["task"].strip())
                    if "block_id" in item:
                        self.assertIn(item["block_id"], block_ids)

    def test_schema_registration_is_optional_and_has_required_stable_reference_ids(self):
        schema = json.loads((ROOT / "schemas" / "paper-data-3.1.schema.json").read_text(encoding="utf-8"))
        requirements = schema["properties"]["learning_requirements"]
        self.assertNotIn("overview_framework", requirements.get("required", []))
        framework = requirements["properties"]["overview_framework"]
        self.assertEqual(set(framework["required"]), {"section_id", "block_id"})
        self.assertIn("human review", framework["description"])
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError:
            return
        schemas = {p.name:json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "schemas").glob("*.json")}
        registry = Registry().with_resources((name, Resource.from_contents(value)) for name, value in schemas.items())
        validator = Draft202012Validator(schema, registry=registry)
        self.assertEqual(list(validator.iter_errors(paper())), [])
        for value in (None, {}, {"section_id":"bad id", "block_id":"figure"}):
            data = paper()
            data["learning_requirements"]["overview_framework"] = value
            self.assertTrue(list(validator.iter_errors(data)))
        data = paper()
        del data["learning_requirements"]["overview_framework"]
        self.assertEqual(list(validator.iter_errors(data)), [])


if __name__ == "__main__":
    unittest.main()
