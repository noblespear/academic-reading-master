"""Release archives preserve binary assets and are independent of checkout EOLs."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


spec = importlib.util.spec_from_file_location(
    "skill_packager", Path(__file__).resolve().parents[2] / "tools/package_skill.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def fixture(self, root):
        for name in packager.TOP_FILES:
            (root / name).write_bytes(b"3.2.0\n" if name == "VERSION" else b"Example\nSecond line\n")
        (root / "assets").mkdir()
        (root / "assets/example.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary\r\n")
        (root / "references").mkdir()
        (root / "references/example.md").write_bytes(b"# Example\n\nText.\n")

    def build(self, root, output):
        with patch.object(packager, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
            packager.package(output)
        return output / "academic-reading-master-v3.2.0.skill"

    def test_checkout_line_endings_do_not_change_archive_or_binary_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            first = self.build(root, root / "dist-lf").read_bytes()
            for path in [*(root / name for name in packager.TOP_FILES), root / "references/example.md"]:
                path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            second = self.build(root, root / "dist-crlf")
            self.assertEqual(first, second.read_bytes())
            with zipfile.ZipFile(second) as archive:
                self.assertEqual(archive.read("academic-reading-master/assets/example.png"),
                                 (root / "assets/example.png").read_bytes())
                self.assertEqual(archive.read("academic-reading-master/references/example.md"),
                                 b"# Example\n\nText.\n")

    def test_private_upstream_and_runtime_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            for name in ("vendor/visualize/SKILL.md", "vendor/visualize-source.json", ".reader/private.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private", encoding="utf-8")
            with zipfile.ZipFile(self.build(root, root / "dist")) as archive:
                self.assertFalse(any("visualize" in name or ".reader" in name for name in archive.namelist()))


if __name__ == "__main__":
    unittest.main()
