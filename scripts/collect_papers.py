#!/usr/bin/env python3
"""Persist host-verified search results and import papers without overwriting reading assets."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import urllib.request


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def library_lock(vault, timeout=15):
    lock = vault / '.collection.lock'
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError('Another library update is active; retry after it completes.')
            time.sleep(.1)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def normalize_doi(value):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value or '').strip(), flags=re.I).lower()


def normalize_arxiv(value):
    value = re.sub(r'^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/', '', str(value or '').strip(), flags=re.I)
    value = re.sub(r'\.pdf$', '', value, flags=re.I)
    return re.sub(r'v\d+$', '', value).lower()


def normalized_title(value):
    return ''.join(c.lower() for c in str(value or '') if c.isalnum())


def duplicate(old, new):
    if normalize_doi(old.get('doi')) and normalize_doi(old.get('doi')) == normalize_doi(new.get('doi')):
        return True
    if normalize_arxiv(old.get('arxiv_id')) and normalize_arxiv(old.get('arxiv_id')) == normalize_arxiv(new.get('arxiv_id')):
        return True
    if normalized_title(old.get('title')) != normalized_title(new.get('title')):
        return False
    if old.get('year') and new.get('year') and str(old['year']) != str(new['year']):
        return False
    a, b = old.get('authors', []), new.get('authors', [])
    if a and b and normalized_title(a[0] if isinstance(a, list) else a) != normalized_title(b[0] if isinstance(b, list) else b):
        return False
    return bool(normalized_title(new.get('title')))


def pdf_audit(path, expected):
    with open(path, 'rb') as f:
        if f.read(5) != b'%PDF-':
            raise ValueError('Downloaded response is not a PDF.')
    try:
        import fitz
        with fitz.open(path) as doc:
            count = len(doc)
            text = doc[0].get_text()
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError('PDF auditing needs PyMuPDF or pypdf in the selected Python runtime.') from exc
        reader = PdfReader(path)
        count = len(reader.pages)
        text = reader.pages[0].extract_text() or ''
    if count < 1:
        raise ValueError('PDF has no pages.')
    words = [w.lower() for w in re.findall(r'[A-Za-z0-9]+', expected.get('title', '')) if len(w) > 2]
    normalized = text.lower().replace('\n', ' ')
    ratio = sum(w in normalized for w in words) / max(1, len(words))
    audit = {'pdf_pages': count, 'first_page_text': text[:1500], 'checked_at': now(),
             'identity_status': 'matched_title' if words and ratio >= .75 else 'needs_visual_check'}
    if text.strip() and words and ratio < .35:
        raise ValueError('PDF first-page title does not match the requested paper.')
    return audit


def import_pdf(paper, folder):
    target = folder / 'source.pdf'
    if target.exists():
        return {'state': 'existing', **pdf_audit(target, paper)}
    errors = []
    local = paper.get('local_pdf')
    urls = paper.get('pdf_urls', [])
    if paper.get('pdf_url'):
        urls = [paper['pdf_url'], *urls]
    candidates = [('local', local)] if local else []
    candidates += [('url', url) for url in dict.fromkeys(urls)]
    if not candidates:
        return {'state': 'unavailable', 'reason': 'No full-text PDF source was supplied.'}
    for kind, candidate in candidates:
        fd, tmp = tempfile.mkstemp(prefix='source-', suffix='.part', dir=folder)
        os.close(fd)
        try:
            if kind == 'local':
                source = Path(candidate).resolve(strict=True)
                if not source.is_file():
                    raise ValueError('Local PDF source is not a file.')
                shutil.copyfile(source, tmp)
            else:
                if not re.match(r'^https?://', str(candidate), re.I):
                    raise ValueError('PDF URL must be HTTP(S).')
                request = urllib.request.Request(candidate, headers={'User-Agent': 'AcademicReadingMaster/3'})
                with urllib.request.urlopen(request, timeout=45) as response, open(tmp, 'wb') as out:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 100 * 1024 * 1024:
                            raise ValueError('PDF exceeds the 100 MiB import limit.')
                        out.write(chunk)
            audit = pdf_audit(tmp, paper)
            os.replace(tmp, target)
            return {'state': 'downloaded' if kind == 'url' else 'imported', 'source': str(candidate), **audit}
        except Exception as exc:
            errors.append({'source': str(candidate), 'reason': str(exc)})
        finally:
            Path(tmp).unlink(missing_ok=True)
    return {'state': 'unavailable', 'reason': 'All supplied PDF sources failed verification.', 'errors': errors}


def collect(vault, manifest, download=True):
    vault = Path(vault).resolve()
    vault.mkdir(parents=True, exist_ok=True)
    if not isinstance(manifest.get('papers'), list):
        raise ValueError('Manifest must contain a papers array.')
    # Validate the whole manifest before making partial library changes.
    for paper in manifest['papers']:
        if not isinstance(paper, dict) or not str(paper.get('title', '')).strip():
            raise ValueError('Each paper needs a nonempty title.')
        if paper.get('id') and not re.fullmatch(r'[A-Za-z0-9_-]+', paper['id']):
            raise ValueError('Unsafe paper ID.')
    log = {'query': manifest.get('query', ''), 'constraints': manifest.get('constraints', {}),
           'searched_at': manifest.get('searched_at', now()), 'sources': manifest.get('sources', []), 'papers': []}
    with library_lock(vault):
        libfile = vault / 'library.json'
        lib = json.loads(libfile.read_text(encoding='utf-8-sig')) if libfile.exists() else {
            'version': '1.0.0', 'created_at': now(), 'level': 'novice', 'papers': []}
        for supplied in manifest['papers']:
            info = dict(supplied)
            info['doi'] = normalize_doi(info.get('doi'))
            if info.get('arxiv_id'):
                info['arxiv_id'] = normalize_arxiv(info['arxiv_id'])
            existing = next((p for p in lib['papers'] if duplicate(p, info)), None)
            if existing is None:
                slug = re.sub(r'[^a-z0-9]+', '_', info['title'].lower()).strip('_')[:64] or hashlib.sha256(info['title'].encode()).hexdigest()[:12]
                paper_id = info.get('id') or f"{info.get('year', 'undated')}_{slug}"
                if any(p['id'] == paper_id for p in lib['papers']):
                    raise ValueError('Paper ID collision; supply a distinct ID after checking the paper identity.')
                item = {**info, 'id': paper_id, 'created_at': now(), 'updated_at': now(), 'status': 'unread'}
                lib['papers'].append(item)
            else:
                item = existing
                # Reading status/content and existing metadata remain authoritative.
                item.setdefault('related_versions', [])
                version = {k: info[k] for k in ('doi', 'arxiv_id', 'venue', 'year', 'source_url', 'pdf_url') if info.get(k)}
                if version and version not in item['related_versions']:
                    item['related_versions'].append(version)
                for key in ('doi', 'arxiv_id', 'title_cn', 'authors', 'venue', 'year'):
                    if not item.get(key) and info.get(key):
                        item[key] = info[key]
                item['updated_at'] = now()
            folder = vault / 'papers' / item['id']
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'assets').mkdir(exist_ok=True)
            audit = import_pdf({**item, **info}, folder) if download else {'state': 'not_requested'}
            item['pdf_status'] = audit
            if audit.get('pdf_pages'):
                item['pdf_pages'] = audit['pdf_pages']
            atomic_json(folder / 'metadata.json', item)
            log['papers'].append({'id': item['id'], 'title': item['title'], 'duplicate': existing is not None,
                                  'recommendation': info.get('recommendation', ''), 'reason': info.get('reason', ''), 'pdf_status': audit})
        statuses = ('completed', 'reading', 'triage_passed', 'rejected', 'unread')
        lib['stats'] = {'total_papers': len(lib['papers']), **{s: sum(p.get('status', 'unread') == s for p in lib['papers']) for s in statuses}}
        lib['updated_at'] = now()
        atomic_json(libfile, lib)
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]
        logpath = vault / 'searches' / (dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + digest + '.json')
        atomic_json(logpath, log)
    return {'search_record': str(logpath), 'papers': log['papers']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True)
    parser.add_argument('--manifest', required=True, help='Host-verified query/source/papers JSON')
    parser.add_argument('--no-download', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    print(json.dumps(collect(args.vault, manifest, not args.no_download), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
