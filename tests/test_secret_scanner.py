import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts import install_gitleaks as installer
from scripts import run_gitleaks as runner


class VerifiedScannerTests(unittest.TestCase):
    def test_checksum_failure_precedes_archive_read(self):
        with patch.object(installer.zipfile, 'ZipFile') as unzip:
            with self.assertRaises(ValueError):
                installer.verified_executable(b'changed archive', 'windows_x64.zip')
            unzip.assert_not_called()

    def test_only_expected_executable_is_read_from_verified_archive(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('gitleaks.exe', b'executable fixture')
            archive.writestr('../unexpected.txt', b'never extract')
        payload = buffer.getvalue()
        with patch.dict(installer.CHECKSUMS, {'windows_x64.zip': hashlib.sha256(payload).hexdigest()}):
            self.assertEqual(installer.verified_executable(payload, 'windows_x64.zip'), b'executable fixture')

    def test_tar_symlink_executable_is_rejected(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo('gitleaks')
            member.type = tarfile.SYMTYPE
            member.linkname = '/unexpected'
            archive.addfile(member)
        payload = buffer.getvalue()
        with patch.dict(installer.CHECKSUMS, {'linux_x64.tar.gz': hashlib.sha256(payload).hexdigest()}):
            with self.assertRaises(ValueError):
                installer.verified_executable(payload, 'linux_x64.tar.gz')

    def test_platform_mapping_and_unsupported_platform_fail_closed(self):
        self.assertEqual(installer.asset_name('Windows', 'AMD64'), 'windows_x64.zip')
        self.assertEqual(installer.asset_name('Linux', 'aarch64'), 'linux_arm64.tar.gz')
        with self.assertRaises(ValueError):
            installer.asset_name('unknown', 'unknown')

    def test_scanner_reports_only_counts_even_if_tool_output_contains_secret(self):
        secret = 'sk-' + 'x' * 40
        def run(command, **kwargs):
            self.assertNotIn('GITLEAKS_CONFIG_TOML', kwargs['env'])
            self.assertEqual(list(Path(kwargs['cwd']).iterdir()), [])
            report = Path(command[command.index('--report-path') + 1])
            report.write_text(json.dumps([{'RuleID': 'fixture-rule', 'Secret': secret, 'Match': secret}]), encoding='utf-8')
            return SimpleNamespace(returncode=1, stdout=secret.encode(), stderr=secret.encode())
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'scanner'
            binary.touch()
            message = Path(temporary) / 'message'
            message.write_text(secret, encoding='utf-8')
            output = io.StringIO()
            with patch.dict(os.environ, {'GITLEAKS_CONFIG_TOML': 'untrusted exemption'}), \
                    patch.object(runner.subprocess, 'run', run), \
                    patch('sys.argv', ['scanner', '--gitleaks', str(binary), '--commit-msg', str(message)]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(runner.main(), 1)
            self.assertNotIn(secret, output.getvalue())
            self.assertIn('fixture-rule: 1', output.getvalue())

    def test_failed_or_invalid_scanner_report_is_not_success(self):
        for code, report in ((2, '[]'), (0, 'not-json'), (0, '{}')):
            def run(command, **kwargs):
                Path(command[command.index('--report-path') + 1]).write_text(report, encoding='utf-8')
                return SimpleNamespace(returncode=code)
            with patch.object(runner.subprocess, 'run', run):
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    runner.scan(Path('scanner'), b'fixture')

    def test_missing_scanner_blocks_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch('sys.argv', ['scanner', '--history', '--gitleaks', str(Path(temporary) / 'missing')]), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(runner.main(), 2)
