"""Build reproducible .skill/.zip distributions from explicit source roots.

Run from any directory: python tools/package_skill.py --output dist
The .skill format here is a ZIP container with one SKILL.md-based directory.
"""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'academic-reading-master'
TOP_FILES = ('SKILL.md', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
             'requirements.txt', 'requirements-dev.txt', 'VERSION', '.gitignore')
SUFFIXES = {
    'references': {'.md'},
    'scripts': {'.py', '.cjs', '.js', '.svg'},
    'assets': {'.html', '.js', '.css', '.json', '.woff', '.woff2', '.ttf'},
    'tools': {'.py'},
}
EXCLUDED = {'__pycache__', 'artifacts', '.git', 'node_modules', '.reader',
            'PaperVault', 'dist', 'outputs', 'validation'}


def source_files():
    files = []
    for name in TOP_FILES:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing or unsafe distribution file: {name}')
        files.append(path)
    for directory, suffixes in SUFFIXES.items():
        base = ROOT / directory
        if base.is_symlink():
            raise ValueError(f'Symlinked source root: {directory}')
        for path in sorted(base.rglob('*')):
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f'Symlink in source: {relative}')
            if not path.is_file():
                continue
            if path.suffix not in suffixes and not (directory == 'assets' and path.name == 'LICENSE'):
                raise ValueError(f'Unexpected distribution file: {relative}')
            if not path.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError(f'Source outside repository: {relative}')
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def package(output):
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?', version):
        raise ValueError('Invalid release version.')
    files = source_files()
    output = Path(output).resolve()
    if any(output == ROOT / name or output.is_relative_to(ROOT / name) for name in SUFFIXES):
        raise ValueError('Output must not be inside an included source directory.')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f'{NAME}-v{version}.skill'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            name = f'{NAME}/{path.relative_to(ROOT).as_posix()}'
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, path.read_bytes(), compresslevel=9)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Distribution ZIP failed CRC validation.')
        if f'{NAME}/SKILL.md' not in archive.namelist():
            raise ValueError('SKILL.md missing from distribution.')
    zip_copy = destination.with_suffix('.zip')
    zip_copy.write_bytes(destination.read_bytes())
    sums = ''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
                   for path in (destination, zip_copy))
    (output / 'SHA256SUMS.txt').write_text(sums, encoding='utf-8')
    print(f'Packaged {len(files)} files, {destination.stat().st_size} bytes.')
    print(destination)
    print(zip_copy)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'dist'))
    package(parser.parse_args().output)
