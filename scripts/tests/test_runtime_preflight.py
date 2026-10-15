"""Launch-time PDF dependency failures must precede an apparently ready service."""
import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import open_reader
import serve_reader_v3


class RuntimePreflightTests(unittest.TestCase):
    def test_missing_dependency_names_actual_interpreter_and_install_command(self):
        with patch.object(serve_reader_v3.importlib, "import_module", side_effect=ImportError("not installed")):
            with self.assertRaises(RuntimeError) as failure:
                serve_reader_v3.require_pdf_runtime()
        message = str(failure.exception)
        self.assertIn(sys.executable, message)
        self.assertIn("-m pip install PyMuPDF", message)

    def test_legacy_import_works_and_unrelated_fitz_is_rejected(self):
        pdf = MagicMock()
        with patch.object(serve_reader_v3.importlib, "import_module", side_effect=[ImportError(), pdf]):
            serve_reader_v3.require_pdf_runtime()
        pdf.open.return_value.__enter__.return_value.new_page.return_value.get_text.assert_called_once()
        with patch.object(serve_reader_v3.importlib, "import_module", side_effect=[ImportError(), object()]):
            with self.assertRaisesRegex(RuntimeError, "PyMuPDF"):
                serve_reader_v3.require_pdf_runtime()

    def test_launcher_blocks_new_process_before_writing_launcher_or_opening_browser(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent.parent) as directory:
            vault = Path(directory)
            with patch.object(open_reader, "live_v3_state", return_value=None), \
                    patch.object(serve_reader_v3, "require_pdf_runtime", side_effect=RuntimeError("missing PyMuPDF")), \
                    patch.object(open_reader, "write_bat") as bat, \
                    patch.object(open_reader.subprocess, "Popen") as launch, \
                    patch.object(open_reader.webbrowser, "open") as browser, contextlib.redirect_stderr(io.StringIO()):
                code = open_reader.open_v3(vault / "papers/paper", vault, argparse.Namespace())
            self.assertEqual(code, 2)
            bat.assert_not_called()
            launch.assert_not_called()
            browser.assert_not_called()
            self.assertFalse((vault / ".reader/server.json").exists())

    def test_existing_service_reuse_skips_dependency_probe(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent.parent) as directory:
            vault = Path(directory)
            args = argparse.Namespace(bridge="none", no_open=True)
            with patch.object(open_reader, "live_v3_state", return_value={"url":"http://127.0.0.1:8790"}), \
                    patch.object(serve_reader_v3, "require_pdf_runtime") as probe, \
                    patch.object(open_reader, "write_bat"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(open_reader.open_v3(vault / "papers/paper", vault, args), 0)
            probe.assert_not_called()

    def test_file_preview_skips_dependency_probe(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent.parent) as directory:
            paper = Path(directory) / "papers/paper"
            paper.mkdir(parents=True)
            (paper / "reader.html").write_text("preview", encoding="utf-8")
            with patch.object(sys, "argv", ["open_reader.py", str(paper / "reader.html"), "--file"]), \
                    patch.object(open_reader, "open_file", return_value=0) as file_open, \
                    patch.object(serve_reader_v3, "require_pdf_runtime") as probe:
                with self.assertRaises(SystemExit) as outcome:
                    open_reader.main()
            self.assertEqual(outcome.exception.code, 0)
            file_open.assert_called_once()
            probe.assert_not_called()

    def test_direct_service_cli_fails_before_server_creation(self):
        with patch.object(sys, "argv", ["serve_reader_v3.py", "--vault", "unused", "--paper", "paper"]), \
                patch.object(serve_reader_v3, "require_pdf_runtime", side_effect=RuntimeError("missing PyMuPDF")), \
                patch.object(serve_reader_v3, "ReaderServer") as server, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as outcome:
                serve_reader_v3.main()
        self.assertEqual(outcome.exception.code, 2)
        server.assert_not_called()

    def test_real_service_process_without_site_packages_never_becomes_ready(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent.parent) as directory:
            environment = {key:value for key, value in os.environ.items() if key != "PYTHONPATH"}
            environment["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run([sys.executable, "-S", str(SCRIPTS / "serve_reader_v3.py"),
                "--vault", directory, "--paper", "unused", "--no-open"],
                env=environment, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn(sys.executable, result.stderr)
            self.assertIn("-m pip install PyMuPDF", result.stderr)
            self.assertNotIn("Reader v3 ready", result.stdout)
            self.assertFalse((Path(directory) / ".reader").exists())


if __name__ == "__main__":
    unittest.main()
