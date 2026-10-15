"""Deployment must preserve user content and reject unsafe/incomplete upgrades."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy_reader import (READER_VERSION, V3_TEMPLATE_FILES, deploy_templates, normalized_template,
                           validate_paper_data)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / "skill"
        self.assets = self.skill / "assets"
        self.paper = self.root / "paper"
        self.assets.mkdir(parents=True)
        self.paper.mkdir()
        for name in V3_TEMPLATE_FILES:
            text = ('<script src="paper-data.js"></script><script src="reader-v3.js"></script>'
                    '<script src="content-renderer.js"></script><script src="learning-components.js"></script>'
                    '<link href="styles-v3.css" rel="stylesheet">') if name.endswith(".html") else "/* fixture */"
            (self.assets / name).write_text(text, encoding="utf-8")
        (self.assets / "math").mkdir()
        (self.assets / "math/katex.min.js").write_text("/* synthetic dependency */", encoding="utf-8")
        self.data = {"schema_version": 3, "id": "paper", "content_version": "one"}
        self.source = b"window.PAPER_DATA = {};"
        (self.paper / "paper-data.js").write_bytes(self.source)
        (self.paper / "annotations.json").write_text('{"annotations":[{"id":"keep"}]}', encoding="utf-8")

    def deploy(self, **kwargs):
        return deploy_templates(self.paper, self.skill, self.data, **kwargs)

    def test_complete_bundle_has_hashes_and_keeps_content_and_annotations(self):
        manifest = self.deploy()
        self.assertEqual(manifest["reader_version"], READER_VERSION)
        self.assertEqual(READER_VERSION, (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip())
        self.assertNotIn("default_annotations", manifest["capabilities"])
        self.assertIn("weighted_degree", manifest["capabilities"])
        self.assertIn("body_learning_aids", manifest["capabilities"])
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.paper / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((self.paper / "paper-data.js").read_bytes(), self.source)
        self.assertEqual(json.loads((self.paper / "annotations.json").read_text())["annotations"][0]["id"], "keep")
        self.assertIn('content-renderer.js?v=', (self.paper / "reader.html").read_text())
        self.assertEqual(self.deploy(), manifest)

    def test_missing_new_component_cannot_partially_overwrite_old_files(self):
        self.deploy()
        before = (self.paper / "reader.html").read_bytes()
        (self.assets / "learning-components.js").unlink()
        (self.assets / "reader-v3.html").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "模板缺失"):
            self.deploy()
        self.assertEqual((self.paper / "reader.html").read_bytes(), before)

    def test_local_modification_requires_explicit_backup_and_replacement(self):
        self.deploy()
        target = self.paper / "styles-v3.css"
        target.write_text("/* user's layout */", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "自定义"):
            self.deploy()
        self.assertEqual(target.read_text(), "/* user's layout */")
        self.deploy(replace_custom=True)
        backups = list(self.paper.glob(".reader-template-backup-*.zip"))
        self.assertEqual(len(backups), 1)
        import zipfile
        with zipfile.ZipFile(backups[0]) as archive:
            self.assertEqual(archive.read("styles-v3.css"), b"/* user's layout */")

    def test_old_cache_queries_do_not_mark_known_template_as_custom(self):
        old_html = (self.assets / "reader-v3.html").read_bytes().replace(b"reader-v3.js", b"reader-v3.js?v=12345")
        (self.paper / "reader.html").write_bytes(old_html)
        manifest = self.deploy()
        self.assertEqual(manifest["reader_version"], READER_VERSION)

    def test_known_legacy_hash_can_upgrade_without_force(self):
        old = b"/* previous release */"
        (self.paper / "reader-v3.js").write_bytes(old)
        legacy = {"reader-v3.js": [hashlib.sha256(normalized_template(old, "reader-v3.js")).hexdigest()]}
        (self.assets / "legacy-template-hashes.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.deploy()
        self.assertNotEqual((self.paper / "reader-v3.js").read_bytes(), old)

    def test_interrupted_upgrade_restores_previously_written_files(self):
        self.deploy()
        before = {p.name: p.read_bytes() for p in self.paper.iterdir() if p.is_file()}
        (self.assets / "reader-v3.js").write_text("new code", encoding="utf-8")
        import deploy_reader
        original = deploy_reader.atomic_write
        calls = 0
        def fail_once(path, value):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated interrupted write")
            return original(path, value)
        with patch.object(deploy_reader, "atomic_write", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.deploy()
        self.assertEqual({p.name: p.read_bytes() for p in self.paper.iterdir() if p.is_file()}, before)

    def test_future_schema_rejected_by_deployer_like_frontend(self):
        path = self.paper / "paper-data.js"
        for value in (4, "3", True, None):
            path.write_text("window.PAPER_DATA = " + json.dumps({"schema_version": value}) + ";", encoding="utf-8")
            errors, _ = validate_paper_data(path, self.paper)
            self.assertTrue(errors)

    def test_malformed_agent_output_returns_errors_instead_of_crashing(self):
        path = self.paper / "paper-data.js"
        for value in ([], None, {"schema_version": 3, "sections": [{"id": [], "blocks": {}}],
                               "overview": {"sections": [{"id": "o", "blocks": [{"id": {}, "type": []}]}]}}):
            with self.subTest(value=value):
                path.write_text("window.PAPER_DATA = " + json.dumps(value) + ";", encoding="utf-8")
                errors, _ = validate_paper_data(path, self.paper)
                self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
