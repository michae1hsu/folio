import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import tokenizer


class TokenizerTests(unittest.TestCase):
    def check_offline_start(self, corrupt_cache=False):
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / hashlib.sha1(tokenizer.SOURCE_URL.encode()).hexdigest()
            if corrupt_cache:
                cache_file.write_bytes(b'corrupt cache')
            script = """
from unittest.mock import patch
with patch('requests.sessions.Session.request', side_effect=AssertionError('Network is forbidden')):
    from backend import knowledge
    text = '中文文件與完整分詞。Hello, world!'
    assert knowledge.ENCODER.name == 'cl100k_base'
    assert knowledge.ENCODER.decode(knowledge.ENCODER.encode(text)) == text
"""
            result = subprocess.run(
                [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'TIKTOKEN_CACHE_DIR': directory,
                     'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
                capture_output=True, text=True, encoding='utf-8', timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(hashlib.sha256(cache_file.read_bytes()).hexdigest(), tokenizer.SHA256)

    def test_cold_start_without_network(self):
        self.check_offline_start()

    def test_corrupt_cache_is_repaired_without_network(self):
        self.check_offline_start(corrupt_cache=True)

    def test_corrupt_bundle_fails_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory:
            bundled = Path(directory) / 'cl100k_base.tiktoken'
            bundled.write_bytes(b'corrupt bundle')
            with patch.object(tokenizer, 'BUNDLED_FILE', bundled), \
                    patch('requests.sessions.Session.request', side_effect=AssertionError('Network is forbidden')):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    tokenizer.load_encoding()
