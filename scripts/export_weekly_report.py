#!/usr/bin/env python3
"""Export evidence-based research/work weekly reports as Markdown only."""
import argparse
import json
from pathlib import Path
import re
from math_validation import markdown_math, validate_math


def body_texts(data):
    parts = [str(data.get('introduction', ''))]
    for section in data.get('sections', []):
        parts.extend(str(p) for p in section.get('paragraphs', []))
        parts.extend(str(p) for p in section.get('items', []))
        for table in section.get('tables', []):
            parts.append(str(table.get('caption', '')))
            parts.extend(str(cell) for row in table.get('rows', []) for cell in row)
        parts.extend(str(image.get('caption', '')) for image in section.get('images', []))
    return parts


def counted_text(data):
    return len(re.sub(r'\s+', '', ''.join(body_texts(data))))


def validate(data):
    if data.get('type') not in ('research', 'work'):
        raise ValueError('type must be research or work.')
    if 'include_sources' in data and not isinstance(data['include_sources'], bool):
        raise ValueError('include_sources must be a boolean.')
    if not str(data.get('title', '')).strip() or not isinstance(data.get('sections'), list) or not data['sections']:
        raise ValueError('Report needs a title and nonempty sections.')
    has_visual = False
    for section in data['sections']:
        if not str(section.get('title', '')).strip():
            raise ValueError('Each section needs a title.')
        has_visual = has_visual or bool(section.get('tables') or section.get('images'))
        has_visual = has_visual or any(
            re.search(r'```mermaid[ \t]*\r?\n.+?\r?\n```', str(p), re.S)
            for p in section.get('paragraphs', []))
        for table in section.get('tables', []):
            rows = table.get('rows', [])
            if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
                raise ValueError('Table rows must have the same nonzero column count.')
    if not has_visual:
        raise ValueError('Weekly report needs a useful figure, Mermaid diagram, or comparison table.')
    return counted_text(data)


def build(data, output, base, strict_length=False):
    count = validate(data)
    output = Path(output)
    if output.suffix.lower() != '.md':
        raise ValueError('Weekly report output must be a .md file.')
    if strict_length and not 1500 <= count <= 2500:
        raise ValueError(f'Body/table characters {count} outside default 1500–2500 range.')
    all_text = body_texts(data) + [data['title'], data.get('subtitle', ''), data.get('name', '')]
    all_text += [s['title'] for s in data['sections']]
    all_text += [s if isinstance(s, str) else s.get('text', '') for s in data.get('sources', [])]
    math_count = validate_math(all_text)
    lines = ['# ' + markdown_math(data['title']), '']
    meta = []
    if data.get('name'):
        meta.append(str(data['name']))
    if data.get('period_start') or data.get('period_end'):
        meta.append(f"{data.get('period_start', '')} 至 {data.get('period_end', '')}")
    if data.get('subtitle'):
        meta.append(str(data['subtitle']))
    if meta:
        lines += ['  ·  '.join(markdown_math(m) for m in meta), '']
    if data.get('introduction'):
        lines += [markdown_math(data['introduction']), '']
    for i, section in enumerate(data['sections'], 1):
        lines += [f"## {i} {markdown_math(section['title'])}", '']
        for paragraph in section.get('paragraphs', []):
            lines += [markdown_math(paragraph), '']
        for item in section.get('items', []):
            lines.append('- ' + markdown_math(item))
        if section.get('items'):
            lines.append('')
        for table in section.get('tables', []):
            if table.get('caption'):
                lines += [markdown_math(table['caption']), '']
            for ri, row in enumerate(table['rows']):
                cells = [markdown_math(cell).replace('|', r'\|').replace('\r\n', '<br>').replace('\n', '<br>') for cell in row]
                lines.append('| ' + ' | '.join(cells) + ' |')
                if ri == 0:
                    lines.append('| ' + ' | '.join('---' for _ in row) + ' |')
            lines.append('')
        for image in section.get('images', []):
            path = Path(image['path'])
            path = (Path(base) / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_file():
                raise ValueError(f'Image missing: {path}')
            if any(c in str(path) for c in '<>\n\r'):
                raise ValueError('Image path cannot contain Markdown destination delimiters.')
            caption = markdown_math(image.get('caption', '')).replace(']', r'\]')
            lines += [f'![{caption}](<{path.as_posix()}>)', '']
    if data.get('include_sources', False) and data.get('sources'):
        lines += ['## ' + ('参考文献' if data['type'] == 'research' else '证据来源'), '']
        for i, source in enumerate(data['sources'], 1):
            value = source if isinstance(source, str) else source.get('text', '')
            lines.extend([f'[{i}] ' + markdown_math(value), ''])
        lines.append('')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    return {'path': str(output.resolve()), 'format': 'md', 'type': data['type'], 'body_characters': count,
            'sections': len(data['sections']), 'tables': sum(len(s.get('tables', [])) for s in data['sections']),
            'validated_math_expressions': math_count, 'length_in_default_range': 1500 <= count <= 2500,
            'warnings': [] if 1500 <= count <= 2500 else ['Default target is 1500–2500 body/table characters.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--strict-length', action='store_true')
    args = parser.parse_args()
    src = Path(args.input)
    result = build(json.loads(src.read_text(encoding='utf-8-sig')), args.output, src.parent, args.strict_length)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
