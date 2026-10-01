import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_report_explanation import REQUIRED_TOPICS, build, report_headings


class ReportExplanationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.report = self.base / '提交版报告.md'
        self.original = ('# 研究周报\r\n\r\n## 1 调研背景与目标\r\n真实结论。\r\n'
                         '## 2 方法与结果\r\n尚未完成实验，不能声称性能提升。\r\n').encode('utf-8')
        self.report.write_bytes(self.original)
        self.output = self.base / 'learning' / '讲解说明.md'
        self.data = {
            'title': '研究周报讲解说明', 'report_path': self.report.name,
            'introduction': '根据提交版报告解释研究思路，实验结果仍待验证。',
            'sections': [{'title': '目标详解', 'paragraphs': ['研究目标来自报告所列问题。']},
                         {'title': '方法详解', 'paragraphs': ['区分方法设想和已经验证的结果。']}],
            'section_links': [{'report_heading': '1 调研背景与目标', 'explanation_heading': '目标详解'},
                              {'report_heading': '2 方法与结果', 'explanation_heading': '方法详解'}],
            'faq': [{'topics': list(REQUIRED_TOPICS), 'question': '为什么研究这个问题，如何检验方法？',
                     'short_answer': '目标是解决报告中的困难，效果需要后续实验检验。',
                     'explanation': ['先界定共同困难，再解释研究方向和方法。',
                                     r'以 \(\mathcal{L}=\frac{1}{N}\sum_{i=1}^{N} e_i^2\) 为示例指标。'],
                     'evidence': ['提交版报告明确说明尚未完成实验。'],
                     'limitations': '这个公式仅用于讲解，报告尚未确认指标值或性能提升。'}],
            'sources': ['报告引用的正式参考文献。'],
        }

    def assert_source_unchanged(self):
        self.assertEqual(self.report.read_bytes(), self.original)

    def assert_rejected(self, data=None, output=None, message=None):
        target = self.output if output is None else output
        before = target.read_bytes() if target.is_file() else None
        with self.assertRaisesRegex(ValueError, message or '.'):
            build(self.data if data is None else data, target, self.base)
        self.assert_source_unchanged()
        if before is None:
            self.assertFalse(target.exists())
        else:
            self.assertEqual(target.read_bytes(), before)

    def test_success_is_independent_md_and_metadata_hash_is_not_visible(self):
        self.data['report_sha256'] = hashlib.sha256(self.original).hexdigest().upper()
        result = build(self.data, self.output, self.base)
        text = self.output.read_text(encoding='utf-8')
        self.assert_source_unchanged()
        self.assertEqual(result['format'], 'md')
        self.assertEqual(result['path'], str(self.output.resolve()))
        self.assertEqual(result['report_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(result['faq'], 1)
        self.assertEqual(result['covered_topics'], list(REQUIRED_TOPICS))
        self.assertEqual(result['validated_math_expressions'], 1)
        self.assertIn('# 研究周报讲解说明', text)
        self.assertIn('个人学习与汇报准备', text)
        self.assertIn('[提交版报告.md](<../', text)
        self.assertNotIn(result['report_sha256'], text)
        self.assertNotIn('report_sha256', text)
        self.assertNotIn('KaTeX', text)
        self.assertNotIn('.md#', text)
        self.assertLess(text.index('**短答：**'), text.index('**详细讲解：**'))
        self.assertLess(text.index('**详细讲解：**'), text.index('**报告依据：**'))
        self.assertLess(text.index('**报告依据：**'), text.index('**边界与限制：**'))
        self.assertIn('$\\mathcal{L}=\\frac{1}{N}\\sum_{i=1}^{N} e_i^2$', text)
        self.assertNotIn(r'\(', text)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_topics_can_be_combined_and_order_and_question_count_are_free(self):
        self.data.pop('sections')
        self.data.pop('section_links')
        second = copy.deepcopy(self.data['faq'][0])
        self.data['faq'][0]['topics'] = ['evaluation', 'goal', 'other']
        second['topics'] = ['methods', 'metrics', 'directions', 'challenges']
        second['question'] = '方向、方法与指标如何相互对应？'
        self.data['faq'].append(second)
        result = build(self.data, self.output, self.base)
        self.assertEqual(result['sections'], 0)
        self.assertEqual(result['faq'], 2)
        self.assertEqual(result['covered_topics'], list(REQUIRED_TOPICS) + ['other'])
        self.assert_source_unchanged()

    def test_prose_topics_need_not_be_repeated_as_faq_questions(self):
        self.data['sections'].append({'title': '整体认识', 'paragraphs': ['研究方向、共同困难、方法及评价口径见本节。']})
        self.data['faq'][0]['topics'] = ['goal']
        result = build(self.data, self.output, self.base)
        self.assertEqual(result['faq'], 1)
        self.assertEqual(result['covered_topics'], ['goal'])
        self.assert_source_unchanged()

    def test_empty_or_unknown_topic_tags_are_rejected_before_creating_output(self):
        for topics in (list(REQUIRED_TOPICS) + ['invented'], []):
            with self.subTest(topics=topics):
                data = copy.deepcopy(self.data)
                data['faq'][0]['topics'] = topics
                self.assert_rejected(data)
                self.assertFalse(self.output.parent.exists())

    def test_report_overwrite_and_dot_path_alias_are_rejected(self):
        self.assert_rejected(output=self.report, message='source report or a path alias')
        nested = self.base / 'nested'
        nested.mkdir()
        self.assert_rejected(output=nested / '..' / self.report.name, message='source report or a path alias')

    def test_report_hardlink_alias_is_rejected(self):
        alias = self.base / 'hardlink.md'
        try:
            os.link(self.report, alias)
        except OSError as error:
            self.skipTest(f'Hard links are unavailable: {error}')
        self.assert_rejected(output=alias, message='source report or a path alias')

    def test_report_symbolic_link_alias_is_rejected(self):
        alias = self.base / 'symlink.md'
        try:
            alias.symlink_to(self.report)
        except OSError as error:
            self.skipTest(f'Symbolic links are unavailable: {error}')
        self.assert_rejected(output=alias, message='source report or a path alias')
        self.assertTrue(alias.is_symlink())

    @unittest.skipUnless(os.name == 'nt', 'Directory junctions are a Windows alias type.')
    def test_report_parent_directory_junction_alias_is_rejected(self):
        junction_home = tempfile.TemporaryDirectory()
        self.addCleanup(junction_home.cleanup)
        junction = Path(junction_home.name) / 'report-directory-alias'
        created = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(self.base)],
                                 capture_output=True, timeout=10)
        if created.returncode:
            self.skipTest('Windows directory junction creation is unavailable.')
        self.addCleanup(junction.rmdir)
        self.assert_rejected(output=junction / self.report.name, message='source report or a path alias')

    def test_non_markdown_output_and_mismatched_hash_are_rejected(self):
        for suffix in ('.docx', '.pdf', '.txt'):
            with self.subTest(suffix=suffix):
                self.assert_rejected(output=self.output.with_suffix(suffix), message='.md file')
        for expected in ('0' * 64, 'not-a-hash', ''):
            with self.subTest(expected=expected):
                data = copy.deepcopy(self.data)
                data['report_sha256'] = expected
                self.assert_rejected(data)

    def test_missing_and_ambiguous_section_mapping_is_rejected(self):
        for side in ('report_heading', 'explanation_heading'):
            with self.subTest(side=side):
                data = copy.deepcopy(self.data)
                data['section_links'][0][side] = '不存在的章节'
                self.assert_rejected(data, message='exist exactly once')
        data = copy.deepcopy(self.data)
        data['sections'].append(copy.deepcopy(data['sections'][0]))
        self.assert_rejected(data, message='Explanation heading must exist exactly once')
        self.original += '\r\n## 1 调研背景与目标\r\n同名章节。\r\n'.encode('utf-8')
        self.report.write_bytes(self.original)
        self.assert_rejected(message='Source report heading must exist exactly once')

    def test_heading_parser_ignores_fences_and_preserves_full_titles(self):
        headings = report_headings('# 标题\n## 1 调研背景与目标 ###\n```md\n## 1 调研背景与目标\n```\n'
                                   '~~~\n## 忽略\n~~~\n旧式标题\n=====\n')
        self.assertEqual(headings, ['标题', '1 调研背景与目标', '旧式标题'])
        self.original += b'\r\n```md\r\n## 1 ' + '调研背景与目标'.encode('utf-8') + b'\r\n```\r\n'
        self.report.write_bytes(self.original)
        build(self.data, self.output, self.base)
        self.assert_source_unchanged()

    def test_bad_table_and_missing_image_fail_without_changing_existing_output(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b'previous valid explanation\n')
        for rows in ([], [['A', 'B'], ['one']], ['not a row'], [[]], [['A'], [object()]]):
            with self.subTest(rows=rows):
                data = copy.deepcopy(self.data)
                data['sections'][0]['tables'] = [{'rows': rows}]
                self.assert_rejected(data)
        data = copy.deepcopy(self.data)
        data['sections'][0]['images'] = [{'path': 'missing.png', 'caption': '图像'}]
        self.assert_rejected(data, message='Image missing')

    def test_valid_table_image_and_all_math_surfaces_are_normalized(self):
        image = self.base / '图像.png'
        image.write_bytes(b'image placeholder for file-existence validation')
        data = copy.deepcopy(self.data)
        data['title'] += r' \(x\)'
        data['introduction'] += r' \[x^2\]'
        section = data['sections'][0]
        section['paragraphs'] = [r'段落 \(a\)']
        section['items'] = [r'条目 \(b\)']
        section['tables'] = [{'caption': r'表 \(c\)', 'rows': [[r'列 \(d\)', '值'],
                              [r'\(|x|+\|y\|\)', 'pipe | two\nlines'], ['数字', 1.5]]}]
        section['images'] = [{'path': image.name, 'caption': r'图 \(\mathbf{x}\)'}]
        faq = data['faq'][0]
        faq['question'] += r' \(q\)'
        faq['short_answer'] += r' \(s\)'
        faq['evidence'] = [r'依据 \(e\)']
        faq['limitations'] += r' \(l\)'
        data['sources'] = [r'文献 \(z\)', {'text': r'文献 \(w\)'}]
        result = build(data, self.output, self.base)
        text = self.output.read_text(encoding='utf-8')
        self.assertEqual(result['validated_math_expressions'], 15)
        self.assertIn('| --- | --- |', text)
        self.assertIn(r'pipe \| two<br>lines', text)
        self.assertIn(r'$\vert x\vert +\Vert y\Vert$', text)
        self.assertIn('$$\nx^2\n$$', text)
        self.assertIn('![图像]', text)
        self.assertIn('图 $\\mathbf{x}$', text)
        self.assertNotIn(r'\(', text)
        self.assertNotIn(r'\[', text)
        self.assert_source_unchanged()

    def test_invalid_math_on_every_visible_surface_is_rejected(self):
        bad = r'\(\notarealcommand{x}\)'
        image = self.base / 'exists.png'
        image.write_bytes(b'image')
        mutations = [
            lambda d: d.update(title=bad),
            lambda d: d.update(introduction=bad),
            lambda d: d['sections'][0].update(title=bad),
            lambda d: d['sections'][0].update(paragraphs=[bad]),
            lambda d: d['sections'][0].update(items=[bad]),
            lambda d: d['sections'][0].update(tables=[{'caption': bad, 'rows': [['A']]}]),
            lambda d: d['sections'][0].update(tables=[{'rows': [[bad]]}]),
            lambda d: d['sections'][0].update(images=[{'path': image.name, 'caption': bad}]),
            lambda d: d['faq'][0].update(question=bad),
            lambda d: d['faq'][0].update(short_answer=bad),
            lambda d: d['faq'][0].update(explanation=[bad]),
            lambda d: d['faq'][0].update(evidence=[bad]),
            lambda d: d['faq'][0].update(limitations=bad),
            lambda d: d.update(sources=[bad]),
            lambda d: d.update(sources=[{'text': bad}]),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(surface=index):
                data = copy.deepcopy(self.data)
                # A changed explanation title need not be checked by an old mapping.
                data.pop('section_links')
                mutation(data)
                self.assert_rejected(data, message='Invalid or unsupported strict LaTeX')
        for bad in (r'Unclosed \(x', 'Unclosed $x', r'Unclosed \[x'):
            with self.subTest(delimiter=bad):
                data = copy.deepcopy(self.data)
                data['faq'][0]['short_answer'] = bad
                self.assert_rejected(data, message='Unbalanced')

    def test_long_explanation_has_no_weekly_report_length_limit(self):
        data = copy.deepcopy(self.data)
        data['faq'][0]['explanation'].append('详细说明。' * 2000)
        result = build(data, self.output, self.base)
        self.assertGreater(result['body_characters'], 2500)
        self.assertNotIn('length_in_default_range', result)
        self.assertNotIn('warnings', result)
        self.assert_source_unchanged()

    def test_delimiter_padding_is_removed_for_standard_markdown_math(self):
        self.data['faq'][0]['explanation'] = [r'行内 \( x + y \) 和独立公式 \[ \frac{1}{2} \]。']
        result = build(self.data, self.output, self.base)
        text = self.output.read_text(encoding='utf-8')
        self.assertEqual(result['validated_math_expressions'], 2)
        self.assertIn('$x + y$', text)
        self.assertIn('$$\n\\frac{1}{2}\n$$', text)
        self.assertNotIn('$ x + y $', text)
        self.assert_source_unchanged()

    def test_limitations_are_optional_and_blank_limits_are_not_rendered(self):
        for limitation in (None, '', '   '):
            with self.subTest(limitation=limitation):
                data = copy.deepcopy(self.data)
                if limitation is None:
                    data['faq'][0].pop('limitations')
                else:
                    data['faq'][0]['limitations'] = limitation
                build(data, self.output, self.base)
                self.assertNotIn('**边界与限制：**', self.output.read_text(encoding='utf-8'))
                self.assert_source_unchanged()

    def test_display_formulas_are_separate_blocks_in_body_and_faq_paragraphs(self):
        self.data['sections'][0]['paragraphs'] = [r'公式之前。\[x^2+y^2\]公式之后，行内 \(z\) 保持行内。']
        self.data['faq'][0]['short_answer'] = r'短答之前。\[a=b\]短答之后。'
        self.data['faq'][0]['explanation'] = [r'讲解之前。\[\frac{1}{2}\]讲解之后。']
        self.data['sections'][0]['tables'] = [{'rows': [[r'\[t\]', '表格']]}]
        result = build(self.data, self.output, self.base)
        text = self.output.read_text(encoding='utf-8')
        self.assertIn('公式之前。\n\n$$\nx^2+y^2\n$$\n\n公式之后，行内 $z$ 保持行内。', text)
        self.assertIn('**短答：** 短答之前。\n\n$$\na=b\n$$\n\n短答之后。', text)
        self.assertIn('讲解之前。\n\n$$\n\\frac{1}{2}\n$$\n\n讲解之后。', text)
        self.assertIn('| $$t$$ | 表格 |', text)
        self.assertEqual(result['validated_math_expressions'], 5)
        self.assert_source_unchanged()

    def test_table_formula_newlines_do_not_become_html_inside_latex(self):
        formula = r'''\[\begin{aligned}
x &= y \\
z &= w
\end{aligned}\]'''
        self.data['sections'][0]['tables'] = [{'rows': [['说明', '公式'], ['两行公式', formula]]}]
        result = build(self.data, self.output, self.base)
        text = self.output.read_text(encoding='utf-8')
        self.assertEqual(result['validated_math_expressions'], 2)
        self.assertIn('$$\\begin{aligned} x &= y \\\\ z &= w \\end{aligned}$$', text)
        self.assertNotIn('<br>', text)
        self.assert_source_unchanged()

    def test_cli_resolves_report_relative_to_json_and_returns_json(self):
        payload = self.base / '讲解输入.json'
        payload.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8-sig')
        script = Path(__file__).resolve().parents[1] / 'export_report_explanation.py'
        result = subprocess.run([sys.executable, str(script), '--input', str(payload), '--output', str(self.output)],
                                cwd=script.parent, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual(metadata['path'], str(self.output.resolve()))
        self.assertEqual(metadata['faq'], 1)
        self.assert_source_unchanged()

    def test_cli_content_error_returns_failure_before_writing_output(self):
        self.data['section_links'][0]['report_heading'] = '不存在的章节'
        payload = self.base / '错误讲解输入.json'
        payload.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        script = Path(__file__).resolve().parents[1] / 'export_report_explanation.py'
        environment = os.environ.copy()
        environment.pop('PYTHONIOENCODING', None)
        result = subprocess.run([sys.executable, str(script), '--input', str(payload), '--output', str(self.output)],
                                cwd=script.parent, env=environment, capture_output=True, text=True,
                                encoding='utf-8', timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('不存在的章节', result.stderr)
        self.assertFalse(self.output.exists())
        self.assert_source_unchanged()


if __name__ == '__main__':
    unittest.main()
