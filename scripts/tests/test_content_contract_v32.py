"""Body-first contract and isolated draft preview regressions; no model calls."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from content_contract import FACTUAL_GATES, is_translation_deferred, validate_learning_content
from deploy_reader import deploy_preview, load_paper_data, validate_paper_data
from init_reading_content import build_scaffold, PRESETS


def example():
    return json.loads((ROOT / "assets/content-templates/body-first-valid-example.json").read_text(encoding="utf-8"))


def with_formula(data=None):
    data = example() if data is None else data
    data["explanation"]["sections"][0]["blocks"].extend([
        {"id":"hit-rate", "type":"formula", "latex":r"h=\frac{H}{N}", "key_formula":True,
         "evidence_kind":"teaching_example", "source_refs":[]},
        {"id":"hit-rate-explanation", "type":"paragraph", "evidence_kind":"teaching_example", "source_refs":[],
         "text_zh":"命中率 h 是命中次数 H 与请求总数 N 的比值。除法把次数转换为每次请求的平均命中比例；两次命中除以七次请求约为 0.286。"}])
    data["learning_requirements"]["required_formula_ids"] = ["hit-rate"]
    return data


def deferred_example():
    data = example()
    data["translation"] = {"status":"deferred"}
    data["sections"] = []
    for view in ("overview", "explanation"):
        for section in data[view]["sections"]:
            section["source_refs"] = [{"page":1, "label":"Chapter source"}]
            for block in section["blocks"]:
                block.pop("source_refs", None)
    return data


class Contract32Tests(unittest.TestCase):
    def test_explicit_deferred_allows_completed_body_with_chapter_sources(self):
        data = deferred_example()
        before = copy.deepcopy(data)
        self.assertTrue(is_translation_deferred(data))
        self.assertEqual(validate_learning_content(data), ([], []))
        self.assertEqual(data, before)
        data["overview"]["sections"][0]["source_refs"] = [{"page":0}]
        self.assertTrue(any("overview.sections.0.source_refs" in error for error in validate_learning_content(data)[0]))

    def test_translation_marker_cannot_hide_empty_or_mislabeled_translation(self):
        for state in (None, {"status":"ready"}, {"status":"generating"}, []):
            data = deferred_example()
            if state is None:
                data.pop("translation")
            else:
                data["translation"] = state
            self.assertTrue(validate_learning_content(data)[0], state)
        data = deferred_example()
        data["translation"]["status"] = "ready"
        self.assertTrue(validate_learning_content(data, preview=True)[0])
        data["sections"] = [{"id":"translation", "blocks":[]}]
        self.assertTrue(validate_learning_content(data, preview=True)[0])
        data = example()
        data["translation"] = {"status":"deferred"}
        self.assertTrue(any("deferred translation" in error for error in validate_learning_content(data)[0]))

    def test_ready_translation_requires_both_languages(self):
        data = example()
        data["translation"] = {"status":"ready", "review":{"scope":"new translation"}}
        self.assertEqual(validate_learning_content(data), ([], []))
        for field in ("text_en", "text_zh"):
            wrong = copy.deepcopy(data)
            wrong["sections"][0]["blocks"][0][field] = ""
            self.assertTrue(any(field in error for error in validate_learning_content(wrong)[0]))
        data["sections"][0]["blocks"] = [{"id":"translated-details", "type":"details", "summary":"Additional source",
            "evidence_kind":"translation", "blocks":[{"id":"translated-nested", "type":"paragraph",
                "evidence_kind":"translation", "text_zh":"这一段没有英文。"}]}]
        self.assertTrue(any("text_en" in error for error in validate_learning_content(data)[0]))

    def test_unmarked_legacy_empty_translation_keeps_original_validator_path(self):
        for version in (None, "3.1"):
            data = deferred_example()
            data.pop("translation")
            if version is None:
                data.pop("content_contract_version")
            else:
                data["content_contract_version"] = version
                data["learning_catalog"] = {"concepts":[], "aids":[]}
            self.assertFalse(is_translation_deferred(data))
            self.assertEqual(validate_learning_content(data), ([], []))

    def test_body_formula_and_prose_without_hidden_learning_or_catalog(self):
        data = with_formula()
        original = copy.deepcopy(data)
        self.assertNotIn("learning_catalog", data)
        self.assertEqual(validate_learning_content(data), ([], []))
        self.assertEqual(data, original)

    def test_legacy_31_keeps_its_old_formula_explanation_rule(self):
        data = with_formula()
        data["content_contract_version"] = "3.1"
        data["learning_catalog"] = {"concepts":[], "aids":[]}
        errors, _ = validate_learning_content(data)
        self.assertTrue(any("key_formula requires" in error for error in errors))
        data.pop("content_contract_version")
        data.pop("content_review")
        data.pop("content_status")
        self.assertEqual(validate_learning_content(data), ([], []))

    def test_formula_and_optional_catalog_references_still_must_resolve(self):
        data = with_formula()
        data["learning_requirements"]["required_formula_ids"] = ["missing"]
        self.assertTrue(any("unknown reference" in error for error in validate_learning_content(data)[0]))
        data = with_formula()
        data["learning_catalog"] = {"concepts":[{"id":"capacity", "label":"容量",
            "context_meaning":"缓存位置数量", "plain_definition":"三个位置最多放三项。"}],
            "aids":[{"id":"body-cards", "type":"concept_cards", "version":1,
                "evidence_kind":"teaching_example", "config":{"concept_ids":["capacity"]},
                "fallback_text":"三个缓存位置最多同时放三项。"}]}
        data["overview"]["sections"][0]["blocks"].append({"id":"body-aid", "type":"learning_aid",
            "placement":"body", "evidence_kind":"teaching_example", "aid_id":"body-cards"})
        self.assertEqual(validate_learning_content(data), ([], []))
        data["overview"]["sections"][0]["blocks"][-1]["aid_id"] = "missing"
        self.assertTrue(any("unknown catalog component" in error for error in validate_learning_content(data)[0]))

    def test_32_rejects_learning_including_null_and_nested_layers_but_old_contracts_keep_it(self):
        for value in (None, {}, {"id":"case", "plain_summary":"A case."}):
            data = example()
            data["overview"]["sections"][0]["blocks"][0]["learning"] = value
            for preview in (False, True):
                errors, _ = validate_learning_content(data, preview=preview)
                self.assertTrue(any(".learning" in error and "正文" in error for error in errors))
        data = example()
        data["explanation"]["sections"][0]["blocks"].append({"id":"case", "type":"details",
            "evidence_kind":"teaching_example", "summary":"Case", "blocks":[{
                "id":"case-p", "type":"paragraph", "evidence_kind":"teaching_example",
                "text":"Three positions hold three entries.", "learning":{"id":"old", "plain_summary":"A case."}}]})
        self.assertTrue(any(".learning" in error for error in validate_learning_content(data)[0]))
        old = json.loads((ROOT / "assets/content-templates/minimal-valid-example.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_learning_content(old), ([], []))
        old.pop("content_contract_version")
        self.assertEqual(validate_learning_content(old), ([], []))

    def test_nested_paper_ids_sources_and_evidence_share_paper_scope(self):
        data = example()
        nested = {"id":"nested-formula", "type":"formula", "latex":"x=1", "evidence_kind":"teaching_example",
            "source_refs":[{"content_id":"src-capacity"}]}
        data["explanation"]["sections"][0]["blocks"].append({"id":"case", "type":"details", "summary":"Case",
            "evidence_kind":"teaching_example", "blocks":[nested]})
        data["learning_requirements"]["required_formula_ids"] = ["nested-formula"]
        self.assertEqual(validate_learning_content(data), ([], []))
        for field in ("block_id", "content_id"):
            nested["source_refs"] = [{field:"invented-block"}]
            self.assertTrue(any("unknown block invented-block" in error for error in validate_learning_content(data)[0]))
        nested["source_refs"] = []
        nested.pop("evidence_kind")
        self.assertTrue(any("nested" not in error and ".blocks.0.evidence_kind" in error for error in validate_learning_content(data)[0]))
        nested["evidence_kind"] = "teaching_example"
        data["overview"]["sections"][0]["blocks"].append({"id":"quote-case", "type":"quote",
            "evidence_kind":"teaching_example", "blocks":[copy.deepcopy(nested)]})
        self.assertTrue(any("duplicate ID nested-formula" in error for error in validate_learning_content(data)[0]))
        data["content_contract_version"] = "3.1"
        data["learning_catalog"] = {"concepts":[], "aids":[]}
        data["learning_requirements"]["required_formula_ids"] = []
        # New paper-scope checks apply only to 3.2, retaining 3.1 behavior.
        self.assertEqual(validate_learning_content(data), ([], []))

    def test_component_fallback_ids_keep_local_scope_and_need_no_body_provenance(self):
        data = example()
        for index in range(2):
            data["overview"]["sections"][0]["blocks"].append({"id":"inline-aid-" + str(index), "type":"learning_aid",
                "placement":"body", "evidence_kind":"teaching_example", "aid":{"id":"cards-" + str(index),
                    "type":"steps", "version":1, "evidence_kind":"teaching_example",
                    "config":{"steps":[{"title":"Place an item", "text":"One position is used."}]},
                    "fallback":{"version":1, "blocks":[{"id":"ov-capacity", "type":"paragraph", "text":"One position is used."}]}}})
        self.assertEqual(validate_learning_content(data), ([], []))

    def test_publication_still_requires_ready_named_reviewer_and_all_six_gates(self):
        data = example()
        mutations = [("content_status", "incomplete"), ("content_review", {})]
        for field, value in mutations:
            wrong = copy.deepcopy(data); wrong[field] = value
            self.assertTrue(validate_learning_content(wrong)[0])
        for gate in FACTUAL_GATES:
            wrong = copy.deepcopy(data); wrong["content_review"]["factual_gates"][gate] = False
            self.assertTrue(validate_learning_content(wrong)[0])
        data["content_review"]["reviewer"] = ""
        self.assertTrue(any("reviewer" in error for error in validate_learning_content(data)[0]))

    def test_scaffold_is_32_body_only_and_keeps_review_unfinished(self):
        for preset in PRESETS:
            data, todo = build_scaffold("draft", preset, "Draft", "草稿")
            self.assertEqual(data["content_contract_version"], "3.2")
            self.assertNotIn("learning_catalog", data)
            self.assertEqual(data["content_status"], "incomplete")
            self.assertEqual(data["translation"], {"status":"deferred"})
            self.assertEqual(data["sections"], [])
            self.assertEqual(data["content_review"]["reviewer"], "")
            self.assertTrue(all(value is False for value in data["content_review"]["factual_gates"].values()))
            blocks = [block for view in (data["overview"]["sections"], data["explanation"]["sections"], data["sections"])
                      for section in view for block in section["blocks"]]
            self.assertTrue(all("learning" not in block for block in blocks))
            self.assertNotIn("formula_explanations", json.dumps(data))
            self.assertLess(len(json.dumps(todo, ensure_ascii=False)), 3200)

    def test_32_schema_accepts_body_only_and_rejects_learning_and_unclassified_nested_blocks(self):
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError:
            self.skipTest("optional jsonschema package is not installed")
        schemas = {p.name:json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "schemas").glob("*.json")}
        registry = Registry().with_resources((name, Resource.from_contents(schema)) for name, schema in schemas.items())
        validator = Draft202012Validator(schemas["paper-data-3.2.schema.json"], registry=registry)
        self.assertEqual(list(validator.iter_errors(deferred_example())), [])
        deferred = deferred_example()
        deferred["translation"]["status"] = "ready"
        self.assertTrue(list(validator.iter_errors(deferred)))
        deferred.pop("translation")
        self.assertTrue(list(validator.iter_errors(deferred)))
        data = with_formula()
        self.assertEqual(list(validator.iter_errors(data)), [])
        data = with_formula()
        data["explanation"]["sections"][0]["blocks"].append({"id":"case", "type":"details", "summary":"Case",
            "evidence_kind":"teaching_example", "blocks":[{"id":"nested", "type":"formula", "latex":"x=1"}]})
        self.assertTrue(list(validator.iter_errors(data)))
        data["explanation"]["sections"][0]["blocks"][-1]["blocks"][0]["evidence_kind"] = "teaching_example"
        self.assertEqual(list(validator.iter_errors(data)), [])
        data["overview"]["sections"][0]["blocks"][0]["learning"] = {"id":"opt", "blocks":[
            {"id":"opt-p", "type":"paragraph", "text":"An optional concrete case."}]}
        self.assertTrue(list(validator.iter_errors(data)))


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT.parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paper = self.root / "vault/papers/paper"
        self.paper.mkdir(parents=True)
        self.output = self.root / "isolated-preview"
        self.data = with_formula()
        self.data["id"] = "paper"
        self.data["content_status"] = "incomplete"
        self.data["content_review"] = {"reviewer":"", "factual_gates":{gate:False for gate in FACTUAL_GATES}}
        (self.paper / ".reader").mkdir()
        (self.paper / ".reader/state.sqlite3").write_bytes(b"user-state-sentinel")
        (self.paper / "annotations.json").write_bytes(b'{"annotations":[{"id":"keep"}]}')
        (self.paper / "reader.html").write_bytes(b"formal-deployment-sentinel")
        (self.paper / "source.pdf").write_bytes(b"%PDF-fixture")
        (self.paper / "assets").mkdir()
        (self.paper / "assets/safe.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="20" height="20"/></svg>', encoding="utf-8")
        self.data["explanation"]["sections"][0]["blocks"].append({"id":"illustration", "type":"figure",
            "src":"assets/safe.svg", "evidence_kind":"teaching_example", "source_refs":[]})
        self.save()

    def save(self):
        (self.paper / "paper-data.js").write_text("window.PAPER_DATA = " + json.dumps(self.data) + ";", encoding="utf-8")

    def snapshot(self):
        return {p.relative_to(self.paper).as_posix():p.read_bytes() for p in self.paper.rglob("*") if p.is_file()}

    def test_preview_relaxes_only_review_and_preserves_formal_files_and_state(self):
        before = self.snapshot()
        self.assertTrue(validate_paper_data(self.paper / "paper-data.js", self.paper)[0])
        self.assertEqual(validate_paper_data(self.paper / "paper-data.js", self.paper, preview=True)[0], [])
        output, manifest = deploy_preview(self.paper, ROOT, self.output)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(output, self.output.resolve())
        self.assertTrue(manifest["reader_preview"])
        draft = load_paper_data(output / "paper-data.js")
        self.assertEqual(draft["content_status"], "incomplete")
        self.assertEqual(draft["content_review"], self.data["content_review"])
        self.assertNotEqual(draft["id"], self.data["id"])
        self.assertFalse((output / ".reader").exists())
        self.assertFalse((output / "annotations.json").exists())
        self.assertEqual((output / "assets/safe.svg").read_bytes(), (self.paper / "assets/safe.svg").read_bytes())
        html = (output / "reader.html").read_text(encoding="utf-8")
        self.assertIn("草稿预览", html)
        self.assertLess(html.index('src="preview-config.js"'), html.index('src="paper-data.js'))
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)

    def test_preview_rejects_unsafe_nested_content_bad_formula_and_missing_assets(self):
        mutations = [lambda d:d["explanation"]["sections"][0]["blocks"][0].update(onclick="alert(1)"),
            lambda d:d["explanation"]["sections"][0]["blocks"][1].update(latex=r"\input{secret}"),
            lambda d:d["explanation"]["sections"][0]["blocks"][-1].update(src="../outside.svg"),
            lambda d:d["explanation"]["sections"][0]["blocks"][-1].update(src="assets/missing.svg"),
            lambda d:d["explanation"]["sections"][0]["blocks"].append({"id":"nested", "type":"details", "summary":"Case", "evidence_kind":"teaching_example", "blocks":[
                {"id":"nested-html", "type":"paragraph", "text":"<img src=x onerror=alert(1)>"}]}),
            lambda d:d["learning_requirements"].update(required_formula_ids=["missing"])]
        original = copy.deepcopy(self.data)
        for mutation in mutations:
            self.data = copy.deepcopy(original); mutation(self.data); self.save()
            before = self.snapshot()
            with self.assertRaises(ValueError):
                deploy_preview(self.paper, ROOT, self.output)
            self.assertFalse(self.output.exists())
            self.assertEqual(before, self.snapshot())

    def test_body_can_be_previewed_before_translation_is_finished(self):
        self.data["sections"] = []
        # The original draft still cites the PDF, not nonexistent translation blocks.
        for view in ("overview", "explanation"):
            for section in self.data[view]["sections"]:
                for block in section["blocks"]:
                    for ref in block.get("source_refs", []):
                        ref.pop("block_id", None)
                        ref.pop("content_id", None)
        self.save()
        before = self.snapshot()
        self.assertEqual(validate_paper_data(self.paper/"paper-data.js", self.paper, preview=True)[0], [])
        self.assertTrue(any("body.sections" in error for error in validate_paper_data(self.paper/"paper-data.js", self.paper)[0]))
        output, _ = deploy_preview(self.paper, ROOT, self.output)
        self.assertEqual(load_paper_data(output/"paper-data.js")["sections"], [])
        self.assertEqual(before, self.snapshot())
        self.data["sections"] = "unfinished"
        self.save()
        self.assertTrue(any("sections" in error for error in validate_paper_data(self.paper/"paper-data.js", self.paper, preview=True)[0]))

    def test_explicit_deferred_is_a_formal_delivery_not_a_translation_claim(self):
        self.data = deferred_example()
        self.data["id"] = "paper"
        self.save()
        self.assertEqual(validate_paper_data(self.paper/"paper-data.js", self.paper)[0], [])
        self.data.pop("translation")
        self.save()
        self.assertTrue(validate_paper_data(self.paper/"paper-data.js", self.paper)[0])
        self.data["translation"] = {"status":"ready"}
        self.save()
        self.assertTrue(validate_paper_data(self.paper/"paper-data.js", self.paper)[0])

    def test_output_cannot_overlap_formal_skill(self):
        for destination in (self.paper, self.paper / "preview", self.paper.parent, ROOT / "preview",
                self.root / "vault/preview", self.root / "vault/.reader/preview"):
            with self.assertRaisesRegex(ValueError, "隔离"):
                deploy_preview(self.paper, ROOT, destination)

    def test_repeated_preview_uses_fresh_sibling_and_preserves_previous_snapshot(self):
        original, _ = deploy_preview(self.paper, ROOT, self.output)
        first_bytes = {p.relative_to(original):p.read_bytes() for p in original.rglob("*") if p.is_file()}
        before = self.snapshot()
        self.data["title_en"] = "Revised title"
        self.save()
        revised_source = self.snapshot()
        revised, _ = deploy_preview(self.paper, ROOT, self.output)
        self.assertEqual(revised.parent, original.parent)
        self.assertNotEqual(revised, original)
        self.assertEqual(load_paper_data(revised / "paper-data.js")["title_en"], "Revised title")
        self.assertEqual(first_bytes, {p.relative_to(original):p.read_bytes() for p in original.rglob("*") if p.is_file()})
        self.assertEqual(revised_source, self.snapshot())
        self.assertEqual(before[".reader/state.sqlite3"], self.snapshot()[".reader/state.sqlite3"])
        self.assertFalse((revised / ".reader").exists())
        self.assertFalse((revised / "annotations.json").exists())

    def test_occupied_unknown_directory_or_file_is_preserved(self):
        self.output.mkdir()
        (self.output / "keep.txt").write_bytes(b"keep")
        output, _ = deploy_preview(self.paper, ROOT, self.output)
        self.assertNotEqual(output, self.output)
        self.assertEqual(list(self.output.iterdir()), [self.output / "keep.txt"])
        self.assertEqual((self.output / "keep.txt").read_bytes(), b"keep")
        occupied_file = self.root / "unknown.txt"
        occupied_file.write_bytes(b"unknown file")
        output, _ = deploy_preview(self.paper, ROOT, occupied_file)
        self.assertEqual(output.parent, occupied_file.parent)
        self.assertTrue((output / "reader.html").is_file())
        self.assertEqual(occupied_file.read_bytes(), b"unknown file")

    def test_default_preview_is_separate_and_can_preview_31(self):
        self.data["content_contract_version"] = "3.1"
        self.data["learning_catalog"] = {"concepts":[], "aids":[]}
        self.data["learning_requirements"]["required_formula_ids"] = []
        self.data["explanation"]["sections"][0]["blocks"][1]["key_formula"] = False
        self.save()
        with patch("deploy_reader.tempfile.tempdir", str(self.root)):
            output, _ = deploy_preview(self.paper, ROOT)
        self.assertEqual(output.parent, self.root)
        self.assertTrue(output.name.startswith("arm-draft-preview-"))
        self.assertFalse(output.is_relative_to(self.paper))

    def test_unsafe_cached_or_environment_temp_root_is_rejected_before_any_write(self):
        for unsafe in (self.paper, self.paper / ".reader", self.root / "vault/.reader", ROOT, ROOT / "assets"):
            before = self.snapshot()
            children = set(unsafe.rglob("*")) if unsafe.exists() else set()
            with patch("deploy_reader.tempfile.tempdir", str(unsafe)), patch("deploy_reader.atomic_write") as write:
                with self.assertRaisesRegex(ValueError, "隔离"):
                    deploy_preview(self.paper, ROOT)
                write.assert_not_called()
            self.assertEqual(before, self.snapshot())
            self.assertEqual(set(unsafe.rglob("*")) if unsafe.exists() else set(), children)
        with patch("deploy_reader.tempfile.tempdir", None), patch.dict("os.environ", {"TMPDIR":str(self.paper / ".reader")}):
            with self.assertRaisesRegex(ValueError, "隔离"):
                deploy_preview(self.paper, ROOT)

    def test_unknown_metadata_cannot_copy_private_state_or_annotations(self):
        self.data["metadata"] = {"src":"annotations.json", "nested":{"asset":".reader/state.sqlite3"}}
        self.save()
        before = self.snapshot()
        self.assertEqual(validate_paper_data(self.paper / "paper-data.js", self.paper, preview=True)[0], [])
        output, manifest = deploy_preview(self.paper, ROOT, self.output)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((output / "annotations.json").exists())
        self.assertFalse((output / ".reader").exists())
        self.assertNotIn("annotations.json", manifest["files"])
        self.assertFalse(any(name.startswith(".reader") for name in manifest["files"]))
        self.assertFalse(any(b"user-state-sentinel" in path.read_bytes() for path in output.rglob("*") if path.is_file()))

    def test_cli_marks_draft_and_does_not_create_formal_annotation_stub(self):
        (self.paper / "annotations.json").unlink()
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(ROOT / "scripts/deploy_reader.py"), "--vault", str(self.root / "vault"),
            "--paper", "paper", "--preview", "--preview-dir", str(self.output)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("草稿预览", result.stdout)
        self.assertIn(str(self.output / "reader.html"), result.stdout)
        self.assertEqual(before, self.snapshot())
        repeated = subprocess.run(result.args, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        output_line = next(line for line in repeated.stdout.splitlines() if line.startswith("✓ 草稿预览"))
        actual_html = Path(output_line.split(": ", 1)[1])
        self.assertTrue(actual_html.is_file())
        self.assertNotEqual(actual_html.parent, self.output)
        self.assertEqual(actual_html.parent.parent, self.output.parent)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
