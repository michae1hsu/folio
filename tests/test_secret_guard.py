import importlib.util
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('secret_guard', Path(__file__).resolve().parents[1] / 'scripts/check_secrets.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class SecretGuardTests(unittest.TestCase):
    def make_repo(self, root):
        def git(*args):
            return subprocess.check_output(['git', '-c', 'core.hooksPath=', *args], cwd=root,
                                           stderr=subprocess.DEVNULL)
        git('init', '-q')
        git('config', 'user.name', 'Synthetic Test')
        git('config', 'user.email', 'test@users.noreply.github.com')
        return git

    def test_commit_and_tag_identities_require_approved_public_addresses(self):
        private = b'private' + b'@' + b'mail.invalid'
        for kind, role in [('commit', b'author'), ('commit', b'committer'), ('tag', b'tagger')]:
            with self.subTest(role=role):
                value = role + b' Test <' + private + b'> 1700000000 +0000\n\nfixture'
                self.assertIn('private-identity-email', guard.metadata_findings(kind, value))
                self.assertIn('private-email', guard.metadata_findings(kind, value))
        for email in (b'test@users.noreply.github.com', b'noreply@github.com',
                      b'ai.michae1hsu@gmail.com'):
            for kind, role in [('commit', b'author'), ('commit', b'committer'), ('tag', b'tagger')]:
                with self.subTest(email=email, role=role):
                    value = role + b' Test <' + email + b'> 1700000000 +0000\n\nfixture'
                    self.assertEqual(list(guard.metadata_findings(kind, value)), [])

    def test_public_owner_address_does_not_allow_other_or_lookalike_addresses(self):
        approved = b'ai.michae1hsu@gmail.com'
        self.assertEqual(list(guard.content_findings(approved)), [])
        self.assertEqual(list(guard.content_findings(approved.upper())), [])
        for email in (b'other' + b'@gmail.com', b'not-' + approved,
                      approved + b'.invalid', b'ai.michae1hsu+alias' + b'@gmail.com'):
            with self.subTest(email=email):
                self.assertIn('private-email', guard.content_findings(email))
                identity = b'author Test <' + email + b'> 1700000000 +0000\n\nfixture'
                self.assertIn('private-identity-email', guard.metadata_findings('commit', identity))

    def test_unreachable_commit_message_and_annotated_tag_are_scanned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = self.make_repo(root)
            (root / 'app.txt').write_text('clean fixture', encoding='utf-8')
            git('add', '.')
            git('commit', '-qm', 'sk-' + 'x' * 40)
            old = git('rev-parse', 'HEAD').strip().decode()
            git('commit', '--amend', '-qm', 'Clean fixture')
            git('reflog', 'expire', '--expire=now', '--all')
            git('tag', '-a', 'fixture', '-m', 'sk-' + 'y' * 40)
            objects = list(guard.history_objects(root))
            self.assertTrue(any(kind == 'commit' and old[:12] in label and 'openai-key' in guard.metadata_findings(kind, data)
                                for kind, label, data in objects))
            self.assertTrue(any(kind == 'tag' and 'openai-key' in guard.metadata_findings(kind, data)
                                for kind, _, data in objects))

    def test_same_blob_at_private_and_public_paths_is_not_deduplicated_away(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = self.make_repo(root)
            (root / 'data').mkdir()
            for path in ('app.txt', 'data/chat.txt'):
                (root / path).write_text('same fixture', encoding='utf-8')
            git('add', '.')
            git('commit', '-qm', 'Fixture')
            self.assertTrue(any(path == 'data/chat.txt' and 'private-file-path' in guard.findings(path, data)
                                for _, path, data in guard.history_objects(root)))

    def test_private_resources_and_source_documents_are_blocked(self):
        drive = b'https://drive.google.com/drive/folders/' + b'x' * 25
        signed = b'https://storage.googleapis.com/example?X-Goog-Signature=' + b'a' * 40
        self.assertIn('private-drive-resource', guard.findings('README.md', drive))
        self.assertIn('signed-url', guard.findings('README.md', signed))
        self.assertIn('source-document-file', guard.findings('fixture.pdf', b'%PDF'))
        self.assertEqual(list(guard.content_findings(b'fixture@example.invalid test@users.noreply.github.com')), [])
        self.assertNotIn('private-email', guard.content_findings(b'\0' + b'private' + b'@' + b'mail.invalid'))

    def test_history_preserves_full_template_path_without_promoting_child_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = self.make_repo(root)
            (root / 'examples').mkdir()
            (root / 'examples/google-service-account.example.json').write_text('{"type":"service_account"}', encoding='utf-8')
            git('add', '.')
            git('commit', '-qm', 'Empty template fixture')
            findings = [rule for kind, path, data in guard.history_objects(root)
                        for rule in (guard.findings(path, data) if kind == 'blob' else guard.metadata_findings(kind, data))]
            self.assertEqual(findings, [])

    def test_cli_redacts_sensitive_names_messages_and_effective_identities(self):
        private = 'private' + '@' + 'mail.invalid'
        key = 'sk-' + 'x' * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = self.make_repo(root)
            (root / (private + '.txt')).write_text(key, encoding='utf-8')
            git('add', '.')
            message = root / 'message'
            message.write_text(key, encoding='utf-8')
            def repo_git(*args, **kwargs):
                return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)
            for arguments in (['guard', '--staged'], ['guard', '--commit-msg', str(message)]):
                output = io.StringIO()
                with patch.object(guard, 'ROOT', root), patch.object(guard, 'git', repo_git), \
                        patch.dict(os.environ, {'GIT_AUTHOR_EMAIL': private, 'GIT_COMMITTER_EMAIL': private}), \
                        patch('sys.argv', arguments), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    self.assertEqual(guard.main(), 1)
                self.assertNotIn(private, output.getvalue())
                self.assertNotIn(key, output.getvalue())
                self.assertIn('private-identity-email', output.getvalue())

    def test_empty_examples_are_allowed_but_credentials_in_disguised_json_are_blocked(self):
        self.assertEqual(list(guard.findings('.env.example', b'OPENAI_API_KEY=\nDOCUMENT_MODEL=model')), [])
        self.assertIn('nonempty-secret-template', guard.findings('.env.example', b'OPENAI_API_KEY=not-empty'))
        self.assertIn('service-account-credentials', guard.findings('download.json', b'{"type":"service_account"}'))
        self.assertIn('private-file-path', guard.findings('data/chat.json', b'{}'))
        self.assertIn('private-file-path', guard.findings('.env.local', b'ANY=value'))

    def test_deleted_secret_is_found_in_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)
            git('init', '-q')
            git('config', 'user.name', 'Synthetic Test')
            git('config', 'user.email', 'test@example.invalid')
            (root / 'oops.txt').write_text('sk-' + 'x' * 40)
            git('add', '.')
            git('commit', '-qm', 'Synthetic fixture')
            git('rm', '-q', 'oops.txt')
            git('commit', '-qm', 'Remove fixture')
            self.assertTrue(any('openai-key' in list(guard.findings(p, b)) for p, b in guard.blobs('history', root)))

    def test_staged_scan_checks_index_not_unstaged_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.check_call(['git', 'init', '-q'], cwd=root)
            (root / 'app.txt').write_text('sk-' + 'x' * 40)
            subprocess.check_call(['git', 'add', '.'], cwd=root)
            (root / 'app.txt').write_text('clean working copy')
            self.assertTrue(any('openai-key' in list(guard.findings(p, b)) for p, b in guard.blobs('staged', root)))
