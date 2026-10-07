import hashlib
import json
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from backend import config, drive, jobs, knowledge
from backend.app import app

ROOT_ID = 'folderABCDEFGHIJK'
EMAIL = 'test@example.iam.gserviceaccount.com'


def response(data=None, content=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = content if content is not None else json.dumps(data).encode('utf-8')
    result._content_consumed = True
    return result


def client_with(*responses):
    client = drive.DriveClient.__new__(drive.DriveClient)
    client.identity = {'email': EMAIL}
    calls = []
    pending = list(responses)
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return pending.pop(0)
    client.session = SimpleNamespace(get=get, close=lambda: None)
    return client, calls


def folder():
    return {'id': ROOT_ID, 'name': '教學資料', 'mimeType': drive.FOLDER, 'driveId': 'sharedDrive123'}


def file(name='筆記.txt', file_id='fileABCDEFGHIJK', **extra):
    return {'id': file_id, 'name': name, 'mimeType': 'text/plain', 'version': '1', **extra}


class DriveListingTests(unittest.TestCase):
    def test_generated_markdown_is_not_reimported(self):
        generated = file('報告.parsed.md', 'generated', appProperties={'folio_generated':'1'})
        client, _ = client_with(response(folder()), response({'files':[file(), generated]}))
        self.assertEqual(len(client.scan(ROOT_ID, False)['files']), 1)

    def test_markdown_upload_is_utf8_in_same_folder_and_retry_uses_same_id(self):
        text = '# 架構\n|A|B|\n|---|---|\n|1|2|'
        source = file('報告.pdf', parent_id=ROOT_ID)
        metadata = file('報告.pdf.parsed.md', 'generated-id', parents=[ROOT_ID], appProperties={
            'folio_source':source['id'], 'folio_role':'parsed',
            'folio_hash':hashlib.sha256(text.encode()).hexdigest()})
        client, _ = client_with(response(status=404), response(metadata))
        posts = []
        def post(url, **kwargs):
            posts.append((url,kwargs))
            return response(metadata)
        client.session.post = post
        first = client.upload_text(source, text, 'parsed', 'generated-id')
        second = client.upload_text(source, text, 'parsed', 'generated-id')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(posts), 1)
        body = posts[0][1]['data'].decode('utf-8')
        self.assertIn(text, body)
        self.assertIn('text/markdown', body)
        self.assertIn('"parents": ["'+ROOT_ID+'"]', body)
        self.assertIn('報告.pdf.parsed.md', body)

    def test_folder_links_ids_and_resource_keys(self):
        for url in (ROOT_ID, f'https://drive.google.com/drive/folders/{ROOT_ID}',
                    f'https://drive.google.com/drive/u/1/folders/{ROOT_ID}?usp=sharing',
                    f'https://drive.google.com/open?id={ROOT_ID}'):
            self.assertEqual(drive.parse_folder(url)[0], ROOT_ID)
        self.assertEqual(drive.parse_folder(f'https://drive.google.com/drive/folders/{ROOT_ID}?resourcekey=key_123')[1], 'key_123')
        for invalid in ('../../file', 'https://evil.example/drive/folders/'+ROOT_ID,
                        'https://drive.google.com.evil.example/drive/folders/'+ROOT_ID,
                        "folder' or trashed = true", 'https://drive.google.com/file/d/'+ROOT_ID):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                drive.parse_folder(invalid)

    def test_pagination_recursion_shared_drive_and_exports(self):
        native = file('課程試算表', mimeType='application/vnd.google-apps.spreadsheet')
        nested = file('講義', 'nestedABCDEFGHIJ', mimeType=drive.FOLDER)
        client, calls = client_with(response(folder()), response({'files': [native], 'nextPageToken': 'page2'}),
                                    response({'files': [nested]}), response({'files': [file('課程.pdf', mimeType='application/pdf')]}))
        scan = client.scan(ROOT_ID, True)
        self.assertEqual([f['name'] for f in scan['files']], ['課程試算表.xlsx', '講義/課程.pdf'])
        self.assertEqual(scan['files'][0]['drive']['export_mime'], drive.EXPORTS[native['mimeType']][1])
        self.assertEqual(calls[1][1]['params']['driveId'], 'sharedDrive123')
        self.assertEqual(calls[2][1]['params']['pageToken'], 'page2')
        self.assertTrue(all(url.startswith(drive.BASE) for url, _ in calls))

    def test_duplicate_and_unsafe_names_are_safe_and_unique(self):
        children = [file('報告', 'id1', mimeType='application/vnd.google-apps.document'),
                    file('報告.docx', 'id2'), file('../CON.txt', 'id3'), file('CON.txt', 'id4')]
        client, _ = client_with(response(folder()), response({'files': children}))
        scan = client.scan(ROOT_ID, False)
        names = [f['name'] for f in scan['files']]
        self.assertEqual(len(set(n.casefold() for n in names)), 4)
        self.assertTrue(all('..' not in PurePosixPath(n).parts for n in names))
        self.assertIn('報告 (2).docx', names)

    def test_shortcuts_permission_restrictions_and_unknown_native_are_listed(self):
        children = [file('捷徑', 'id1', mimeType=drive.SHORTCUT),
                    file('限制.pdf', 'id2', capabilities={'canDownload': False}),
                    file('問卷', 'id3', mimeType='application/vnd.google-apps.form')]
        client, _ = client_with(response(folder()), response({'files': children}))
        scan = client.scan(ROOT_ID, True)
        self.assertEqual(len(scan['files']), 3)
        self.assertTrue(all(f['source_error'] for f in scan['files']))

    def test_non_recursive_scan_records_excluded_folders(self):
        client, calls = client_with(response(folder()), response({'files': [file(), file('子資料夾', 'childabcdefgh', mimeType=drive.FOLDER)]}))
        scan = client.scan(ROOT_ID, False)
        self.assertEqual(len(scan['files']), 1)
        self.assertEqual(scan['excluded_folders'], ['子資料夾/'])
        self.assertEqual(len(calls), 2)

    def test_incomplete_empty_or_over_limit_scan_fails(self):
        for listing in ({'files': [], 'incompleteSearch': True}, {'files': []},
                        {'files': [file(file_id='one'), file(file_id='two')]}):
            client, _ = client_with(response(folder()), response(listing))
            with self.subTest(listing=listing), patch.object(config, 'MAX_FILES', 1), self.assertRaises(ValueError):
                client.scan(ROOT_ID, True)

    def test_provider_error_body_is_not_exposed(self):
        client, _ = client_with(response({'error': {'message': 'SECRET-MUST-NOT-LEAK'}}, status=403))
        with self.assertRaises(ValueError) as error:
            client.scan(ROOT_ID, False)
        self.assertNotIn('SECRET-MUST-NOT-LEAK', str(error.exception))
        self.assertIn('cannot read or download', str(error.exception))


class DriveDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name) / 'source.txt'
        self.content = '完整檔案內容'.encode('utf-8')
        self.metadata = file(size=str(len(self.content)), md5Checksum=hashlib.md5(self.content).hexdigest())
        self.item = {'drive': dict(self.metadata, export_mime=None)}
        self.budget = drive.DownloadBudget()

    def tearDown(self):
        self.temp.cleanup()

    def test_whole_file_download_hash_and_budget(self):
        client, calls = client_with(response(self.metadata), response(content=self.content), response(self.metadata))
        result = client.download(self.item, self.target, self.budget, lambda: False)
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertEqual(result['sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(self.budget.used, len(self.content))
        self.assertEqual(calls[1][1]['params']['alt'], 'media')

    def test_native_sheet_export_uses_xlsx(self):
        item = {'drive': dict(file(mimeType='application/vnd.google-apps.spreadsheet'), export_mime=drive.EXPORTS['application/vnd.google-apps.spreadsheet'][1])}
        client, calls = client_with(response(item['drive']), response(content=b'whole xlsx'), response(item['drive']))
        client.download(item, self.target, self.budget, lambda: False)
        self.assertTrue(calls[1][0].endswith('/export'))
        self.assertIn('spreadsheetml.sheet', calls[1][1]['params']['mimeType'])

    def test_changed_file_during_download_is_not_used(self):
        client, _ = client_with(response(self.metadata), response(content=self.content), response(dict(self.metadata, version='2')))
        with self.assertRaises(ValueError):
            client.download(self.item, self.target, self.budget, lambda: False)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob('*.part')))
        self.assertEqual(self.budget.used, 0)

    def test_limits_cancellation_and_checksum_failure_remove_partial_files(self):
        for scenario in ('size', 'cancel', 'checksum', 'batch'):
            before = dict(self.metadata)
            if scenario == 'checksum':
                before['md5Checksum'] = 'wrong'
            client, _ = client_with(response(before), response(content=self.content), response(before))
            budget = drive.DownloadBudget(config.MAX_EXPANDED if scenario == 'batch' else 0)
            with self.subTest(scenario=scenario), patch.object(config, 'MAX_FILE', 1 if scenario == 'size' else 1024), self.assertRaises(ValueError):
                client.download(self.item, self.target, budget, lambda: scenario == 'cancel')
            self.assertFalse(self.target.exists())
            self.assertFalse(list(self.target.parent.glob('*.part')))


class DriveConfigAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(config, 'DATA', Path(self.temp.name))
        self.patch.start()
        self.env = patch.dict('os.environ', {'GOOGLE_SERVICE_ACCOUNT_FILE': ''})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.patch.stop()
        self.temp.cleanup()

    def test_missing_credentials_is_actionable_without_using_password(self):
        with patch.dict('os.environ', {'GOOGLE_ACCOUNT': 'test@example.com', 'GOOGLE_PASSWORD': 'SECRET-MUST-NOT-LEAK'}):
            result = self.client.get('/api/drive/status').json()
        self.assertFalse(result['configured'])
        self.assertNotIn('SECRET-MUST-NOT-LEAK', json.dumps(result))
        self.assertIn('JSON', result['message'])

    def test_general_account_or_untrusted_token_uri_is_rejected(self):
        path = config.DATA / 'credential.json'
        for data in ({'type': 'authorized_user'}, {'type': 'service_account', 'token_uri': 'https://evil.example/token'}):
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                drive.configure(str(path))
        self.assertFalse((config.DATA / 'drive-settings.json').exists())

    def test_private_key_error_is_redacted(self):
        path = config.DATA / 'credential.json'
        path.write_text(json.dumps({'type': 'service_account', 'token_uri': 'https://oauth2.googleapis.com/token',
                                    'client_email': EMAIL, 'private_key': 'SECRET-MUST-NOT-LEAK'}), encoding='utf-8')
        with self.assertRaises(ValueError) as error:
            drive.configure(str(path))
        self.assertNotIn('SECRET-MUST-NOT-LEAK', str(error.exception))

    def test_config_saves_only_path(self):
        path = config.DATA / 'service.json'
        with patch.object(drive, 'load_credentials', return_value=(None, {'email': EMAIL, 'project_id': 'example'})):
            result = drive.configure(str(path))
        self.assertEqual(result['email'], EMAIL)
        saved = json.loads((config.DATA / 'drive-settings.json').read_text(encoding='utf-8'))
        self.assertEqual(set(saved), {'credentials_path'})

    def test_mutations_require_ui_header_and_zip_upload_is_removed(self):
        self.assertEqual(self.client.post('/api/drive/import', json={'folder': ROOT_ID}).status_code, 403)
        self.assertEqual(self.client.post('/api/jobs', headers={'X-Folio-Request': '1'}).status_code, 405)

    def test_drive_import_creates_batch_without_model_or_download_calls(self):
        scan = {'folder_id': ROOT_ID, 'folder_name': '測試', 'account': EMAIL, 'recursive': True, 'excluded_folders': [],
                'files': [{'name': 'one.txt', 'size': 10, 'source_error': None, 'drive': file('one.txt')}]}
        fake = SimpleNamespace(scan=lambda *args: scan, close=lambda: None)
        with patch.object(drive, 'DriveClient', return_value=fake), patch.object(jobs, 'JOBS', {}), patch.object(jobs, 'api_client') as llm:
            result = self.client.post('/api/drive/import', headers={'X-Folio-Request': '1'}, json={'folder': ROOT_ID})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['status'], 'ready')
            self.assertFalse(result.json()['files'][0]['source_downloaded'])
            library = knowledge.get_library(result.json()['id'])
            self.assertEqual(library['folder_id'], ROOT_ID)
            repeat = self.client.post('/api/drive/import', headers={'X-Folio-Request': '1'}, json={'folder': ROOT_ID})
            self.assertEqual(repeat.status_code, 200)
            self.assertNotEqual(result.json()['id'], repeat.json()['id'])
            self.assertNotEqual(knowledge.collection_name(result.json()['id']), knowledge.collection_name(repeat.json()['id']))
            llm.assert_not_called()

    def test_chat_api_requires_a_library_and_rejects_unknown_scope(self):
        headers = {'X-Folio-Request': '1'}
        self.assertEqual(self.client.post('/api/chats', headers=headers, json={}).status_code, 422)
        self.assertEqual(self.client.post('/api/chats', headers=headers, json={'library_id':'not-imported'}).status_code, 400)
        self.assertEqual(self.client.get('/api/knowledge?library_id=not-imported').status_code, 404)

    def test_build_and_connection_api_require_an_explicit_library(self):
        headers = {'X-Folio-Request': '1'}
        self.assertEqual(self.client.post('/api/knowledge/build', headers=headers, json={}).status_code, 422)
        self.assertEqual(self.client.post('/api/knowledge/test', headers=headers).status_code, 422)


if __name__ == '__main__':
    unittest.main(verbosity=2)
