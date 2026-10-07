"""Scan index, messages, or every stored Git object; output rule counts only."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

if __package__:
    from . import check_secrets
else:
    import check_secrets

ROOT = Path(__file__).resolve().parents[1]


def scan(binary, data):
    with tempfile.TemporaryDirectory(prefix='folio-secret-scan-') as directory:
        report = Path(directory) / 'findings.json'
        # Use built-in rules only: a repository config/baseline or inherited config
        # environment must not silently exempt sensitive content from the gate.
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GITLEAKS_')}
        result = subprocess.run([str(binary.resolve()), 'stdin', '--redact=100', '--no-banner',
                                 '--ignore-gitleaks-allow', '--max-decode-depth', '3',
                                 '--max-archive-depth', '4', '--report-format', 'json',
                                 '--report-path', str(report)], input=data, capture_output=True,
                                cwd=directory, env=environment)
        if result.returncode not in {0, 1}:
            raise ValueError('Scanner failed')
        findings = json.loads(report.read_text(encoding='utf-8'))
        if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
            raise ValueError('Invalid scanner report')
        # Never emit tool output, excerpts, filenames, or matching values.
        counts = Counter(str(item.get('RuleID', 'unknown-rule')) for item in findings)
        return result.returncode, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--staged', action='store_true')
    group.add_argument('--history', action='store_true')
    group.add_argument('--commit-msg', type=Path)
    parser.add_argument('--gitleaks', type=Path,
                        default=ROOT / '.tools' / 'gitleaks' / ('gitleaks.exe' if platform.system() == 'Windows' else 'gitleaks'))
    args = parser.parse_args()
    if not args.gitleaks.is_file():
        print('Gitleaks is required. Run: python scripts/install_gitleaks.py')
        return 2
    try:
        if args.commit_msg:
            data = args.commit_msg.read_bytes()
        elif args.history:
            data = b'\n'.join(data for _, _, data in check_secrets.history_objects())
        else:
            data = b'\n'.join(data for _, data in check_secrets.blobs('staged'))
        code, counts = scan(args.gitleaks.resolve(), data)
        for rule, count in sorted(counts.items()):
            print(f'BLOCKED: Gitleaks rule {rule}: {count} finding(s)')
        if counts or code:
            return 1
    except Exception:
        print('Gitleaks could not complete; no scanner output or sensitive content was printed.')
        return 2
    print('Gitleaks passed; no matching credentials.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
