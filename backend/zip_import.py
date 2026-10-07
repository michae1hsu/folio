"""Bounded ZIP intake. Member names never become extraction paths."""
import hashlib
import re
import shutil
import stat
import unicodedata
import uuid
import zipfile
from pathlib import Path

from . import config, jobs


def member_name(info):
    name = unicodedata.normalize('NFC', info.orig_filename.replace('\\', '/'))
    parts = name.rstrip('/').split('/')
    if (not name or name.startswith('/') or any(p in {'', '.', '..'} for p in parts)
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(c in name for c in '<>:"|?*')
            or any(p.endswith((' ', '.')) or len(p) > 180 for p in parts)
            or len(name) > 1800
            or any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I) for p in parts)):
        raise ValueError('ZIP contains an unsafe or non-portable file path.')
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (kind == stat.S_IFDIR and not info.is_dir()):
        raise ValueError('ZIP symbolic links and special files are not supported.')
    if info.flag_bits & 1:
        raise ValueError('Encrypted ZIP files are not supported. Upload an unencrypted archive.')
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError('Use a ZIP archive with standard store or deflate compression.')
    return '/'.join(parts)


def import_zip(path, filename):
    """Validate every entry, then create an isolated batch and knowledge library."""
    from . import knowledge
    if Path(filename).suffix.lower() != '.zip':
        raise ValueError('Choose a .zip file.')
    if Path(path).stat().st_size > config.MAX_UPLOAD:
        raise ValueError('ZIP upload exceeds the 512 MiB limit.')
    job_id = uuid.uuid4().hex
    root = config.DATA / job_id
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > config.MAX_FILES * 2:
                raise ValueError('ZIP contains too many entries (maximum 1,000 including folders).')
            selected, ignored, names, total = [], [], set(), 0
            for info in entries:
                name = member_name(info)
                if name.casefold() in names:
                    raise ValueError('ZIP contains duplicate paths (including case or Unicode variants).')
                names.add(name.casefold())
                if info.is_dir():
                    continue
                if info.file_size > config.MAX_FILE:
                    raise ValueError('A ZIP member exceeds the 512 MiB file limit.')
                total += info.file_size
                if total > config.MAX_EXPANDED:
                    raise ValueError('ZIP expanded size exceeds the 2 GiB batch limit.')
                if '__MACOSX' in name.split('/') or Path(name).name in {'.DS_Store', 'Thumbs.db'}:
                    ignored.append(name)
                    continue
                selected.append((info, name))
            if not selected:
                raise ValueError('ZIP contains no source files.')
            if len(selected) > config.MAX_FILES:
                raise ValueError('ZIP contains more than 500 source files.')
            root.mkdir(parents=True)
            files, expanded = [], 0
            for info, name in selected:
                item_id = f'{len(files) + 1:04}'
                item_root = root / item_id
                item_root.mkdir()
                digest, size = hashlib.sha256(), 0
                with archive.open(info) as source, (item_root / ('source' + Path(name).suffix.lower())).open('xb') as target:
                    while block := source.read(1024 * 1024):
                        size += len(block)
                        expanded += len(block)
                        if size > config.MAX_FILE or expanded > config.MAX_EXPANDED:
                            raise ValueError('ZIP exceeded its expanded size limit while reading.')
                        digest.update(block)
                        target.write(block)
                if size != info.file_size:
                    raise ValueError('ZIP member size does not match its metadata.')
                kind = config.file_kind(name)
                files.append({'id': item_id, 'name': name, 'size': size, 'sha256': digest.hexdigest(),
                              'source_downloaded': True, 'source_error': None, 'kind': kind,
                              'status': 'ready', 'stage': 'Ready to process', 'pages': [], 'page_count': 0,
                              'warnings': [], 'error': None, 'has_output': False})
        job = {'id': job_id, 'name': Path(filename.replace('\\', '/')).name,
               'created_at': jobs.now(), 'updated_at': jobs.now(), 'status': 'ready',
               'cancel_requested': False, 'concurrency': 4, 'files': files, 'ignored': ignored,
               'document_model': config.DOCUMENT_MODEL, 'transcription_model': config.TRANSCRIPTION_MODEL,
               'source': {'type': 'zip', 'account': 'local', 'url': None, 'folder_id': None}}
        with jobs.LOCK:
            jobs.save(job)
            jobs.JOBS[job_id] = job
        knowledge.register_library(job)
        return jobs.snapshot(job)
    except Exception as error:
        with jobs.LOCK:
            jobs.JOBS.pop(job_id, None)
        with knowledge.LOCK:
            if knowledge.STATE['libraries'].pop(job_id, None):
                knowledge.save()
        # Only the UUID directory created by this import can be removed.
        if root.parent.resolve() == config.DATA.resolve() and root.exists():
            shutil.rmtree(root)
        if isinstance(error, (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError)):
            raise ValueError('ZIP is corrupt, encrypted, or uses unsupported compression.') from None
        raise


def read_original(doc):
    """Resolve only catalogued local sources and verify the indexed version."""
    if doc.get('source_type') != 'zip':
        raise ValueError('This source is not a ZIP upload.')
    job = jobs.JOBS.get(doc['job_id'])
    if not job or job['id'] != doc['library_id']:
        raise ValueError('The source batch is unavailable.')
    item = next((f for f in job['files'] if f['id'] == doc['item_id']), None)
    if not item or item.get('knowledge_id') != doc['id']:
        raise ValueError('The source file does not belong to this document.')
    path = jobs.folder(job) / item['id'] / ('source' + Path(item['name']).suffix.lower())
    if not path.is_file():
        raise ValueError('The uploaded source is no longer available on this server.')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != doc['source_version']:
        raise ValueError('The uploaded source no longer matches the indexed SHA-256 version.')
    return data
