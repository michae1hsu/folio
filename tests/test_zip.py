"""ZIP safety and the complete provider-mocked import-to-retrieval workflow."""
import base64
import hashlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from backend import config, jobs, knowledge, chat, gcs, zip_import
from backend.app import app


def archive_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return stream.getvalue()


class InlineExecutor:
    def submit(self, function, *args, **kwargs):
        result = Future()
        try:
            result.set_result(function(*args, **kwargs))
        except Exception as error:
            result.set_exception(error)
        return result


class ZipImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, name, value in [
            (config, 'DATA', self.root / 'data'), (jobs, 'JOBS', {}), (chat, 'CHATS', {}),
            (knowledge, 'STATE', {'schema_version': 2, 'libraries': {}, 'documents': {}, 'builds': []})
        ]:
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self, content, name='sample.zip', headers=None):
        return self.client.post('/api/zip/import', params={'filename': name}, content=content,
                                headers=headers or {'Content-Type': 'application/zip', 'X-Folio-Request': '1'})

    def assert_rejected_cleanly(self, content):
        result = self.upload(content)
        self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(jobs.JOBS, {})
        self.assertEqual(knowledge.STATE['libraries'], {})
        self.assertEqual(list(config.DATA.iterdir()), [])

    def test_nested_unicode_paths_and_unsupported_files_are_preserved_without_drive(self):
        body = archive_bytes([('nested/notes.txt', 'Full text'), ('nested/資料.txt', 'Original language'),
                              ('unsupported.bin', b'opaque'), ('__MACOSX/._notes.txt', b'metadata')])
        with patch('backend.drive.DriveClient') as drive, patch('backend.models.api_client') as ai:
            result = self.upload(body)
        self.assertEqual(result.status_code, 200, result.text)
        job = result.json()
        self.assertEqual(job['source']['type'], 'zip')
        self.assertEqual(len(job['files']), 3)
        self.assertEqual(job['ignored'], ['__MACOSX/._notes.txt'])
        self.assertEqual(job['files'][2]['kind'], 'unsupported')
        self.assertEqual(job['files'][1]['name'], 'nested/資料.txt')
        self.assertEqual((jobs.folder(job) / '0001/source.txt').read_text(), 'Full text')
        self.assertEqual(job['files'][0]['sha256'], hashlib.sha256(b'Full text').hexdigest())
        self.assertIn(job['id'], knowledge.STATE['libraries'])
        drive.assert_not_called()
        ai.assert_not_called()

    def test_repeated_imports_create_isolated_libraries(self):
        data = archive_bytes([('one.txt', 'One')])
        a, b = self.upload(data).json(), self.upload(data).json()
        self.assertNotEqual(a['id'], b['id'])
        self.assertNotEqual(knowledge.collection_name(a['id']), knowledge.collection_name(b['id']))

    def test_unsafe_paths_never_escape_the_batch(self):
        for name in ['../outside.txt', '/absolute.txt', 'C:/outside.txt', 'a/../../x.txt',
                     '\\\\server\\share.txt', 'a/./x.txt', 'a//b.txt', 'CON.txt', 'a.txt:stream', 'trailing.']:
            with self.subTest(name=name):
                self.assert_rejected_cleanly(archive_bytes([(name, 'payload')]))
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_duplicate_case_and_unicode_names_are_rejected(self):
        for names in [('A.txt', 'a.txt'), ('café.txt', 'cafe\u0301.txt'), ('a.txt', 'a.txt')]:
            with self.subTest(names=names):
                self.assert_rejected_cleanly(archive_bytes([(n, 'payload') for n in names]))

    def test_symlink_and_special_members_are_rejected(self):
        for kind in [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR]:
            info = zipfile.ZipInfo('link.txt')
            info.create_system = 3
            info.external_attr = (kind | 0o777) << 16
            self.assert_rejected_cleanly(archive_bytes([(info, '../outside.txt')]))

    def test_encrypted_archive_is_rejected_before_extraction(self):
        data = bytearray(archive_bytes([('secret.txt', 'text')]))
        data[6] |= 1
        central = data.index(b'PK\x01\x02')
        data[central + 8] |= 1
        self.assert_rejected_cleanly(bytes(data))

    def test_empty_invalid_and_corrupt_archives_leave_no_batch(self):
        for data in [b'not a zip', archive_bytes([]), archive_bytes([('__MACOSX/a', 'metadata')])]:
            self.assert_rejected_cleanly(data)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr('first.txt', 'valid')
            archive.writestr('second.txt', 'CORRUPT-ME')
        data = bytearray(stream.getvalue())
        data[data.index(b'CORRUPT-ME')] ^= 1
        self.assert_rejected_cleanly(bytes(data))

    def test_file_count_and_expanded_size_limits(self):
        for setting, value, data in [
            ('MAX_FILES', 1, archive_bytes([('a.txt', 'x'), ('b.txt', 'y')])),
            ('MAX_FILE', 4, archive_bytes([('a.txt', '12345')])),
            ('MAX_EXPANDED', 6, archive_bytes([('a.txt', '1234'), ('b.txt', '1234')]))
        ]:
            with self.subTest(setting=setting), patch.object(config, setting, value):
                self.assert_rejected_cleanly(data)

    def test_request_guard_type_filename_and_upload_limit(self):
        data = archive_bytes([('a.txt', 'A')])
        self.assertEqual(self.upload(data, headers={'Content-Type': 'application/zip'}).status_code, 403)
        self.assertEqual(self.upload(data, name='file.txt').status_code, 400)
        self.assertEqual(self.upload(data, headers={'Content-Type': 'text/plain', 'X-Folio-Request': '1'}).status_code, 415)
        with patch.object(config, 'MAX_UPLOAD', 5):
            self.assertEqual(self.upload(data).status_code, 413)
        self.assertEqual(jobs.JOBS, {})
        self.assertEqual(list(config.DATA.iterdir()), [])

    def test_import_parse_store_index_search_read_and_export(self):
        """Real converters, Qdrant, HTTP routes, hashes, and archives; fake remote providers."""
        text = 'Folio sample project: the budget is 120 units and the deadline is Friday.'
        imported = self.upload(archive_bytes([('notes/project.txt', text)])).json()
        job = jobs.JOBS[imported['id']]
        db = QdrantClient(':memory:')
        self.addCleanup(db.close)
        client = MagicMock()
        client.__enter__.return_value = client
        client.responses.create.return_value = SimpleNamespace(status='completed', id='synthetic-response', usage=None,
            output_text=json.dumps({'page_count': 1, 'integration': 'A sample project plan.', 'pages': [
                {'number': 1, 'purpose': 'Project plan', 'layout': 'One paragraph', 'markdown': text,
                 'issues': [], 'checks': dict(text=True, tables=True, visuals=True, footnotes=True)}]}))
        def embeddings(**kwargs):
            values = kwargs['input'] if isinstance(kwargs['input'], list) else [kwargs['input']]
            return SimpleNamespace(usage=None, data=[SimpleNamespace(index=i, embedding=[1.0] + [0.0] * 7) for i in range(len(values))])
        client.embeddings.create.side_effect = embeddings
        stored = {}
        cloud = MagicMock()
        def upload(doc, role, body):
            digest = hashlib.sha256(body.encode()).hexdigest()
            stored[digest] = body.encode()
            return {'provider': 'gcs', 'status': 'uploaded', 'bucket': 'example-bucket',
                    'object': f'libraries/{doc["library_id"]}/{doc["id"]}/{role}.md', 'generation': '1',
                    'sha256': digest, 'size': len(body.encode()), 'uri': 'gs://example-bucket/sample.md'}
        cloud.upload.side_effect = upload
        with patch.object(jobs, 'CONVERTERS', InlineExecutor()), patch.object(jobs, 'RUNNERS', MagicMock()), \
                patch.object(knowledge, 'BUILDERS', InlineExecutor()), patch.object(knowledge, 'DB', db), \
                patch.object(config, 'EMBEDDING_DIMENSIONS', 8), \
                patch('backend.app.openai_key_configured', return_value=True), \
                patch.object(jobs, 'api_client', return_value=client), \
                patch.object(knowledge, 'api_client', return_value=client), \
                patch.object(gcs, 'GCSClient', return_value=cloud), \
                patch.object(gcs, 'read', side_effect=lambda asset: stored[asset['sha256']]), \
                patch.object(gcs, 'links', return_value={'url': 'https://example.invalid/open', 'download_url': 'https://example.invalid/download'}), \
                patch.object(knowledge, 'judge_document', return_value={'judge_status': 'completed', 'score': 3, 'grade': 3}), \
                patch('backend.drive.DriveClient') as drive:
            response = self.client.post(f'/api/jobs/{job["id"]}/start', headers={'X-Folio-Request': '1'}, json={'concurrency': 2})
            self.assertEqual(response.status_code, 200, response.text)
            jobs.run_job(job)
            self.assertEqual(job['status'], 'completed', job)
            self.assertEqual(knowledge.STATE['builds'][-1]['status'], 'completed', knowledge.STATE['builds'])
            doc = next(iter(knowledge.STATE['documents'].values()))
            self.assertEqual(doc['source_type'], 'zip')
            self.assertEqual(doc['source_version'], job['files'][0]['sha256'])
            self.assertEqual(doc['index_status'], 'ready')
            self.assertEqual(doc['storage_status'], 'uploaded')
            markdown_url = f'/api/knowledge/{doc["id"]}/markdown?library_id={job["id"]}'
            self.assertIn(text, self.client.get(markdown_url).json()['text'])
            self.assertEqual(self.client.get(markdown_url.replace(job['id'], 'other')).status_code, 404)
            self.assertFalse(Path(doc['text_path']).exists())
            result = knowledge.search_files('project budget', 10, library_id=job['id'])
            self.assertEqual([f['document_id'] for f in result['files']], [doc['id']])
            self.assertIsNone(result['files'][0]['drive_modified_at'])
            source_url = result['files'][0]['links'][0]['url']
            self.assertTrue(source_url.startswith('/api/knowledge/'))
            self.assertEqual(self.client.get(source_url).content, text.encode())
            self.assertEqual(self.client.get(source_url.replace(job['id'], 'other')).status_code, 404)
            c = chat.create(job['id'])
            c = chat.CHATS[c['id']]
            c.update(environment_id='synthetic-environment', session_id='synthetic-session')
            c['authorized_files'][doc['id']] = doc['content_hash']
            with patch.object(chat, 'wait_environment'):
                result = chat.fetch_file(c, client, doc['id'], 'original')
            sent = client.beta.agents.environments.files.create.call_args.kwargs
            self.assertEqual(base64.b64decode(sent['data']), text.encode())
            self.assertEqual(result['provider'], 'local')
            self.assertIsNone(result['url'])
            archive = self.client.get(f'/api/jobs/{job["id"]}/download')
            self.assertEqual(archive.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(archive.content)) as contents:
                self.assertIn(text, contents.read('markdown/notes/project.txt.md').decode())
                self.assertIn('manifest.json', contents.namelist())
            (jobs.folder(job) / '0001/source.txt').write_text('changed', encoding='utf-8')
            self.assertEqual(self.client.get(source_url).status_code, 404)
            drive.assert_not_called()
