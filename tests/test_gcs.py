import base64
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.api_core import exceptions
from google.oauth2 import service_account

from backend import chat, config, drive, gcs, jobs, knowledge


class CloudMarkdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(config, 'DATA', self.root),
                        patch.object(knowledge, 'STATE', {'libraries': {'a': {'id': 'a', 'name': 'A'}}, 'documents': {}, 'builds': []})]
        for item in self.patches:
            item.start()
        gcs.configure('test-folio-bucket')
        self.text = '# 完整會議\n\n| 項目 | 結果 |\n|---|---|\n| 決議 | 完成 |\n\n最後一行'
        self.data = self.text.encode('utf-8')
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.doc = {'id': 'doc', 'library_id': 'a', 'name': '會議.mp3', 'media': True,
                    'content_hash': self.digest, 'source': {'id': 'drive-original'}, 'assets': {},
                    'index_status': 'ready', 'storage_status': 'uploaded', 'job_id': 'a', 'item_id': '0001'}
        self.asset = {'provider': 'gcs', 'status': 'uploaded', 'bucket': 'test-folio-bucket',
                      'object': f'libraries/a/doc/{self.digest}/report.md', 'generation': '12',
                      'sha256': self.digest, 'size': len(self.data), 'uri': 'gs://test-folio-bucket/report.md'}
        self.doc['assets']['report'] = dict(self.asset)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()
        gcs._signed_links.cache_clear()

    def cloud_client(self):
        client = gcs.GCSClient.__new__(gcs.GCSClient)
        client.client = MagicMock()
        client.identity = {'email': 'test@example.com', 'project_id': 'test'}
        blob = client.client.bucket.return_value.blob.return_value
        blob.size = len(self.data)
        blob.generation = 12
        blob.download_as_bytes.return_value = self.data
        return client, blob

    def test_upload_is_immutable_verified_and_scoped_to_import(self):
        client, blob = self.cloud_client()
        bucket = client.client.bucket.return_value
        bucket.get_blob.return_value = None
        result = client.upload(self.doc, 'report', self.text)
        self.assertEqual(result['sha256'], self.digest)
        self.assertEqual(result['generation'], '12')
        self.assertEqual(blob.upload_from_string.call_args.kwargs['if_generation_match'], 0)
        self.assertEqual(blob.upload_from_string.call_args.args[0], self.data)
        self.assertEqual(blob.download_as_bytes.call_args.kwargs['if_generation_match'], 12)
        self.assertTrue(result['object'].startswith('libraries/a/doc/'))
        second = client.upload({**self.doc, 'library_id': 'b', 'id': 'doc-b'}, 'report', self.text)
        self.assertNotEqual(result['object'], second['object'])

    def test_uncertain_upload_retry_reuses_verified_object_without_duplicate(self):
        client, blob = self.cloud_client()
        blob.metadata = {'sha256': self.digest}
        client.client.bucket.return_value.get_blob.return_value = blob
        result = client.upload(self.doc, 'report', self.text)
        blob.upload_from_string.assert_not_called()
        self.assertEqual(result['sha256'], self.digest)
        blob.download_as_bytes.return_value = b'corrupted cloud contents'
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            client.upload(self.doc, 'report', self.text)

    def test_concurrent_create_checks_winning_object_and_never_overwrites(self):
        client, blob = self.cloud_client()
        blob.upload_from_string.side_effect = exceptions.PreconditionFailed('exists')
        client.client.bucket.return_value.get_blob.side_effect = [None, blob]
        self.assertEqual(client.upload(self.doc, 'report', self.text)['generation'], '12')
        blob.upload_from_string.assert_called_once()

    def test_cloud_read_is_authoritative_without_any_local_markdown(self):
        self.doc['text_path'] = str(self.root / 'missing.md')
        with patch.object(gcs, 'read', return_value=self.data) as read:
            self.assertEqual(knowledge.read_markdown(self.doc), self.text)
            read.assert_called_once_with(self.asset)
        (self.root / 'missing.md').write_text(self.text, encoding='utf-8')
        with patch.object(gcs, 'read', side_effect=ValueError('GCS denied')):
            with self.assertRaisesRegex(ValueError, 'GCS denied'):
                knowledge.read_markdown(self.doc)

    def test_jev_receives_entire_cloud_markdown_without_local_fallback(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(body['state']['full_document'], self.text)
            return httpx.Response(200, json={'model': 'jev-1.13.0', 'answers': {'relevance': {
                'type': 'score', 'score': 3, 'confidence': 1, 'probabilities': {'0': 0, '1': 0, '2': 0, '3': 1}}}})
        with patch.object(gcs, 'read', return_value=self.data), patch.dict('os.environ', {'JEV_API_KEY': 'test'}):
            score = knowledge.judge_document('完整決議', self.doc, httpx.MockTransport(handle))
        self.assertEqual(score['input_chars'], len(self.text))

    def test_staging_removed_only_after_cloud_verification_and_ready_index(self):
        folder = self.root / 'a' / '0001'
        folder.mkdir(parents=True)
        for name in ['report.md', 'result.md']:
            (folder / name).write_text(self.text, encoding='utf-8')
        self.doc['assets']['report']['path'] = str(folder / 'report.md')
        self.doc['index_status'] = 'failed'
        knowledge.remove_staging_markdown(self.doc)
        self.assertTrue((folder / 'report.md').exists())
        self.doc['index_status'] = 'ready'
        knowledge.remove_staging_markdown(self.doc)
        self.assertFalse((folder / 'report.md').exists())
        self.assertFalse((folder / 'result.md').exists())

    def test_v4_links_open_and_download_same_pinned_generation_with_expiry(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        credentials = service_account.Credentials.from_service_account_info({
            'type': 'service_account', 'client_email': 'test@example.iam.gserviceaccount.com',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'private_key': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()).decode()})
        path = self.root / 'credential-placeholder'
        path.touch()
        with patch.object(drive, 'load_credentials', return_value=(credentials, {'project_id': 'test'})), patch.object(drive, 'credential_path', return_value=path):
            links = gcs.links(self.asset, '會議.mp3.report.md')
        for kind in ['url', 'download_url']:
            url = urlparse(links[kind])
            query = parse_qs(url.query)
            self.assertEqual(url.scheme, 'https')
            self.assertEqual(url.hostname, 'storage.googleapis.com')
            self.assertEqual(query['generation'], ['12'])
            self.assertIn('X-Goog-Signature', query)
            self.assertGreater(int(query['X-Goog-Expires'][0]), 85000)
            self.assertLessEqual(int(query['X-Goog-Expires'][0]), 86400)
            self.assertTrue(query['response-content-disposition'][0].startswith('inline' if kind == 'url' else 'attachment'))
        self.assertNotEqual(links['url'], links['download_url'])

    def test_agent_uses_cloud_bytes_and_renews_link_when_reusing_sandbox_file(self):
        knowledge.STATE['documents']['doc'] = self.doc
        c = {'id': 'chat', 'library_id': 'a', 'authorized_files': {'doc': self.digest},
             'fetched': {}, 'environment_id': 'env', 'session_id': 'sess'}
        api = MagicMock()
        with patch.object(chat, 'wait_environment'), patch.object(chat, 'save'), patch.object(chat, 'trace'), \
             patch.object(gcs, 'read', return_value=self.data) as read, \
             patch.object(gcs, 'links', side_effect=[{'url': 'https://example.com/first'}, {'url': 'https://example.com/renewed'}]), \
             patch.object(drive, 'DriveClient') as original:
            result = chat.fetch_file(c, api, 'doc', 'report')
            self.assertEqual(base64.b64decode(api.beta.agents.environments.files.create.call_args.kwargs['data']), self.data)
            self.assertEqual(result['sha256'], self.digest)
            renewed = chat.fetch_file(c, api, 'doc', 'report')
            self.assertEqual(renewed['url'], 'https://example.com/renewed')
            read.assert_called_once()
            api.beta.agents.environments.files.create.assert_called_once()
            original.assert_not_called()
            self.assertEqual(list(self.root.rglob('*.md')), [])

    def test_job_preview_and_archive_can_read_cloud_after_staging_removed(self):
        self.doc['assets']['transcript'] = {**self.asset, 'object': 'transcript.md'}
        knowledge.STATE['documents']['doc'] = self.doc
        job = {'id': 'a', 'name': 'demo', 'files': [{'id': '0001', 'name': '會議.mp3', 'kind': 'audio', 'knowledge_id': 'doc'}]}
        (self.root / 'a').mkdir()
        with patch.object(gcs, 'read', return_value=self.data), patch.object(jobs, 'snapshot', return_value=job):
            self.assertEqual(jobs.output_text(job, job['files'][0]), self.text)
            archive = jobs.make_archive(job)
        with zipfile.ZipFile(archive) as file:
            self.assertEqual(file.read('text/會議.mp3.transcript.md'), self.data)
            self.assertEqual(file.read('text/會議.mp3.report.md'), self.data)
            self.assertEqual(file.read('markdown/會議.mp3.md'), self.data)

    def test_connection_checks_object_permissions_and_billing_errors_are_specific(self):
        client, _ = self.cloud_client()
        client.client.bucket.return_value.test_iam_permissions.return_value = ['storage.objects.get']
        with patch.object(gcs, 'GCSClient', return_value=client):
            self.assertFalse(gcs.connection()['connected'])
        self.assertIn('billing', gcs.error_message(exceptions.Forbidden('The billing account is disabled')))
        self.assertIn('Storage Object User', gcs.error_message(exceptions.Forbidden('denied')))


if __name__ == '__main__':
    unittest.main()
