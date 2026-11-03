#!/usr/bin/env python3
"""Return bounded structure/counts, or one requested excerpt; never dump a paper by default."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from deploy_reader import load_paper_data


def inspect(path, section_id=None, block_id=None, max_chars=4000):
    data = load_paper_data(path)
    views = [(name, data.get(name, {}).get('sections', []))
             for name in ('overview', 'explanation')]
    views.append(('translation', data.get('sections', [])))
    if section_id or block_id:
        matches = []
        for view, sections in views:
            for section in sections:
                if section_id and section.get('id') != section_id:
                    continue
                if block_id:
                    matches.extend(b for b in section.get('blocks', []) if b.get('id') == block_id)
                else:
                    matches.append(section)
        if len(matches) != 1:
            raise ValueError('Requested ID must match exactly one section or top-level block')
        encoded = json.dumps(matches[0], ensure_ascii=False)
        return {'id': block_id or section_id, 'truncated': len(encoded) > max_chars,
                'excerpt': encoded[:max_chars]}
    result = {'id': data.get('id'), 'content_status': data.get('content_status'),
              'framework': data.get('learning_requirements', {}).get('overview_framework'),
              'views': {}}
    missing = []
    for view, sections in views:
        blocks = [b for s in sections for b in s.get('blocks', [])]
        for b in blocks:
            if b.get('type') in ('figure', 'diagram') and b.get('src'):
                src = str(b['src'])
                if not re.match(r'^[a-z]+://', src) and not (path.parent / src).is_file():
                    missing.append({'block_id': b.get('id'), 'src': src})
        result['views'][view] = {'sections': len(sections),
            'blocks_by_type': dict(Counter(b.get('type', '?') for b in blocks)),
            'prose_cjk_characters': len(re.findall(r'[\u4e00-\u9fff]', ''.join(
                b.get('text_zh', '') for b in blocks if b.get('type') in ('paragraph', 'para')))),
            'section_ids': [s.get('id') for s in sections[:40]],
            'section_ids_truncated': len(sections) > 40}
    result['missing_figure_assets'] = missing[:20]
    result['missing_figure_asset_count'] = len(missing)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--section-id')
    parser.add_argument('--block-id')
    parser.add_argument('--max-chars', type=int, default=4000)
    args = parser.parse_args()
    if not 200 <= args.max_chars <= 12000:
        parser.error('--max-chars must be between 200 and 12000')
    try:
        result = inspect(args.path, args.section_id, args.block_id, args.max_chars)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        print(json.dumps({'error': str(exc)[:500]}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
