#!/usr/bin/env python3
"""Read only explicitly supplied sources; mtime is a filter, never evidence of completed work."""
import argparse
import datetime as dt
import json
from pathlib import Path

TEXT = {'.md', '.txt', '.log', '.py', '.json', '.csv', '.tsv', '.js', '.cpp', '.h', '.yaml', '.yml'}
SKIP = {'.git', '.reader', 'node_modules', '__pycache__', '.venv'}


def collect(paths, since=None, until=None, limit=5000):
    hk = dt.timezone(dt.timedelta(hours=8))
    lo = dt.datetime.fromisoformat(since).replace(tzinfo=hk).timestamp() if since else float('-inf')
    hi = (dt.datetime.fromisoformat(until).replace(tzinfo=hk) + dt.timedelta(days=1)).timestamp() if until else float('inf')
    records = []
    seen = set()
    for value in paths:
        path = Path(value).resolve(strict=True)
        if path.is_dir() and path == Path(path.anchor):
            raise ValueError('Scanning a filesystem root is not supported; supply a specific directory.')
        candidates = [path] if path.is_file() else sorted(path.rglob('*'))
        for file in candidates:
            if not file.is_file() or file.is_symlink() or any(p in SKIP for p in file.relative_to(path.parent if path.is_file() else path).parts):
                continue
            if file.resolve() in seen:
                continue
            seen.add(file.resolve())
            stat = file.stat()
            if not lo <= stat.st_mtime < hi:
                continue
            record = {'path': str(file), 'modified_at': dt.datetime.fromtimestamp(stat.st_mtime, hk).isoformat(), 'size': stat.st_size}
            if file.suffix.lower() in TEXT:
                raw = file.read_bytes()
                try:
                    content = raw.decode('utf-8-sig')
                except UnicodeDecodeError:
                    record['read_status'] = 'encoding_requires_review'
                else:
                    record.update(text=content[:limit], truncated=len(content) > limit, read_status='text')
            else:
                record['read_status'] = 'binary_requires_separate_read'
            records.append(record)
    return {'sources': records, 'note': 'File timestamps only filter candidate sources. Report claims need content/experiment evidence.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--since')
    parser.add_argument('--until')
    parser.add_argument('--max-chars', type=int, default=5000)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    data = collect(args.paths, args.since, args.until, args.max_chars)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(data["sources"])} sources → {out}')


if __name__ == '__main__':
    main()
