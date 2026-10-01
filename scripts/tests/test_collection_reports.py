import json
from pathlib import Path
import sys
import tempfile
import unittest



sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_papers import collect, duplicate, normalize_arxiv, pdf_audit
from collect_report_sources import collect as sources
from export_weekly_report import build
from math_validation import validate_math



class CollectionTests(unittest.TestCase):
    def test_doi_dedup_preserves_reading_assets_and_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            a = {'title': 'Paper A', 'doi': 'https://doi.org/10.1000/AbC', 'year': 2026, 'authors': ['Author'], 'venue': 'Preprint'}
            result = collect(vault, {'query': 'topic', 'papers': [a]}, download=False)
            paper_id = result['papers'][0]['id']
            libfile = vault / 'library.json'
            lib = json.loads(libfile.read_text(encoding='utf-8'))
            lib['level'] = 'expert'; lib['papers'][0]['status'] = 'reading'; lib['custom'] = {'keep': 1}
            libfile.write_text(json.dumps(lib), encoding='utf-8')
            content = vault / 'papers' / paper_id / 'paper-data.js'
            content.write_text('do not overwrite', encoding='utf-8')
            result = collect(vault, {'papers': [{**a, 'doi': 'doi:10.1000/abc', 'venue': 'Conference'}]}, download=False)
            lib = json.loads(libfile.read_text(encoding='utf-8'))
            self.assertEqual(len(lib['papers']), 1)
            self.assertEqual(lib['papers'][0]['status'], 'reading')
            self.assertEqual(lib['level'], 'expert')
            self.assertEqual(lib['custom'], {'keep': 1})
            self.assertTrue(result['papers'][0]['duplicate'])
            self.assertEqual(content.read_text(), 'do not overwrite')
            self.assertEqual(lib['papers'][0]['related_versions'][0]['venue'], 'Conference')

    def test_distinct_authors_not_collapsed_and_arxiv_version_matches(self):
        self.assertFalse(duplicate({'title': 'Same title', 'authors': ['A']}, {'title': 'Same title', 'authors': ['B']}))
        self.assertEqual(normalize_arxiv('https://arxiv.org/pdf/2501.12345v3.pdf'), '2501.12345')
        self.assertTrue(duplicate({'arxiv_id': '2501.12345v1'}, {'arxiv_id': '2501.12345v2'}))

    def test_bad_manifest_and_non_pdf_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            with self.assertRaises(ValueError):
                collect(vault, {'papers': [{'title': 'Ok'}, {'title': 'Bad', 'id': '../escape'}]}, False)
            self.assertFalse((vault / 'library.json').exists())
            bad = vault / 'bad.pdf'; bad.write_bytes(b'<html>not a pdf</html>')
            with self.assertRaises(ValueError):
                pdf_audit(bad, {'title': 'Paper'})

    def test_binary_evidence_is_not_claimed_as_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'log.md').write_text('actual evidence', encoding='utf-8')
            (path / 'image.png').write_bytes(b'not text')
            result = sources([path])
            byname = {Path(s['path']).name: s for s in result['sources']}
            self.assertEqual(byname['log.md']['text'], 'actual evidence')
            self.assertEqual(byname['image.png']['read_status'], 'binary_requires_separate_read')
            self.assertNotIn('text', byname['image.png'])


class MarkdownReportTests(unittest.TestCase):
    def test_strict_math_and_unbalanced_delimiters_rejected(self):
        self.assertEqual(validate_math([r'\(\mathbf{x}+\mathbb{R}+\mathcal{L}+\frac{1}{2}+\sqrt{x}\)']), 1)
        with self.assertRaises(ValueError):
            validate_math([r'\(\notarealcommand{x}\)'])
        with self.assertRaises(ValueError):
            validate_math([r'Unclosed \(x'])
        with self.assertRaises(ValueError):
            validate_math(['Equation $x^2 is not closed.'])
        self.assertEqual(validate_math(['Cost $5 and $10.']), 0)

    def test_md_only_all_surfaces_table_escape_and_currency(self):
        with tempfile.TemporaryDirectory() as directory:
            data = {'type': 'work', 'title': r'Title \(x^2\)', 'subtitle': r'Sub \(a\)',
                    'introduction': r'Cost $5 and $10, but math $x^2$ and \(\mathbf{x}\).',
                    'sections': [{'title': r'Section \(y\)', 'paragraphs': [r'\[\frac{1}{2}\]'],
                                  'tables': [{'caption': r'Table \(E=mc^2\)', 'rows': [['A', 'B'], [r'\(a/b\)', 'value | two\nlines']]}]}],
                    'sources': [r'Source \(z\)']}
            out = Path(directory) / 'report.md'
            result = build(data, out, directory)
            text = out.read_text(encoding='utf-8')
            self.assertEqual(result['format'], 'md')
            self.assertEqual(result['validated_math_expressions'], 9)
            self.assertIn('| --- | --- |', text)
            self.assertIn(r'value \| two', text)
            self.assertIn('$5 and $10', text)
            self.assertIn('$$\\frac{1}{2}$$', text)
            self.assertNotIn('\\(', text)
            with self.assertRaises(ValueError):
                build(data, Path(directory) / 'wrong.docx', directory)
            with self.assertRaises(ValueError):
                build(data, out, directory, strict_length=True)


if __name__ == '__main__':
    unittest.main()
