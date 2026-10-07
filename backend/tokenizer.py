"""Load the bundled cl100k_base vocabulary without a network download."""
import hashlib
import os
import tempfile
from pathlib import Path

import tiktoken

BUNDLED_FILE = Path(__file__).resolve().parents[1] / 'vendor/tiktoken/cl100k_base.tiktoken'
SOURCE_URL = 'https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken'
SHA256 = '223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7'


def load_encoding():
    data = BUNDLED_FILE.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Bundled cl100k_base tokenizer checksum mismatch; restore it from Git.')

    cache_dir = Path(os.getenv('TIKTOKEN_CACHE_DIR') or os.getenv('DATA_GYM_CACHE_DIR')
                     or Path(tempfile.gettempdir()) / 'data-gym-cache')
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / hashlib.sha1(SOURCE_URL.encode()).hexdigest()
    if not cache_file.is_file() or hashlib.sha256(cache_file.read_bytes()).hexdigest() != SHA256:
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        try:
            temporary.replace(cache_file)
        finally:
            temporary.unlink(missing_ok=True)

    os.environ['TIKTOKEN_CACHE_DIR'] = str(cache_dir)
    return tiktoken.get_encoding('cl100k_base')
