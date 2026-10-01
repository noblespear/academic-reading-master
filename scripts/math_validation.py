"""Validate strict LaTeX with the reader's local KaTeX; no network requests."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

MATH = re.compile(r'\\\((.*?)\\\)|\\\[(.*?)\\\]|(?<!\\)\$\$(.*?)\$\$|(?<![\\\w])\$(?!\d+(?:[.,]\d+)?(?:\s|$))(?!\s)([^$\n]+?)(?<!\s)\$', re.S)


def expressions(text):
    text = str(text)
    matches = list(MATH.finditer(text))
    remainder = MATH.sub('', text)
    if any(token in remainder for token in (r'\(', r'\)', r'\[', r'\]')) or re.search(r'(?<!\\)\$\$', remainder):
        raise ValueError('Unbalanced math delimiters; use \\(…\\) or \\[…\\].')
    if re.search(r'(?<!\\)\$(?!\d+(?:[.,]\d+)?(?:\s|[.,;:!?)]|$))', remainder):
        raise ValueError('Unbalanced inline math delimiter; escape a literal dollar sign with \\$ .')
    return [{'latex': next(part for part in m.groups() if part is not None),
             'display': m.group(2) is not None or m.group(3) is not None} for m in matches]


def validate_math(texts):
    equations = [equation for text in texts for equation in expressions(text)]
    if not equations:
        return 0
    node = os.environ.get('ARM_NODE_PATH') or shutil.which('node')
    if not node:
        raise RuntimeError('LaTeX validation needs Node.js; set ARM_NODE_PATH to the host runtime.')
    result = subprocess.run([node, str(Path(__file__).with_name('render_math.cjs'))],
        input=json.dumps({'expressions': equations}), text=True, encoding='utf-8', capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('Invalid or unsupported strict LaTeX: ' + result.stderr.strip()[-1500:])
    payload = json.loads(result.stdout)
    if len(payload.get('results', [])) != len(equations):
        raise ValueError('Math validator returned an incomplete result.')
    return len(equations)


def markdown_math(text):
    """Use standard Markdown dollar delimiters; retain the exact LaTeX expression."""
    return MATH.sub(lambda m: ('$$' if m.group(2) is not None or m.group(3) is not None else '$')
        + next(part for part in m.groups() if part is not None)
        + ('$$' if m.group(2) is not None or m.group(3) is not None else '$'), str(text))
