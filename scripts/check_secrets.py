"""Reject credentials/private data, including identities and all stored Git history.

Reports paths and rule names only, never matching secret values. This is a local
guard, not a replacement for a dedicated scanner and human review.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SAFE_TEMPLATES = {'.env.example', 'examples/google-service-account.example.json'}
PATTERNS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'openai-key': re.compile(rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b'),
    'github-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'google-api-key': re.compile(rb'\bAIza[0-9A-Za-z_-]{30,}\b'),
    'aws-access-key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'slack-token': re.compile(rb'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
    'credential-url': re.compile(rb'https?://[^\s/:]+:[^\s/@]+@[^\s]+'),
}
FORBIDDEN_DIRS = {'data', 'credentials', 'secrets', 'output', 'uploads', '.venv', '.tools', 'node_modules'}
SECRET_SUFFIXES = {'.pem', '.key', '.p12', '.pfx'}
SOURCE_DOCUMENT_SUFFIXES = {'.zip', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                           '.odt', '.ods', '.odp', '.rtf', '.wav', '.mp3', '.mp4', '.m4a', '.mov'}
EMAIL = re.compile(rb'[A-Za-z0-9_.+%\[\]-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
# The owner explicitly uses this dedicated address for public Git contributions.
PUBLIC_IDENTITY = re.compile(
    rb'(?:[^<>\s@]+@users\.noreply\.github\.com|noreply@github\.com|ai\.michae1hsu@gmail\.com)',
    re.I,
)
RESOURCE_PATTERNS = {
    'signed-url': re.compile(rb'X-Goog-(?:Signature|Credential)=[A-Za-z0-9%_-]{16,}', re.I),
    'private-drive-resource': re.compile(rb'https://drive\.google\.com/(?:drive/folders/|file/d/)[A-Za-z0-9_-]{20,}'),
}


def content_findings(data):
    for rule, pattern in {**PATTERNS, **RESOURCE_PATTERNS}.items():
        if pattern.search(data):
            yield rule
    # Binary compressed data can coincidentally look like an email address.
    try:
        data.decode('utf-8')
    except UnicodeError:
        return
    if b'\0' in data:
        return
    for email in EMAIL.findall(data):
        domain = email.rsplit(b'@', 1)[-1].lower()
        if (PUBLIC_IDENTITY.fullmatch(email)
                or domain in {b'example.com', b'example.net', b'example.org', b'example.invalid', b'example.test'}
                or domain == b'example.iam.gserviceaccount.com'):
            continue
        yield 'private-email'


def identity_findings(identity):
    match = re.search(rb'<([^<>]+)> [0-9]+ [+-][0-9]{4}$', identity.strip())
    if not match or not PUBLIC_IDENTITY.fullmatch(match.group(1)):
        yield 'private-identity-email'


def metadata_findings(kind, data):
    yield from content_findings(data)
    for line in data.split(b'\n\n', 1)[0].splitlines():
        if line.startswith((b'author ', b'committer ', b'tagger ')):
            yield from identity_findings(line)


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd, stderr=subprocess.DEVNULL)


def findings(path, data):
    """Yield rule names, with no secret material or content excerpts."""
    p = PurePosixPath(path.replace('\\', '/'))
    name = p.name.lower()
    if path not in SAFE_TEMPLATES:
        if (any(part.lower() in FORBIDDEN_DIRS or part.lower().startswith('data-') for part in p.parts)
                or name == '.env' or name.startswith('.env.') or name.endswith('.env')
                or p.suffix.lower() in SECRET_SUFFIXES
                or re.search(r'(service.?account|credential|secret|token).*\.json$', name)):
            yield 'private-file-path'
        if p.suffix.lower() in SOURCE_DOCUMENT_SUFFIXES:
            yield 'source-document-file'
    yield from content_findings(path.encode('utf-8'))
    yield from content_findings(data)
    if p.suffix.lower() == '.json':
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            value = None
        if isinstance(value, dict):
            if value.get('type') == 'service_account':
                if path != 'examples/google-service-account.example.json' or any(value.get(k) for k in
                        ('private_key', 'private_key_id', 'client_email', 'client_id', 'project_id')):
                    yield 'service-account-credentials'
            if value.get('private_key'):
                yield 'json-private-key'
    if path == '.env.example':
        for line in data.decode('utf-8').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            name, value = line.split('=', 1)
            if re.search(r'(KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL)', name, re.I) and value.strip().strip('\"\''):
                yield 'nonempty-secret-template'


def history_objects(cwd=ROOT):
    """Include reachable, reflog-only, and unreachable objects, with every stored path."""
    objects = [line.split() for line in git('cat-file', '--batch-all-objects',
                                           '--batch-check=%(objectname) %(objecttype)', cwd=cwd).splitlines()]
    contents = {oid: git('cat-file', kind.decode('ascii'), oid.decode('ascii'), cwd=cwd)
                for oid, kind in objects if kind in {b'blob', b'commit', b'tag'}}
    trees = {oid for oid, kind in objects if kind == b'tree'}
    children = set()
    for tree in trees:
        for entry in git('ls-tree', '-z', tree.decode('ascii'), cwd=cwd).split(b'\0'):
            if entry:
                _, kind, oid = entry.split(b'\t', 1)[0].split()
                if kind == b'tree':
                    children.add(oid)
    roots = trees - children
    # A child tree is not another repository root: preserve full paths for template exceptions.
    # Every commit root (including unreachable commits) and directly tagged tree is a root.
    for oid, kind in objects:
        if kind == b'commit':
            roots.add(contents[oid].splitlines()[0].split()[1])
        elif kind == b'tag':
            headers = dict(line.split(b' ', 1) for line in contents[oid].split(b'\n\n', 1)[0].splitlines()
                           if b' ' in line)
            if headers.get(b'type') == b'tree':
                roots.add(headers[b'object'])
    paths = {}
    # Walk all stored root trees; keep every path for blobs reused across directories.
    for oid in roots:
        for entry in git('ls-tree', '-rz', oid.decode('ascii'), cwd=cwd).split(b'\0'):
            if not entry:
                continue
            info, path = entry.split(b'\t', 1)
            mode, entry_kind, blob = info.split()
            if entry_kind == b'blob':
                paths.setdefault(blob, set()).add(path.decode('utf-8'))
    for oid, kind in objects:
        if kind not in {b'blob', b'commit', b'tag'}:
            continue
        name, object_kind = oid.decode('ascii'), kind.decode('ascii')
        data = contents[oid]
        if kind == b'blob':
            for path in sorted(paths.get(oid, {f'<unreachable blob {name[:12]}>'})):
                yield object_kind, path, data
        else:
            yield object_kind, f'<{object_kind} {name[:12]}>', data


def blobs(mode, cwd=ROOT):
    if mode == 'history':
        for kind, path, data in history_objects(cwd):
            if kind == 'blob':
                yield path, data
    else:
        args = ['ls-files', '-z', '--cached']
        if mode == 'worktree':
            args += ['--others', '--exclude-standard']
        for raw in sorted(set(git(*args, cwd=cwd).split(b'\0')) - {b''}):
            path = raw.decode('utf-8')
            if mode == 'staged':
                yield path, git('show', ':' + path, cwd=cwd)
            elif (Path(cwd) / path).is_file():
                yield path, (Path(cwd) / path).read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--staged', action='store_true', help='Scan the complete Git index before commit')
    group.add_argument('--history', action='store_true', help='Scan all stored local Git objects, including unreachable history')
    group.add_argument('--worktree', action='store_true', help='Scan non-ignored working files (default)')
    group.add_argument('--commit-msg', type=Path, help='Scan a proposed commit message and effective identities')
    args = parser.parse_args()
    mode = 'history' if args.history else 'staged' if args.staged else 'commit-msg' if args.commit_msg else 'worktree'
    count, issues = 0, set()
    try:
        if mode == 'history':
            for kind, path, data in history_objects():
                count += 1
                rules = findings(path, data) if kind == 'blob' else metadata_findings(kind, data)
                issues.update((path, rule) for rule in rules)
        elif mode == 'commit-msg':
            count = 1
            issues.update(('<proposed commit message>', rule) for rule in content_findings(args.commit_msg.read_bytes()))
        else:
            for path, data in blobs(mode):
                count += 1
                issues.update((path, rule) for rule in findings(path, data))
        if mode in {'staged', 'commit-msg'}:
            for role in ('AUTHOR', 'COMMITTER'):
                issues.update((f'<effective {role.lower()} identity>', rule)
                              for rule in identity_findings(git('var', f'GIT_{role}_IDENT')))
    except (subprocess.CalledProcessError, OSError, UnicodeError):
        print('Secret scan could not read the repository. No content was printed.', file=sys.stderr)
        return 2
    for path, rule in sorted(issues):
        label = '<sensitive filename>' if any(content_findings(path.encode('utf-8'))) else path
        print(f'BLOCKED: {label} [{rule}]', file=sys.stderr)
    if issues:
        print('Remove sensitive material before committing or pushing. Never bypass this guard.', file=sys.stderr)
        return 1
    print(f'Secret guard passed: {count} entries checked ({mode}); no matching credentials or private metadata.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
