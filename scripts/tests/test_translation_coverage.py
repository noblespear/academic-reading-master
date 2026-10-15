import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

try:
    import pymupdf as fitz
except ImportError:
    import fitz

spec = importlib.util.spec_from_file_location("coverage_audit", Path(__file__).parents[1] / "audit_translation_coverage.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class CoverageTests(unittest.TestCase):
    def run_audit(self, pages, translations, extra=None):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            pdf = Path(directory) / "source.pdf"
            data = Path(directory) / "paper-data.js"
            doc = fitz.open()
            for texts in pages:
                page = doc.new_page()
                for index, text in enumerate(texts):
                    page.insert_text((60, 70 + index * 100), text)
            doc.save(pdf)
            doc.close()
            sections = [{"id": "translation-p001", "blocks": [
                {"id": f"translated-{index}", "text_en": text,
                 "source_refs": [{"page": page} for page in refs]}
                for index, (text, refs) in enumerate(translations)]}]
            value = {"sections": sections, **(extra or {})}
            data.write_text("window.PAPER_DATA = " + json.dumps(value) + ";", encoding="utf-8")
            return coverage.audit(pdf, data)

    def test_missing_figure_prompt(self):
        report = self.run_audit([["In a bustling marketplace, taking photos of street art."]], [])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["candidates"][0]["page"], 1)
        self.assertEqual(len(report["candidates"][0]["bbox"]), 4)

    def test_missing_table_caption(self):
        report = self.run_audit([["Table 1. Quantitative comparisons. The best results are underlined."]],
                                [("Table 1. Quantitative comparisons.", [1])])
        self.assertTrue(any("best results" in item["text"] for item in report["candidates"]))

    def test_cross_page_source_ranges(self):
        pages = [["Images preserve detailed identity"], ["while backgrounds change across scenes."]]
        full = "Images preserve detailed identity while backgrounds change across scenes."
        good = self.run_audit(pages, [(full, [1, 2])])
        self.assertEqual(good["status"], "no_candidates_found")
        wrong = self.run_audit(pages, [(full, [1])])
        self.assertTrue(any(c["kind"] == "source_page_mismatch" and c["page"] == 2 for c in wrong["candidates"]))

    def test_hyphen_unicode_normalization(self):
        self.assertEqual(coverage.normalize("con-\nsistency text-to-image ﬁne  detail"),
                         coverage.normalize("consistency texttoimage fine detail"))
        report = self.run_audit([["Fine grained con-\nsistency preserves detailed identity."]],
                                [("Fine grained consistency preserves detailed identity.", [1])])
        self.assertEqual(report["status"], "no_candidates_found")

    def test_overview_cannot_cancel_omission(self):
        text = "A young boy with curly brown hair rides a roller coaster."
        report = self.run_audit([[text]], [], {"overview": {"text_en": text}})
        self.assertEqual(report["candidates"][0]["kind"], "unmatched_text")

    def test_custom_section_id_is_translation(self):
        text = "A young boy with curly brown hair rides a roller coaster."
        sections = [{"id": "appendix-images", "blocks": [
            {"id": "custom-block", "text_en": text, "source_refs": [{"page": 1}]}]}]
        report = self.run_audit([[text]], [], {"sections": sections})
        self.assertEqual(report["status"], "no_candidates_found")

    def test_complete_caption_counts(self):
        text = "The best results within training free methods are underlined."
        sections = [{"id": "results", "blocks": [
            {"id": "table-one", "type": "table", "text_en": "Table one comparisons.",
             "caption_en": text, "source_refs": [{"page": 1}]}]}]
        report = self.run_audit([[text]], [], {"sections": sections})
        self.assertEqual(report["status"], "no_candidates_found")

    def test_english_table_fields_count(self):
        text = "Facial identity consistency improves significantly."
        sections = [{"id": "results", "blocks": [
            {"id": "table-one", "headers_en": ["Facial identity consistency"],
             "rows_en": [["improves significantly"]], "source_refs": [{"page": 1}]}]}]
        report = self.run_audit([[text]], [], {"sections": sections})
        self.assertEqual(report["status"], "no_candidates_found")

    def test_blank_pdf_is_not_pass(self):
        report = self.run_audit([[]], [("Any unrelated translated text.", [1])])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["pages_without_extractable_text"], [1])

    def test_short_labels_are_reported_as_skipped(self):
        report = self.run_audit([["123", "RoPE"]], [])
        self.assertEqual(report["checked_units"], 0)
        self.assertEqual(report["skipped_unit_count"], 2)
        self.assertEqual(report["status"], "needs_review")

    def test_spacing_and_precomposed_diacritics_compare_without_splitting_names(self):
        self.assertEqual(coverage.normalize("Patrick Esser and Bj¨orn Ommer met Jonas M¨uller."),
                         coverage.normalize("Patrick Esser and Björn Ommer met Jonas Müller."))
        self.assertEqual(coverage.normalize("The naïve estimator uses café samples."),
                         coverage.normalize("The naive estimator uses cafe samples."))

    def test_pdf_word_split_across_pages_requires_context_and_correct_page_refs(self):
        pages = [["Their successful scaling has led to significant improve-"],
                 ["ments in both image quality and text alignment."]]
        translated = "Their successful scaling has led to significant improvements in both image quality and text alignment."
        good = self.run_audit(pages, [(translated, [1, 2])])
        self.assertEqual(good["status"], "no_candidates_found")
        self.assertTrue(any(note["comparison_word"] == "improvements" for note in good["comparison_normalizations"]))
        wrong = self.run_audit(pages, [(translated, [1])])
        self.assertTrue(any(c["page"] == 2 and c["kind"] == "source_page_mismatch" for c in wrong["candidates"]))

    def test_pdf_reference_word_split_between_text_blocks(self):
        pages = [["Olaf Ronneberger, Philipp Fischer, and Thomas Brox. U-",
                  "net: Convolutional networks for biomedical image segmentation."]]
        translated = "Olaf Ronneberger, Philipp Fischer, and Thomas Brox. U-net: Convolutional networks for biomedical image segmentation."
        good = self.run_audit(pages, [(translated, [1])])
        self.assertEqual(good["status"], "no_candidates_found")
        self.assertTrue(any(note["comparison_word"] == "Unet" for note in good["comparison_normalizations"]))

    def test_continuous_english_in_adjacent_translation_blocks_counts(self):
        source = "Facial identity consistency improves significantly across changing backgrounds."
        report = self.run_audit([[source]], [("Facial identity consistency improves", [1]),
            ("significantly across changing backgrounds.", [1])])
        self.assertEqual(report["status"], "no_candidates_found")
        self.assertTrue(any(note["kind"] == "translation_block_boundary" for note in report["comparison_normalizations"]))

    def test_translation_boundary_hyphen_matches_only_when_pdf_attests_word(self):
        source = "Their successful scaling has led to significant improvements in both image quality and text alignment."
        report = self.run_audit([[source]], [("Their successful scaling has led to significant improve-", [1]),
            ("ments in both image quality and text alignment.", [1])])
        self.assertEqual(report["status"], "no_candidates_found")
        wrong = self.run_audit([[source]], [("Their successful scaling has led to significant improve-", [1]),
            ("ness in both image quality and text alignment.", [1])])
        self.assertEqual(wrong["status"], "needs_review")
        self.assertFalse(wrong["comparison_normalizations"])

    def test_omitted_context_remains_candidate_after_boundary_repair(self):
        pages = [["Their successful scaling has led to significant improve-"],
            ["ments in both image quality and text alignment. A matching background must retain the moving spotlight."]]
        translated = "Their successful scaling has led to significant improvements in both image quality and text alignment."
        report = self.run_audit(pages, [(translated, [1, 2])])
        self.assertEqual(report["status"], "needs_review")
        self.assertTrue(any("moving spotlight" in c["text"] for c in report["candidates"]))
        self.assertTrue(all(c["ngram_coverage"] < .9 for c in report["candidates"]))

    def test_uncertain_boundary_is_not_silently_removed(self):
        report = self.run_audit([["Successful scaling has led to significant improve-"],
            ["ments in image quality and text alignment."]],
            [("Successful scaling has led to significant improvement in image quality and text alignment.", [1, 2])])
        self.assertEqual(report["status"], "needs_review")
        self.assertFalse(report["comparison_normalizations"])
        self.assertTrue(any("improve-" in c["text"] or "ments" in c["text"] for c in report["candidates"]))


if __name__ == "__main__":
    unittest.main()
