"""Load local dotenv values without evaluating shell code or displaying secrets."""
import os
from pathlib import Path
import re
import shlex


def environment(root):
    values = {}
    path = Path(root) / '.env'
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line)
            if not match:
                continue
            key, raw = match.groups()
            lexer = shlex.shlex(raw, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = '#'
            value = ' '.join(lexer)
            context = dict(values, **os.environ)
            value = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)',
                           lambda m: context.get(m.group(1) or m.group(2), ''), value)
            values[key] = value
    values.update(os.environ)
    values['LOCAL_UID'] = str(os.getuid()) if hasattr(os, 'getuid') else '1000'
    values['LOCAL_GID'] = str(os.getgid()) if hasattr(os, 'getgid') else '1000'
    return values
