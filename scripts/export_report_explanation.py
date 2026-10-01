#!/usr/bin/env python3
"""Export a personal report explanation as Markdown without editing its report."""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote

from math_validation import MATH, markdown_math, validate_math


REQUIRED_TOPICS = ('goal', 'challenges', 'directions', 'methods', 'metrics', 'evaluation')
TOPIC_LABELS = {
    'goal': '研究目标', 'challenges': '共同困难', 'directions': '整体方向',
    'methods': '方法', 'metrics': '最终指标', 'evaluation': '效果评定', 'other': '其他',
}


def _markdown_text(value):
    # Inline Markdown math needs non-whitespace next to both dollar delimiters.
    # Removing delimiter padding preserves the LaTeX expression's meaning.
    def unpadded(match):
        expression = next(part for part in match.groups() if part is not None).strip()
        display = match.group(2) is not None or match.group(3) is not None
        return (r'\[' + expression + r'\]') if display else (r'\(' + expression + r'\)')
    return markdown_math(MATH.sub(unpadded, str(value)))


def _paragraph_text(value):
    def display_block(match):
        expression = match.group(3)
        if expression is not None:
            return '\n\n$$\n' + expression + '\n$$\n\n'
        return match[0]
    return MATH.sub(display_block, _markdown_text(value)).strip()


def _text(value, name, required=False, single_line=False):
    if not isinstance(value, str):
        raise ValueError(f'{name} must be a string.')
    if required and not value.strip():
        raise ValueError(f'{name} must not be empty.')
    if single_line and any(c in value for c in '\r\n'):
        raise ValueError(f'{name} must be a single line.')
    return value


def _list(value, name):
    if not isinstance(value, list):
        raise ValueError(f'{name} must be a list.')
    return value


def _texts(value, name, required=False):
    values = _list(value, name)
    if required and not values:
        raise ValueError(f'{name} must not be empty.')
    return [_text(item, name, required=required) for item in values]


def _cell(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Table cells must be finite numbers or strings.')
        return str(value)
    raise ValueError('Table cells must be finite numbers or strings.')


def _table_cell(value):
    """Escape Markdown table pipes, preserving the meaning of LaTeX vertical bars."""
    normalized = _markdown_text(_cell(value))
    parts = []
    cursor = 0
    for match in MATH.finditer(normalized):
        parts.append(normalized[cursor:match.start()].replace('|', r'\|'))
        expression = next(part for part in match.groups() if part is not None)
        # Markdown table escaping would turn | into the different TeX symbol \|.
        # Explicit TeX commands keep single and double bars distinct.
        expression = re.sub(r'\\\||\|', lambda m: r'\Vert ' if m[0] == r'\|' else r'\vert ', expression)
        # LaTeX treats source newlines as spaces; table prose uses HTML line breaks.
        expression = expression.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
        delimiter = '$$' if match.group(3) is not None else '$'
        parts.append(delimiter + expression.rstrip() + delimiter)
        cursor = match.end()
    parts.append(normalized[cursor:].replace('|', r'\|'))
    return ''.join(parts).replace('\r\n', '<br>').replace('\r', '<br>').replace('\n', '<br>')


def report_headings(text):
    """Read full Markdown heading text, ignoring fenced code examples."""
    headings = []
    fence = None
    previous = ''
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            previous = ''
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            previous = ''
            continue
        heading = re.match(r'^ {0,3}#{1,6}(?:[ \t]+|$)(.*)$', line)
        if heading:
            value = re.sub(r'[ \t]+#+[ \t]*$', '', heading[1]).strip()
            if value:
                headings.append(value)
            previous = ''
        elif re.match(r'^ {0,3}(?:=+|-+)[ \t]*$', line) and previous.strip():
            headings.append(previous.strip())
            previous = ''
        else:
            previous = line if line.strip() and not line.startswith(('    ', '\t')) else ''
    return headings


def _reject_report_output(output, report):
    if output.resolve() == report.resolve():
        raise ValueError('Explanation output must not overwrite the source report or a path alias.')
    if output.exists() and os.path.samefile(output, report):
        raise ValueError('Explanation output must not overwrite the source report or a path alias.')


def _report_link(report, output):
    try:
        destination = Path(os.path.relpath(report, output.parent)).as_posix()
    except ValueError:  # Files on different Windows drives have no relative path.
        destination = report.as_posix()
    destination = quote(destination, safe='/.:')
    label = _markdown_text(report.name).replace('[', r'\[').replace(']', r'\]')
    return f'[{label}](<{destination}>)'


def _validate(data, source_text, base):
    if not isinstance(data, dict):
        raise ValueError('Explanation input must be a JSON object.')
    _text(data.get('title'), 'title', required=True, single_line=True)
    _text(data.get('introduction', ''), 'introduction')
    sections = _list(data.get('sections', []), 'sections')
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError('Each section must be an object.')
        _text(section.get('title'), 'section.title', required=True, single_line=True)
        _texts(section.get('paragraphs', []), 'section.paragraphs')
        _texts(section.get('items', []), 'section.items')
        for table in _list(section.get('tables', []), 'section.tables'):
            if not isinstance(table, dict):
                raise ValueError('Each table must be an object.')
            _text(table.get('caption', ''), 'table.caption')
            rows = _list(table.get('rows'), 'table.rows')
            if not rows or not isinstance(rows[0], list) or not rows[0]:
                raise ValueError('Table rows must have the same nonzero column count.')
            if any(not isinstance(row, list) or len(row) != len(rows[0]) for row in rows):
                raise ValueError('Table rows must have the same nonzero column count.')
            for row in rows:
                for cell in row:
                    _cell(cell)
        for image in _list(section.get('images', []), 'section.images'):
            if not isinstance(image, dict):
                raise ValueError('Each image must be an object.')
            value = _text(image.get('path'), 'image.path', required=True)
            _text(image.get('caption', ''), 'image.caption')
            path = Path(value)
            path = (Path(base) / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_file():
                raise ValueError(f'Image missing: {path}')
            if any(c in str(path) for c in '<>\r\n'):
                raise ValueError('Image path cannot contain Markdown destination delimiters.')

    source_counts = Counter(report_headings(source_text))
    explanation_counts = Counter(section['title'].strip() for section in sections)
    for link in _list(data.get('section_links', []), 'section_links'):
        if not isinstance(link, dict):
            raise ValueError('Each section link must be an object.')
        report_title = _text(link.get('report_heading'), 'section_link.report_heading', required=True, single_line=True).strip()
        explanation_title = _text(link.get('explanation_heading'), 'section_link.explanation_heading', required=True, single_line=True).strip()
        if source_counts[report_title] != 1:
            raise ValueError(f'Source report heading must exist exactly once: {report_title}')
        if explanation_counts[explanation_title] != 1:
            raise ValueError(f'Explanation heading must exist exactly once: {explanation_title}')

    faq = _list(data.get('faq'), 'faq')
    if not faq:
        raise ValueError('Explanation needs a nonempty FAQ.')
    covered = set()
    for entry in faq:
        if not isinstance(entry, dict):
            raise ValueError('Each FAQ entry must be an object.')
        topics = _texts(entry.get('topics'), 'faq.topics', required=True)
        invalid = set(topics) - TOPIC_LABELS.keys()
        if invalid:
            raise ValueError('Unknown FAQ topics: ' + ', '.join(sorted(invalid)))
        covered.update(topics)
        _text(entry.get('question'), 'faq.question', required=True, single_line=True)
        _text(entry.get('short_answer'), 'faq.short_answer', required=True)
        _texts(entry.get('explanation'), 'faq.explanation', required=True)
        _texts(entry.get('evidence', []), 'faq.evidence', required=False)
        _text(entry.get('limitations', ''), 'faq.limitations')
    # Topic tags classify questions. The prose may already cover other topics;
    # content coverage is reviewed by the writer, not inferred from FAQ labels.
    for source in _list(data.get('sources', []), 'sources'):
        if isinstance(source, dict):
            _text(source.get('text'), 'source.text', required=True)
        else:
            _text(source, 'source', required=True)
    return covered


def body_texts(data):
    parts = [data.get('introduction', '')]
    for section in data.get('sections', []):
        parts.extend(section.get('paragraphs', []))
        parts.extend(section.get('items', []))
        for table in section.get('tables', []):
            parts.append(table.get('caption', ''))
            parts.extend(_cell(cell) for row in table['rows'] for cell in row)
        parts.extend(image.get('caption', '') for image in section.get('images', []))
    for entry in data['faq']:
        parts.extend([entry['question'], entry['short_answer'], entry.get('limitations', '')])
        parts.extend(entry['explanation'])
        parts.extend(entry.get('evidence', []))
    return parts


def _render(data, output, report, base):
    visible = [data['title'], report.name]
    lines = ['# ' + _markdown_text(data['title']), '', '个人学习与汇报准备', '',
             '对应报告：' + _report_link(report, output), '']

    def paragraph(value):
        visible.append(value)
        lines.extend([_paragraph_text(value), ''])

    if data.get('introduction'):
        paragraph(data['introduction'])
    if data.get('section_links'):
        lines.extend(['## 章节对应', '', '| 提交版报告章节 | 本说明章节 |', '| --- | --- |'])
        for link in data['section_links']:
            cells = [_table_cell(link[key]) for key in ('report_heading', 'explanation_heading')]
            visible.extend(cells)
            lines.append('| ' + ' | '.join(cells) + ' |')
        lines.append('')
    for section in data.get('sections', []):
        visible.append(section['title'])
        lines.extend(['## ' + _markdown_text(section['title']), ''])
        for value in section.get('paragraphs', []):
            paragraph(value)
        for value in section.get('items', []):
            visible.append(value)
            lines.append('- ' + _markdown_text(value))
        if section.get('items'):
            lines.append('')
        for table in section.get('tables', []):
            if table.get('caption'):
                paragraph(table['caption'])
            for index, row in enumerate(table['rows']):
                cells = [_table_cell(cell) for cell in row]
                visible.extend(cells)
                lines.append('| ' + ' | '.join(cells) + ' |')
                if index == 0:
                    lines.append('| ' + ' | '.join('---' for _ in row) + ' |')
            lines.append('')
        for image in section.get('images', []):
            path = Path(image['path'])
            path = (Path(base) / path).resolve() if not path.is_absolute() else path.resolve()
            # Keep mathematical captions in ordinary Markdown, where they render as math.
            lines.extend([f'![图像](<{path.as_posix()}>)', ''])
            if image.get('caption'):
                paragraph(image['caption'])
    lines.extend(['## 问答详解', ''])
    for index, entry in enumerate(data['faq'], 1):
        visible.extend([entry['question'], entry['short_answer'], entry.get('limitations', '')])
        labels = [TOPIC_LABELS[topic] for topic in dict.fromkeys(entry['topics'])]
        lines.extend([f"### {index}. {_markdown_text(entry['question'])}", '',
                      '**涉及主题：** ' + '、'.join(labels), '',
                      '**短答：** ' + _paragraph_text(entry['short_answer']), '', '**详细讲解：**', ''])
        for value in entry['explanation']:
            paragraph(value)
        if entry.get('evidence'):
            lines.extend(['**报告依据：**', ''])
            for value in entry['evidence']:
                visible.append(value)
                lines.append('- ' + _markdown_text(value))
            lines.append('')
        if entry.get('limitations', '').strip():
            lines.extend(['**边界与限制：** ' + _paragraph_text(entry['limitations']), ''])
    if data.get('sources'):
        lines.extend(['## 参考文献', ''])
        for index, source in enumerate(data['sources'], 1):
            value = source['text'] if isinstance(source, dict) else source
            visible.append(value)
            lines.extend([f'[{index}] ' + _markdown_text(value), ''])
    return '\n'.join(lines).rstrip() + '\n', visible


def build(data, output, base):
    """Validate the complete explanation before writing one independent .md file."""
    if not isinstance(data, dict):
        raise ValueError('Explanation input must be a JSON object.')
    output = Path(os.path.abspath(output))
    if output.suffix.lower() != '.md':
        raise ValueError('Report explanation output must be a .md file.')
    report_value = _text(data.get('report_path'), 'report_path', required=True)
    report = Path(report_value)
    report = Path(os.path.abspath(Path(base) / report if not report.is_absolute() else report))
    if report.suffix.lower() != '.md' or not report.is_file():
        raise ValueError(f'Source report must be an existing Markdown file: {report}')
    _reject_report_output(output, report)
    report_bytes = report.read_bytes()
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    if 'report_sha256' in data:
        expected = _text(data['report_sha256'], 'report_sha256', required=True)
        if not re.fullmatch(r'[0-9a-fA-F]{64}', expected) or expected.lower() != report_sha256:
            raise ValueError('report_sha256 does not match the source report.')
    covered = _validate(data, report_bytes.decode('utf-8-sig'), base)
    rendered, visible = _render(data, output, report, base)
    math_count = validate_math([_markdown_text(value) for value in visible])
    count = len(re.sub(r'\s+', '', ''.join(body_texts(data))))
    # Recheck the alias immediately before the only filesystem mutation.
    _reject_report_output(output, report)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.' + output.stem + '-', suffix='.md', dir=output.parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(rendered)
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return {'path': str(output.resolve()), 'format': 'md', 'report_sha256': report_sha256,
            'body_characters': count, 'sections': len(data.get('sections', [])), 'faq': len(data['faq']),
            'covered_topics': [topic for topic in TOPIC_LABELS if topic in covered],
            'validated_math_expressions': math_count}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(args.input)
    result = build(json.loads(source.read_text(encoding='utf-8-sig')), args.output, source.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
