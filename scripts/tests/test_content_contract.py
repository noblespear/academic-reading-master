"""Behavioral authoring/rich contract regressions; no model calls/dependencies."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
TEMPLATES = SKILL / "assets" / "content-templates"
sys.path.insert(0, str(SCRIPTS))
from content_contract import validate_learning_content, validate_rich_document, rich_to_text
from init_reading_content import build_scaffold, PRESETS


def full_example():
    return json.loads((TEMPLATES / "complete-cache-example.json").read_text(encoding="utf-8"))


def walk(document, path):
    target = document
    for part in path[:-1]:
        target = target[part]
    return target, path[-1]


def text_document(*blocks):
    return {"version":1,"blocks":list(blocks)}


class LearningContractTests(unittest.TestCase):
    def assert_error(self, data, field):
        errors, _ = validate_learning_content(data, TEMPLATES)
        self.assertTrue(any(field in error for error in errors), errors)

    def test_complete_and_minimal_examples_are_valid(self):
        for name in ("complete-cache-example.json", "minimal-valid-example.json"):
            with self.subTest(name=name):
                data = json.loads((TEMPLATES / name).read_text(encoding="utf-8"))
                self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))

    def test_legacy_does_not_require_new_review_or_learning(self):
        legacy = {"schema_version":3,"id":"old", "overview":{"sections":[]},"explanation":{"sections":[]},"sections":[]}
        self.assertEqual(validate_learning_content(legacy), ([], []))
        self.assertNotIn("content_review", legacy)

    def test_error_fixtures_reject_the_observable_defect(self):
        cases = json.loads((TEMPLATES / "invalid-cases.json").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(name=case["name"]):
                data = full_example()
                target, field = walk(data, case.get("patch_path", case.get("delete_path")))
                if "delete_path" in case:
                    del target[field]
                else:
                    target[field] = case["value"]
                self.assert_error(data, case["expected_path"])

    def test_unknown_prerequisite_rejected_without_forcing_concept_glossary(self):
        data = full_example()
        data["learning_catalog"]["concepts"][0]["prerequisite_ids"] = ["unknown"]
        self.assert_error(data, "unknown reference")
        data = full_example()
        for section in data["overview"]["sections"] + data["explanation"]["sections"]:
            for block in section["blocks"]:
                if block.get("learning"):
                    block["learning"]["concept_ids"] = []
        data["learning_requirements"]["required_concept_ids"] = [c["id"] for c in data["learning_catalog"]["concepts"]]
        self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))
        data["learning_requirements"]["required_concept_ids"].append("missing")
        self.assert_error(data, "unknown reference")

    def test_duplicate_ids_and_unresolved_formula_or_source(self):
        data = full_example()
        data["learning_catalog"]["concepts"][1]["id"] = "data"
        self.assert_error(data, "duplicate ID")
        data = full_example()
        formula = data["explanation"]["sections"][1]["blocks"][0]["learning"]["formula_explanations"][0]
        formula["formula_ref"] = "not-a-formula"
        self.assert_error(data, ".formula_ref")
        data = full_example()
        data["overview"]["sections"][0]["blocks"][0]["source_refs"][0]["block_id"] = "absent-source"
        self.assert_error(data, "unknown block")

    def test_key_formula_must_have_complete_explanation(self):
        data = full_example()
        data["explanation"]["sections"][1]["blocks"][0]["learning"]["formula_explanations"] = []
        self.assert_error(data, "key_formula")

    def test_formula_symbols_reject_actual_python_escape_pollution(self):
        # Actual decoded controls, rather than literal LaTeX commands.
        for symbol in ("\nabla", "\beta", "\alpha", "\approx", "\bigcup", "\theta", "x\ry"):
            with self.subTest(symbol=repr(symbol)):
                data = full_example()
                explanation = data["explanation"]["sections"][1]["blocks"][0]["learning"]["formula_explanations"][0]
                explanation["symbols"][0]["symbol"] = symbol
                self.assert_error(data, ".symbols.0.symbol: invalid LaTeX control character")

    def test_formula_symbols_use_latex_structure_and_safety_checks(self):
        for symbol, message in ((r"x_{i", "unbalanced LaTeX braces"),
                (r"\left(x", "unbalanced LaTeX delimiters"),
                (r"\input{secret}", "unsafe LaTeX command")):
            with self.subTest(symbol=symbol):
                data = full_example()
                explanation = data["explanation"]["sections"][1]["blocks"][0]["learning"]["formula_explanations"][0]
                explanation["symbols"][0]["symbol"] = symbol
                self.assert_error(data, ".symbols.0.symbol: " + message)

    def test_literal_latex_symbols_remain_valid(self):
        data = full_example()
        explanation = data["explanation"]["sections"][1]["blocks"][0]["learning"]["formula_explanations"][0]
        explanation["symbols"][0]["symbol"] = r"\nabla, \beta, \alpha, \approx, \bigcup_t, \theta, A_{i,j}"
        self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))

    def test_learning_latex_fields_share_control_character_validation(self):
        paths = [
            ["explanation", "sections", 1, "blocks", 0, "learning",
                "formula_explanations", 0, "steps", 0, "latex"],
            ["explanation", "sections", 1, "blocks", 0, "learning",
                "formula_explanations", 0, "example", "steps", 0, "latex"],
            ["learning_catalog", "aids", 3, "config", "latex"],
        ]
        for path in paths:
            with self.subTest(path=path):
                data = full_example()
                target, key = walk(data, path)
                target[key] = "x=\beta"
                self.assert_error(data, ".latex: invalid LaTeX control character")
                target[key] = "x=1\n\t+2"
                self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))

    def test_historical_scores_are_optional_and_have_no_total_threshold(self):
        data = full_example()
        del data["content_review"]["plain_language_scores"]
        self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))
        for scores in ([0] * 10, [1] * 10, [0, 1, 2, 0, 1, 2, 0, 1, 2, 0]):
            with self.subTest(scores=scores):
                data["content_review"]["plain_language_scores"] = scores
                self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))

    def test_historical_scores_reject_malformed_values_when_provided(self):
        for scores in (None, "012", [], [1] * 9, [1] * 11, [None] * 10,
                [True] * 10, [1.0] * 10, ["1"] * 10, [-1] * 10, [3] * 10):
            with self.subTest(scores=scores):
                data = full_example()
                data["content_review"]["plain_language_scores"] = scores
                self.assert_error(data, "plain_language_scores")

    def test_review_reviewer_ready_and_each_factual_gate_remain_required(self):
        data = full_example()
        del data["content_review"]["plain_language_scores"]
        missing_review = copy.deepcopy(data)
        del missing_review["content_review"]
        self.assert_error(missing_review, "content_review")
        incomplete = copy.deepcopy(data)
        incomplete["content_status"] = "incomplete"
        self.assert_error(incomplete, "content_status")
        for reviewer in (None, "", "   ", 1):
            with self.subTest(reviewer=reviewer):
                wrong = copy.deepcopy(data)
                wrong["content_review"]["reviewer"] = reviewer
                self.assert_error(wrong, "reviewer")
        missing_reviewer = copy.deepcopy(data)
        del missing_reviewer["content_review"]["reviewer"]
        self.assert_error(missing_reviewer, "reviewer")
        for gate in data["content_review"]["factual_gates"]:
            for value in (False, None, 1, "true"):
                with self.subTest(gate=gate, value=value):
                    wrong = copy.deepcopy(data)
                    wrong["content_review"]["factual_gates"][gate] = value
                    self.assert_error(wrong, "factual_gates")
            missing_gate = copy.deepcopy(data)
            del missing_gate["content_review"]["factual_gates"][gate]
            self.assert_error(missing_gate, "factual_gates")

    def test_long_summary_sentence_alone_does_not_warn(self):
        data = full_example()
        summary = "由于每次请求到来时缓存都必须在固定容量内保存最近使用的数据，系统先检查所请求的数据是否已经存在，再依据检查结果更新访问顺序或者读取数据并替换最久没有使用的条目，从而让后续步骤能够使用更新后的缓存状态。"
        self.assertGreater(len(summary), 60)
        for view in ("overview", "explanation"):
            data[view]["sections"][0]["blocks"][0]["learning"]["plain_summary"] = summary
        self.assertEqual(validate_learning_content(data, TEMPLATES), ([], []))

    def test_alias_formula_and_ratio_are_compatible(self):
        data = full_example()
        formula = data["explanation"]["sections"][1]["blocks"][0]["learning"]["formula_explanations"][0]
        formula["source_formula_id"] = formula.pop("formula_ref")
        config = data["learning_catalog"]["aids"][3]["config"]
        del config["expression"]
        config.update(model="ratio", numerator="H", denominator=7)
        self.assertEqual(validate_learning_content(data), ([], []))

    def test_prediction_needs_feedback_for_every_choice(self):
        data = full_example()
        del data["learning_catalog"]["aids"][5]["config"]["choices"][1]["feedback"]
        self.assert_error(data, "choices.1.feedback")

    def test_arithmetic_rejects_unknown_reference_invalid_default_and_limits(self):
        for expression, field in [
            ({"op":"divide","args":[1,0]}, "undefined result"),
            ({"op":"power","args":[-1,0.5]}, "finite"),
            ({"op":"power","args":[2,2048]}, "finite"),
            ({"op":"add","args":[{"parameter":"unknown"},1]}, "unknown parameter"),
            ({"op":"subtract","args":[1]}, "exactly two"),
            ({"op":"sum","args":[1] * 33}, "1..32"),
            ({"op":"sum","args":[{"op":"sum","args":[1] * 32}] * 5}, "depth/node"),
        ]:
            with self.subTest(expression=expression):
                data = full_example()
                data["learning_catalog"]["aids"][3]["config"]["expression"] = expression
                self.assert_error(data, field)
        expression = 1
        for _ in range(14):
            expression = {"op":"sum","args":[expression]}
        data = full_example()
        data["learning_catalog"]["aids"][3]["config"]["expression"] = expression
        self.assert_error(data, "depth/node")

    def test_malformed_values_return_errors_instead_of_crashing(self):
        mutations = [(["learning_catalog","concepts",0,"id"], []),
            (["learning_catalog","aids",0,"type"], []),
            (["learning_catalog","aids",3,"config","expression"], {"parameter":[]}),
            (["learning_catalog","aids",3,"config","parameters",0,"id"], []),
            (["learning_catalog","aids",5,"config","correct_choice_id"], []),
            (["explanation","sections",1,"blocks",0,"learning","formula_explanations",0,"formula_ref"], []),
            (["overview","sections",0,"blocks",0,"evidence_kind"], []),
            (["overview","sections",0,"blocks",0,"source_refs",0,"block_id"], [])]
        for path, value in mutations:
            with self.subTest(path=path):
                data = full_example()
                target, key = walk(data, path)
                target[key] = value
                self.assertTrue(validate_learning_content(data)[0])

    def test_fixture_values_are_independently_recomputed(self):
        data = full_example()
        config = data["learning_catalog"]["aids"][1]["config"]
        counts, traces = [], []
        for capacity in range(1,5):
            cache, hits, rows = [], 0, []
            for request in config["sequence"]:
                hit, evicted = request in cache, "无"
                if hit:
                    hits += 1
                    cache.remove(request)
                elif len(cache) == capacity:
                    evicted = cache.pop(0)
                cache.append(request)
                rows.append([request,"是" if hit else "否"," ".join(cache),evicted])
            counts.append(hits)
            traces.append(rows)
        self.assertEqual(counts, [0,1,2,3])
        self.assertEqual(traces[2], data["learning_catalog"]["aids"][1]["fallback"]["blocks"][0]["rows"])
        self.assertAlmostEqual(2 / len(config["sequence"]), 0.2857142857142857)


class RichContractTests(unittest.TestCase):
    def test_latex_rejects_non_whitespace_c0_controls(self):
        for code in range(32):
            if chr(code) in "\t\n\r":
                continue
            with self.subTest(code=code):
                document = text_document({"id":"f", "type":"formula", "latex":"x=" + chr(code) + "1"})
                errors, _ = validate_rich_document(document)
                self.assertTrue(any(".latex: invalid LaTeX control character" in error
                    and f"U+{code:04X}" in error for error in errors), errors)

    def test_latex_allows_multiline_formula_formatting(self):
        latex = "\\begin{aligned}\r\n\tx &= 1 \\\\\n\ty &= 2\n\\end{aligned}"
        document = text_document({"id":"f", "type":"formula", "latex":latex})
        self.assertEqual(validate_rich_document(document), ([], []))

    def test_all_shared_blocks_and_teaching_fallback_survive_text_export(self):
        aid = full_example()["learning_catalog"]["aids"][2]
        document = text_document(
            {"id":"p","type":"paragraph","text_zh":"直接回答。","source_refs":[{"label":"示例","page":1}]},
            {"id":"heading","type":"heading","text":"推理"},
            {"id":"formula","type":"formula","latex":r"h=\frac{2}{7}"},
            {"id":"list","type":"list","items":["第一项",{"text_zh":"第二项"}]},
            {"id":"table","type":"table","headers":["请求","结果"],"rows":[["A","命中"]]},
            {"id":"code","type":"code","code":"print('teaching only')"},
            {"id":"algo","type":"algorithm","algo_title":"步骤","algo_lines":["先数次数","再相除"]},
            {"id":"quote","type":"quote","blocks":[{"id":"quote-p","type":"paragraph","text":"原句。"}]},
            {"id":"details","type":"details","summary":"长解释","blocks":[{"id":"inside","type":"paragraph","text":"完整解释。"}]},
            {"id":"separator","type":"separator"},
            {"id":"aid","type":"learning_aid","aid":aid})
        self.assertEqual(validate_rich_document(document), ([], []))
        before = copy.deepcopy(document)
        plain = rich_to_text(document)
        self.assertIn(r"\[h=\frac{2}{7}\]", plain)
        self.assertIn("请求 | 结果", plain)
        self.assertIn("A | 命中", plain)
        self.assertIn("> 原句。", plain)
        self.assertIn("完整解释。", plain)
        self.assertIn("[教学示例]", plain)
        self.assertIn("第5步读取D时移出B", plain)
        self.assertIn("[来源: 示例 p.1]", plain)
        self.assertEqual(document, before)
        self.assertEqual(plain, rich_to_text(document))

    def test_external_source_urls_survive_text_export(self):
        document = text_document({"id":"p", "type":"paragraph", "text":"背景", "source_refs":[
            {"url":"https://example.test/book"},
            {"label":"教材", "url":"https://example.test/book#section-2", "page":7},
            {"label":"无效来源", "url":"javascript:alert(1)", "page":9},
            {"label":"本论文", "page":2}]})
        plain = rich_to_text(document)
        self.assertIn("https://example.test/book; 教材 p.7 https://example.test/book#section-2; 本论文 p.2", plain)
        self.assertNotIn("无效来源", plain)
        self.assertNotIn("javascript:", plain)
        self.assertEqual(document["blocks"][0]["source_refs"][1]["page"], 7)

    def test_nested_duplicate_id_and_depth_are_rejected(self):
        document = text_document({"id":"same","type":"details","summary":"展开","blocks":[{"id":"same","type":"paragraph","text":"重复"}]})
        self.assertTrue(any("duplicate ID" in x for x in validate_rich_document(document)[0]))
        block = {"id":"bottom","type":"paragraph","text":"正文"}
        for i in range(14):
            block = {"id":"details" + str(i),"type":"details","summary":"展开","blocks":[block]}
        self.assertTrue(any("nesting exceeds" in x for x in validate_rich_document(text_document(block))[0]))

    def test_executable_html_and_custom_component_are_rejected(self):
        cases = [
            {"id":"html","type":"paragraph","text":"<img src=x onerror=alert(1)>"},
            {"id":"js","type":"paragraph","text":"safe", "onclick":"alert(1)"},
            {"id":"svg","type":"diagram","src":"assets/a.svg","svg":"<svg></svg>"},
            {"id":"aid","type":"learning_aid","aid":{"id":"custom","type":"custom-js","version":1,"evidence_kind":"teaching_example","config":{},"fallback_text":"safe"}},
            {"id":"bad-table","type":"table","table_html":"<table></table>"}]
        for block in cases:
            with self.subTest(type=block["type"]):
                self.assertTrue(validate_rich_document(text_document(block))[0])

    def test_asset_paths_and_actual_safe_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            safe = root / "assets" / "safe.svg"
            safe.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="2" height="2"/></svg>', encoding="utf-8")
            document = text_document({"id":"figure","type":"figure","src":"assets/safe.svg","caption":"安全图"})
            self.assertEqual(validate_rich_document(document, root), ([], []))
            self.assertIn("assets/safe.svg", rich_to_text(document))
            for path in ("../outside.png", "..\\outside.png", "C:\\outside.png", "https://example.org/a.png", "data:image/png;base64,x", "assets/%2e%2e/a.png", ".secret.png", "assets/code.js", "assets/missing.png"):
                document["blocks"][0]["src"] = path
                self.assertTrue(validate_rich_document(document, root)[0], path)
            document["blocks"][0]["src"] = "assets/safe.svg"
            safe.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
            self.assertTrue(validate_rich_document(document, root)[0])
            safe.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)"/></svg>', encoding="utf-8")
            self.assertTrue(validate_rich_document(document, root)[0])

    def test_catalog_reference_requires_known_registered_aid(self):
        doc = text_document({"id":"ref","type":"learning_aid","aid_id":"aid-lru"})
        self.assertTrue(validate_rich_document(doc)[0])
        self.assertEqual(validate_rich_document(doc, catalog=full_example()["learning_catalog"]), ([], []))
        doc["blocks"][0]["aid_id"] = "missing"
        self.assertTrue(validate_rich_document(doc, catalog=full_example()["learning_catalog"])[0])

    def test_nan_non_json_and_bad_types_are_errors(self):
        for block in ({"id":"p","type":[],"text":"bad"}, {"id":"p","type":"paragraph","text":"safe","value":float("nan")}, {"id":"p","type":"paragraph","text":"safe","value":object()}):
            self.assertTrue(validate_rich_document(text_document(block))[0])


class ScaffoldTests(unittest.TestCase):
    def test_all_presets_require_source_outline_and_are_honestly_incomplete(self):
        for preset in PRESETS:
            data, todo = build_scaffold("test_paper", preset, "Test", "教学")
            data = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
            self.assertEqual(data["schema_version"], 3)
            self.assertEqual(data["content_status"], "incomplete")
            self.assertNotIn("plain_language_scores", data["content_review"])
            self.assertEqual(data["content_review"]["reviewer"], "")
            self.assertTrue(all(value is False for value in data["content_review"]["factual_gates"].values()))
            self.assertTrue(data["content_review"]["note"])
            self.assertTrue((SKILL / todo["workflow"]).is_file())
            self.assertTrue(data["overview"]["sections"])
            self.assertTrue(data["explanation"]["sections"])
            self.assertEqual(data["sections"], [])
            self.assertEqual(data["translation"], {"status":"deferred"})
            self.assertTrue(all(not item["complete"] for item in todo["items"]))
            self.assertTrue(any("unfinished scaffold" in e for e in validate_learning_content(data)[0]))
            self.assertTrue(all("learning" not in block for view in ("overview", "explanation")
                for section in data[view]["sections"] for block in section["blocks"]))
            self.assertEqual(len(data["overview"]["sections"]), 5)
            self.assertIn("原论文", data["explanation"]["sections"][0]["title_cn"])
            self.assertNotIn("default_annotations", data)
            self.assertNotIn("required_concept_ids", data["learning_requirements"])
            tasks = " ".join(item["task"] for item in todo["items"])
            self.assertNotIn("default_annotations", tasks)
            self.assertNotIn("术语批注", tasks)

    def test_cli_writes_strict_assignment_todo_and_protects_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "paper-data.js"
            args = [sys.executable,str(SCRIPTS / "init_reading_content.py"),"--id","sample","--preset","theory","--output",str(output)]
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            original = output.read_text(encoding="utf-8")
            data = json.loads(original.removeprefix("window.PAPER_DATA = ").strip().removesuffix(";"))
            self.assertEqual(data["content_status"], "incomplete")
            self.assertTrue(output.with_name("paper-data-todo.json").is_file())
            repeated = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(original, output.read_text(encoding="utf-8"))

    def test_schema_preserves_optional_historical_score_shape(self):
        schema = json.loads((SKILL / "schemas" / "paper-data-3.1.schema.json").read_text(encoding="utf-8"))
        review = schema["properties"]["content_review"]
        self.assertEqual(set(review["required"]), {"reviewer", "factual_gates"})
        scores = review["properties"]["plain_language_scores"]
        self.assertIn("historical", scores["description"])
        self.assertEqual((scores["minItems"], scores["maxItems"]), (10, 10))
        self.assertEqual(scores["items"], {"type":"integer", "minimum":0, "maximum":2})

    def test_schema_references_resolve_locally(self):
        schemas = {p.name:json.loads(p.read_text(encoding="utf-8")) for p in (SKILL / "schemas").glob("*.json")}
        def inspect(value, current):
            if isinstance(value, dict):
                if "$ref" in value:
                    ref = value["$ref"]
                    filename, _, pointer = ref.partition("#")
                    target = schemas[filename or current]
                    for piece in pointer.lstrip("/").split("/") if pointer else []:
                        target = target[piece.replace("~1","/").replace("~0","~")]
                    self.assertIsInstance(target, dict)
                for item in value.values():
                    inspect(item, current)
            elif isinstance(value, list):
                for item in value:
                    inspect(item, current)
        for filename, schema in schemas.items():
            inspect(schema, filename)


if __name__ == "__main__":
    unittest.main()
