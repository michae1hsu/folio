"""Service-account Drive import and explicitly requested generated Markdown sidecars."""
import hashlib
import json
import os
import re
import threading
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from . import config

SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
BASE = 'https://www.googleapis.com/drive/v3'
FOLDER = 'application/vnd.google-apps.folder'
SHORTCUT = 'application/vnd.google-apps.shortcut'
EXPORTS = {
    'application/vnd.google-apps.document': ('.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    'application/vnd.google-apps.spreadsheet': ('.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    'application/vnd.google-apps.presentation': ('.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
    'application/vnd.google-apps.drawing': ('.pdf', 'application/pdf'),
}
FIELDS = 'id,name,mimeType,size,version,modifiedTime,md5Checksum,driveId,resourceKey,parents,appProperties,trashed,capabilities(canDownload,canEdit)'
CONFIG_LOCK = threading.RLock()


def credential_path():
    settings = config.DATA / 'drive-settings.json'
    if settings.exists():
        try:
            value = json.loads(settings.read_text(encoding='utf-8')).get('credentials_path', '')
        except (ValueError, OSError):
            raise ValueError('Google Drive settings cannot be read. Configure the credential path again.')
    else:
        value = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', '')
    if not value:
        raise ValueError('Service account credentials are not configured. Set the JSON path in Connections & models.')
    path = Path(value).expanduser()
    return path if path.is_absolute() else config.ROOT / path


def load_credentials(path=None, write=False):
    try:
        info = json.loads((path or credential_path()).read_text(encoding='utf-8-sig'))
        if not isinstance(info, dict) or info.get('type') != 'service_account':
            raise ValueError('Use a service_account JSON downloaded from Google Cloud, not a user password or OAuth client credential.')
        if info.get('token_uri') != 'https://oauth2.googleapis.com/token':
            raise ValueError('The credential token endpoint is not the official Google endpoint. Download a fresh service account JSON.')
        scope = 'https://www.googleapis.com/auth/drive' if write else SCOPE
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[scope])
        return credentials, {'email': credentials.service_account_email, 'project_id': info.get('project_id', '')}
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        raise ValueError('Cannot read the service account JSON. Check its local path and file permissions.') from None
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError('Credentials must be valid UTF-8 JSON.') from None
    except (GoogleAuthError, KeyError, TypeError):
        raise ValueError('Incomplete or invalid service account JSON. Download it again.') from None
    except ValueError as error:
        # Credential libraries can echo malformed input; return only our own messages.
        if str(error).startswith(('Use a service_account JSON', 'The credential token endpoint', 'Service account credentials are not configured', 'Google Drive settings')):
            raise
        raise ValueError('Cannot load service account credentials. Check the JSON structure and private key.') from None


def status():
    with CONFIG_LOCK:
        try:
            _, identity = load_credentials()
            return {'configured': True, 'authenticated': False, **identity,
                    'credentials_path': str(credential_path()), 'message': 'Credentials loaded. Test the connection or read a folder.'}
        except ValueError as error:
            return {'configured': False, 'authenticated': False, 'email': '', 'project_id': '', 'message': str(error)}


def configure(value):
    with CONFIG_LOCK:
        path = Path(value.strip()).expanduser()
        path = path if path.is_absolute() else config.ROOT / path
        _, identity = load_credentials(path)
        config.DATA.mkdir(parents=True, exist_ok=True)
        target = config.DATA / 'drive-settings.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps({'credentials_path': str(path.resolve())}), encoding='utf-8')
        temp.replace(target)
        return {'configured': True, 'authenticated': False, **identity,
                'credentials_path': str(path.resolve()), 'message': 'Credential path saved. Share the source folder with this service account.'}


def parse_folder(value):
    value = value.strip()
    resource_key = ''
    if '://' in value:
        url = urlparse(value)
        if url.scheme != 'https' or url.hostname != 'drive.google.com' or url.username or url.password:
            raise ValueError('Paste an https://drive.google.com folder link or a folder ID.')
        query = parse_qs(url.query)
        match = re.search(r'/folders/([A-Za-z0-9_-]+)(?:/|$)', url.path)
        value = match.group(1) if match else query.get('id', [''])[0] if url.path in {'/open', '/folderview'} else ''
        resource_key = query.get('resourcekey', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,256}', value):
        raise ValueError('Unrecognized folder ID. Use a Google Drive folder sharing link.')
    if resource_key and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', resource_key):
        raise ValueError('The folder resourcekey is invalid.')
    return value, resource_key


def safe_segment(name):
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:180] or 'Untitled'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', result, re.I):
        result = '_' + result
    return result


def error_message(response):
    reason = ''
    try:
        reason = response.json().get('error', {}).get('errors', [{}])[0].get('reason', '')
    except (ValueError, AttributeError, IndexError):
        pass
    if reason in {'accessNotConfigured', 'serviceDisabled'}:
        return 'Enable the Google Drive API in this Google Cloud project and retry.'
    if reason == 'exportSizeLimitExceeded':
        return 'This Google Workspace file exceeds the Drive export limit. Export it manually and import the exported file.'
    if reason == 'storageQuotaExceeded':
        return 'The service account has no personal Drive storage quota. Use a writable shared drive. Local results were retained.'
    return {401: 'Google authentication expired or failed. Check the service account key.',
            403: 'The service account cannot read or download this file. Check sharing, download restrictions, and organization policies.',
            404: 'Folder or file not found, or not shared with the service account.',
            429: 'Google Drive rate limited. Retry later.'}.get(response.status_code, f'Google Drive request failed (HTTP {response.status_code}）。')


class DriveClient:
    def __init__(self, write=False):
        credentials, self.identity = load_credentials(write=write)
        self.session = AuthorizedSession(credentials)

    def close(self):
        self.session.close()

    def get(self, path, *, params=None, file_id='', resource_key='', stream=False):
        headers = {'X-Goog-Drive-Resource-Keys': f'{file_id}/{resource_key}'} if resource_key else {}
        try:
            response = self.session.get(BASE + path, params=params, headers=headers, stream=stream,
                                        timeout=(15, 90), allow_redirects=False)
        except GoogleAuthError:
            raise ValueError('Google service account authentication failed. Check for a revoked key or disabled account.') from None
        except requests.RequestException:
            raise ValueError('Cannot connect to Google Drive. Check the network and retry.') from None
        if not 200 <= response.status_code < 300:
            message = error_message(response)
            response.close()
            raise ValueError(message)
        return response

    def metadata(self, file_id, resource_key=''):
        with self.get(f'/files/{file_id}', params={'fields': FIELDS, 'supportsAllDrives': 'true'},
                      file_id=file_id, resource_key=resource_key) as response:
            return response.json()

    def children(self, folder_id, drive_id=None, resource_key=''):
        params = {'q': f"'{folder_id}' in parents and trashed = false", 'pageSize': 1000,
                  'fields': f'nextPageToken,incompleteSearch,files({FIELDS})',
                  'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
        if drive_id:
            params.update(corpora='drive', driveId=drive_id)
        while True:
            with self.get('/files', params=params, file_id=folder_id, resource_key=resource_key) as response:
                data = response.json()
            if data.get('incompleteSearch'):
                raise ValueError('Google Drive returned incomplete results. No batch was created; retry later.')
            yield from data.get('files', [])
            if not data.get('nextPageToken'):
                break
            params['pageToken'] = data['nextPageToken']

    def scan(self, value, recursive):
        folder_id, key = parse_folder(value)
        root = self.metadata(folder_id, key)
        if root.get('mimeType') != FOLDER or root.get('trashed'):
            raise ValueError('The link is not a valid Google Drive folder.')
        files, skipped, seen = [], [], set()
        pending = deque([(root, '', key, 0)])
        while pending:
            current, prefix, resource_key, depth = pending.popleft()
            if current['id'] in seen:
                continue
            seen.add(current['id'])
            if len(seen) > 500 or depth > 50:
                raise ValueError('Folder depth or count exceeds the limit. Choose a smaller folder; no batch was created.')
            used = set()
            children = []
            for entry in self.children(current['id'], root.get('driveId'), resource_key):
                children.append(entry)
                if len(children) > config.MAX_FILES + 500:
                    raise ValueError('Too many folder entries. Choose a smaller subfolder; no batch was created.')
            children.sort(key=lambda f: (f['name'], f['id']))
            for entry in children:
                if entry.get('trashed'):
                    continue
                if entry.get('appProperties', {}).get('folio_generated') == '1':
                    continue  # Do not re-import our own generated Markdown files.
                mime = entry['mimeType']
                suffix, export_mime = EXPORTS.get(mime, ('', None))
                name = safe_segment(entry['name'])
                if suffix and not name.lower().endswith(suffix):
                    name += suffix
                base, n = name, 2
                while name.casefold() in used:
                    p = Path(base)
                    name = f'{p.stem} ({n}){p.suffix}'
                    n += 1
                used.add(name.casefold())
                relative = prefix + name
                if mime == FOLDER:
                    if recursive:
                        pending.append((entry, relative + '/', entry.get('resourceKey', ''), depth + 1))
                    else:
                        skipped.append(relative + '/')
                    continue
                reason = None
                if mime == SHORTCUT:
                    reason = 'Shortcuts are not followed automatically. Import the original file or target folder.'
                elif mime.startswith('application/vnd.google-apps.') and not export_mime:
                    reason = 'This Google Workspace type cannot be exported. It remains listed.'
                elif entry.get('capabilities', {}).get('canDownload') is False:
                    reason = 'The owner has not permitted downloads by this service account.'
                elif int(entry.get('size', 0)) > config.MAX_FILE:
                    reason = 'File exceeds the 512 MiB limit.'
                files.append({'name': relative, 'size': int(entry.get('size', 0)), 'source_error': reason,
                              'drive': {**entry, 'parent_id': current['id'], 'export_mime': export_mime, 'original_name': entry['name']}})
                if len(files) > config.MAX_FILES:
                    raise ValueError('The folder exceeds 500 files. Choose a smaller folder; no batch was created.')
        if not files:
            raise ValueError('No readable files found. Check sharing or enable Include subfolders.')
        return {'folder_id': folder_id, 'folder_name': root['name'], 'recursive': recursive,
                'account': self.identity['email'], 'files': files, 'excluded_folders': skipped}

    def download(self, item, target, budget, cancelled):
        source = item['drive']
        file_id, key = source['id'], source.get('resourceKey', '')
        before = self.metadata(file_id, key)
        if before.get('trashed') or before.get('version') != source.get('version'):
            raise ValueError('The Drive file changed after import. Read the folder again to create a new batch.')
        export = source.get('export_mime')
        endpoint = f'/files/{file_id}/export' if export else f'/files/{file_id}'
        params = {'mimeType': export} if export else {'alt': 'media', 'supportsAllDrives': 'true'}
        temp = target.with_suffix(target.suffix + '.part')
        sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
        try:
            with self.get(endpoint, params=params, file_id=file_id, resource_key=key, stream=True) as response, temp.open('wb') as output:
                for chunk in response.iter_content(1024 * 1024):
                    if cancelled():
                        raise ValueError('Download cancelled.')
                    if not chunk:
                        continue
                    if size + len(chunk) > config.MAX_FILE:
                        raise ValueError('Download exceeds the 512 MiB file limit.')
                    budget.reserve(len(chunk))
                    size += len(chunk)
                    sha.update(chunk)
                    md5.update(chunk)
                    output.write(chunk)
            after = self.metadata(file_id, key)
            if before.get('version') != after.get('version') or after.get('trashed'):
                raise ValueError('The file changed during download. Content was rejected; import the folder again.')
            if not export and (('size' in before and size != int(before['size'])) or
                               (before.get('md5Checksum') and md5.hexdigest() != before['md5Checksum'])):
                raise ValueError('Drive download size or checksum mismatch. Retry the download.')
            temp.replace(target)
            return {'size': size, 'sha256': sha.hexdigest()}
        except requests.RequestException:
            budget.reserve(-size)
            raise ValueError('Drive download interrupted. Check the network and retry.') from None
        except Exception:
            budget.reserve(-size)
            raise
        finally:
            temp.unlink(missing_ok=True)

    def generate_file_id(self):
        with self.get('/files/generateIds', params={'count': 1, 'space': 'drive', 'type': 'files'}) as response:
            return response.json()['ids'][0]

    def upload_text(self, source, text, role, file_id):
        """The caller persists file_id before sending, so uncertain retries cannot create duplicates."""
        parent = source.get('parent_id') or next(iter(source.get('parents', [])), None)
        if not parent:
            parent = next(iter(self.metadata(source['id']).get('parents', [])), None)
        if not parent:
            raise ValueError('Source parent folder not found; cannot store Markdown beside the original.')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        existing = self.session.get(BASE + f'/files/{file_id}',
                                    params={'fields': FIELDS, 'supportsAllDrives': 'true'}, timeout=(15, 90))
        with existing:
            if existing.status_code == 200:
                meta = existing.json()
                props = meta.get('appProperties', {})
                if meta.get('trashed') or props.get('folio_hash') != digest or props.get('folio_source') != source['id'] or props.get('folio_role') != role:
                    raise ValueError('Registered Markdown content or status changed. Rebuild its catalog entry.')
                return meta
            if existing.status_code != 404:
                raise ValueError(error_message(existing))
        metadata = {'id': file_id, 'name': source.get('original_name', source['name']) + f'.{role}.md',
                    'mimeType': 'text/markdown', 'parents': [parent],
                    'appProperties': {'folio_generated': '1', 'folio_source': source['id'],
                                      'folio_role': role, 'folio_hash': digest}}
        boundary = 'folio_' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
                + json.dumps(metadata, ensure_ascii=False)
                + f'\r\n--{boundary}\r\nContent-Type: text/markdown; charset=UTF-8\r\n\r\n'
                + text + f'\r\n--{boundary}--\r\n').encode('utf-8')
        try:
            with self.session.post('https://www.googleapis.com/upload/drive/v3/files',
                                   params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': FIELDS},
                                   headers={'Content-Type': f'multipart/related; boundary={boundary}'},
                                   data=body, timeout=(15, 120), allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError(error_message(response))
                return response.json()
        except requests.RequestException:
            raise ValueError('Drive upload interrupted. Retrying checks the same file ID to prevent duplicates.') from None


class DownloadBudget:
    def __init__(self, used=0):
        self.used = used
        self.lock = threading.Lock()

    def reserve(self, count):
        with self.lock:
            if self.used + count > config.MAX_EXPANDED:
                raise ValueError('Batch download exceeds 2 GiB. Choose a smaller folder.')
            self.used += count
