"""Reproduce installed dependency license/notice texts, without granting a project license."""
from importlib import metadata
from pathlib import Path
import hashlib
import sys


def collect(output: Path | None = None) -> dict:
    root = Path(__file__).resolve().parent
    output = output or root / 'LICENSES.txt'
    parts = ['小伴 Windows — Third-party licenses and notices\n',
             'This file reproduces license/notice files from the desktop build environment.\n'
             'It does not grant a license for the Xiaoban project itself.\n'
             'Build tools may appear in the inventory even when not included in the runtime.\n']
    missing = []
    count = 0
    for dist in sorted(metadata.distributions(), key=lambda item: item.metadata.get('Name', '').lower()):
        name = dist.metadata.get('Name', 'unknown')
        parts.append('\n' + '=' * 78 + f'\n{name} {dist.version}\n')
        for key in ['License-Expression', 'License', 'Home-page', 'Project-URL']:
            for value in dist.metadata.get_all(key, []):
                parts.append(f'{key}: {value}\n')
        found, seen = False, set()
        for item in dist.files or []:
            filename = Path(str(item)).name.lower()
            if not any(word in filename for word in ['license', 'licence', 'copying', 'notice', 'copyright']):
                continue
            path = Path(dist.locate_file(item))
            if not path.is_file() or path.suffix.lower() in {'.py', '.pyc', '.dll', '.exe', '.so'}:
                continue
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            parts.append(f'\n--- Verbatim file: {item} ---\n')
            parts.append(raw.decode('utf-8', errors='replace') + '\n')
            found = True
            count += 1
        if not found:
            missing.append(name)
            parts.append('\nNo separate license/notice text was shipped in this installed distribution. The metadata above is reproduced without inventing missing terms.\n')
    python_license = Path(sys.base_prefix) / 'LICENSE_PYTHON.txt'
    if python_license.is_file():
        parts += ['\n' + '=' * 78 + '\nCPython runtime — LICENSE_PYTHON.txt\n', python_license.read_text(encoding='utf-8') + '\n']
        count += 1
    for path in sorted((root / 'licenses').glob('*.txt')):
        parts += ['\n' + '=' * 78 + f'\nSupplementary upstream notice — {path.name}\n', path.read_text(encoding='utf-8', errors='replace') + '\n']
        count += 1
    provenance = root / 'licenses/SOURCES.md'
    if provenance.is_file():
        parts += ['\n' + '=' * 78 + '\nSupplementary source provenance\n', provenance.read_text(encoding='utf-8') + '\n']
    output.write_text(''.join(parts), encoding='utf-8', newline='\n')
    return {'files': count, 'wheel_license_files_missing_supplemented_from_upstream': missing, 'bytes': output.stat().st_size}


if __name__ == '__main__':
    print(collect())
