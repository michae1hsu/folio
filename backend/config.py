import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DATA = Path(os.getenv('FOLIO_DATA_DIR', str(ROOT / 'data'))).resolve()
DOCUMENT_MODEL = os.getenv('DOCUMENT_MODEL', 'gpt-6-luna')
TRANSCRIPTION_MODEL = os.getenv('TRANSCRIPTION_MODEL', 'gpt-transcribe')
REPORT_MODEL = os.getenv('REPORT_MODEL', 'gpt-6-luna')
AGENT_MODEL = os.getenv('AGENT_MODEL', 'gpt-6.1-sol')
EMBEDDING_MODEL = 'text-embedding-3-large'
EMBEDDING_DIMENSIONS = 3072
QDRANT_URL = os.getenv('QDRANT_URL', '').strip()
QDRANT_COLLECTION = os.getenv('QDRANT_COLLECTION', 'folio_text_v1')
JEV_MODEL = os.getenv('JEV_MODEL', 'jev-1.13.0')
JEV_URL = 'https://api.typesafe.ai/v1/systemone'
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_FILES = 500
MAX_UPLOAD = 512 * 1024 * 1024

AUDIO = {'.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.opus', '.wma', '.mpga'}
VIDEO = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.mpeg', '.mpg', '.m4v', '.wmv'}
IMAGES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.gif'}
OFFICE = {'.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.odt', '.ods', '.odp', '.rtf'}
TEXT = {'.txt', '.md', '.csv', '.tsv', '.json', '.xml', '.yaml', '.yml', '.log', '.py', '.js', '.ts', '.html', '.htm', '.css', '.sql'}


def file_kind(name):
    ext = Path(name).suffix.lower()
    if ext in AUDIO:
        return 'audio'
    if ext in VIDEO:
        return 'video'
    if ext in IMAGES:
        return 'image'
    if ext in OFFICE | TEXT | {'.pdf'}:
        return 'document'
    return 'unsupported'
