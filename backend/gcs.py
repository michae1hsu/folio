"""Immutable Markdown objects and renewable, private GCS sharing links."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import quote

from google.api_core import exceptions
from google.cloud import storage

from . import config, drive

MAX_MARKDOWN = 50 * 1024 * 1024
SCOPE = 'https://www.googleapis.com/auth/devstorage.read_write'


def bucket_name():
    path = config.DATA / 'gcs-settings.json'
    return (json.loads(path.read_text(encoding='utf-8'))['bucket'] if path.exists()
            else os.getenv('GCS_BUCKET', '').strip())


def configure(value):
    value = value.strip().removeprefix('gs://').rstrip('/')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]', value):
        raise ValueError('Enter a valid GCS bucket name without an object path.')
    config.DATA.mkdir(parents=True, exist_ok=True)
    path = config.DATA / 'gcs-settings.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'bucket': value}), encoding='utf-8')
    temp.replace(path)
    return {'bucket': value, 'configured': True}


def error_message(error):
    if isinstance(error, ValueError):
        return str(error)
    if 'billing' in str(error).lower() or 'accountdisabled' in str(error).lower():
        return 'Enable billing for the GCS project and retry.'
    if isinstance(error, exceptions.NotFound):
        return 'GCS bucket or Markdown generation not found. Verify the bucket exists.'
    if isinstance(error, exceptions.Forbidden):
        return 'Grant the service account Storage Object User on this GCS bucket.'
    return 'GCS connection or transfer failed. Please retry.'


class GCSClient:
    def __init__(self):
        credentials, self.identity = drive.load_credentials()
        self.client = storage.Client(project=self.identity['project_id'], credentials=credentials.with_scopes([SCOPE]))

    def close(self):
        self.client.close()

    def upload(self, doc, variant, text):
        name = bucket_name()
        if not name:
            raise ValueError('Configure a GCS bucket in Libraries for cloud Markdown storage.')
        data = text.encode('utf-8')
        if len(data) >= MAX_MARKDOWN:
            raise ValueError('Markdown exceeds the 50 MiB Agent file limit. Content was not truncated.')
        digest = hashlib.sha256(data).hexdigest()
        object_name = f'libraries/{doc["library_id"]}/{doc["id"]}/{digest}/{variant}.md'
        bucket = self.client.bucket(name)
        blob = bucket.get_blob(object_name, timeout=30)
        if blob is None:
            blob = bucket.blob(object_name)
            blob.metadata = {'sha256': digest, 'folio_library': doc['library_id'],
                             'folio_document': doc['id'], 'folio_variant': variant}
            blob.cache_control = 'private, no-store'
            try:
                # .md contents remain Markdown; text/plain lets a shared link open in the browser.
                blob.upload_from_string(data, content_type='text/plain; charset=utf-8',
                                        if_generation_match=0, timeout=60)
            except exceptions.PreconditionFailed:
                blob = bucket.get_blob(object_name, timeout=30)
        if (blob is None or (blob.metadata or {}).get('sha256') != digest or int(blob.size) != len(data)):
            raise ValueError('The existing GCS object has a different SHA-256. Remote content was not overwritten.')
        asset = {'provider': 'gcs', 'status': 'uploaded', 'bucket': name, 'object': object_name,
                 'generation': str(blob.generation), 'sha256': digest, 'size': len(data),
                 'uri': f'gs://{name}/{object_name}'}
        # Read back before publishing a link or removing the local staging file.
        if self.read(asset) != data:
            raise ValueError('GCS readback verification failed. Local staging is retained for retry.')
        return asset

    def read(self, asset):
        if int(asset['size']) >= MAX_MARKDOWN:
            raise ValueError('Markdown exceeds 50 MiB. Content was not truncated.')
        generation = int(asset['generation'])
        blob = self.client.bucket(asset['bucket']).blob(asset['object'], generation=generation)
        data = blob.download_as_bytes(if_generation_match=generation, timeout=60)
        if len(data) != int(asset['size']) or hashlib.sha256(data).hexdigest() != asset['sha256']:
            raise ValueError('GCS Markdown content or generation changed. SHA-256 verification failed.')
        return data


def read(asset):
    client = GCSClient()
    try:
        return client.read(asset)
    except Exception as error:
        raise ValueError(error_message(error)) from None
    finally:
        client.close()


def connection():
    name = bucket_name()
    if not name:
        return {'connected': False, 'message': 'GCS bucket is not configured.'}
    client = GCSClient()
    try:
        required = ['storage.objects.create', 'storage.objects.get']
        allowed = client.client.bucket(name).test_iam_permissions(required, timeout=20)
        if set(required) - set(allowed):
            raise ValueError('Grant Storage Object User on this bucket to allow object creation and reads.')
        return {'connected': True, 'bucket': name, **client.identity,
                'message': 'GCS read/write permissions verified. Uploads also verify billing, generation, and complete content.'}
    except Exception as error:
        return {'connected': False, 'bucket': name, 'message': error_message(error)}
    finally:
        client.close()


@lru_cache(maxsize=512)
def _signed_links(bucket, object_name, generation, filename, expires, credential_file):
    credentials, identity = drive.load_credentials()
    client = storage.Client(project=identity['project_id'], credentials=credentials.with_scopes([SCOPE]))
    try:
        blob = client.bucket(bucket).blob(object_name, generation=int(generation))
        options = {'version': 'v4', 'expiration': datetime.fromtimestamp(expires, timezone.utc),
                   'method': 'GET', 'generation': generation}
        disposition = '; filename="document.md"; filename*=UTF-8\'\'' + quote(filename, safe='')
        return {'url': blob.generate_signed_url(**options, response_disposition='inline' + disposition),
                'download_url': blob.generate_signed_url(**options, response_disposition='attachment' + disposition),
                'expires_at': datetime.fromtimestamp(expires, timezone.utc).isoformat()}
    finally:
        client.close()


def links(asset, filename):
    # Reuse signatures during UI polling, while every opened catalog gets a fresh ~24h window.
    expires = int(datetime.now(timezone.utc).timestamp() // 600) * 600 + 86400
    path = drive.credential_path()
    credential_file = (str(path), path.stat().st_mtime_ns)
    return dict(_signed_links(asset['bucket'], asset['object'], asset['generation'], filename, expires, credential_file))
