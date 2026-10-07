import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from qdrant_client import QdrantClient
from fastapi.testclient import TestClient

from backend import config, knowledge, jobs, chat
from backend.app import app


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = QdrantClient(':memory:')
        self.patches = [patch.object(config, 'DATA', self.root), patch.object(config, 'EMBEDDING_DIMENSIONS', 8),
                        patch.object(knowledge, 'STATE', {'schema_version': 2, 'libraries': {}, 'documents': {}, 'builds': []}),
                        patch.object(knowledge, 'DB', self.db)]
        for p in self.patches:
            p.start()
        self.library('a')
        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        def embeddings(**kwargs):
            inputs = kwargs['input'] if isinstance(kwargs['input'], list) else [kwargs['input']]
            return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1.0]+[0.0]*7) for i in range(len(inputs))])
        self.client.embeddings.create.side_effect = embeddings

    def tearDown(self):
        self.db.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def library(self, n):
        job = {'id': n, 'name': '資料集'+n, 'created_at': '2026-09-30',
               'source': {'type': 'google_drive', 'folder_id': 'same-folder', 'url': 'https://drive.google.com/drive/folders/same-folder'}}
        knowledge.register_library(job)
        return job

    def document(self, n, body='Qdrant 混合搜尋 RRF 與完整文件評分。\n', library_id='a'):
        path = self.root / f'{library_id}-{n}.md'
        path.write_text(body, encoding='utf-8')
        doc = {'id': library_id + str(n), 'library_id': library_id, 'name': f'資料{n}.pdf', 'text_path': str(path),
               'content_hash': hashlib.sha256(body.encode()).hexdigest(), 'source': {'id': f'drive-{n}'},
               'media': False, 'assets': {}, 'index_status': 'pending'}
        knowledge.STATE['documents'][doc['id']] = doc
        return doc

    def test_unicode_chunks_cover_all_text_with_filename_and_overlap(self):
        source = ('中文資料𠮷👩🏽‍💻。表格|12.34|Qdrant retrieval|\n' * 125) + '最後尾端'
        chunks = knowledge.chunk_text('目錄/完整檔名.pdf', source)
        self.assertGreater(len(chunks), 2)
        tokens = knowledge.ENCODER.encode(source)
        covered = set()
        for chunk in chunks:
            self.assertLessEqual(chunk['content_tokens'], 512)
            self.assertTrue(chunk['text'].startswith('Filename: 目錄/完整檔名.pdf\n\n'))
            self.assertNotIn('\ufffd', chunk['body'])
            self.assertEqual(knowledge.ENCODER.decode(tokens[chunk['start_token']:chunk['end_token']]), chunk['body'])
            covered.update(range(chunk['start_token'], chunk['end_token']))
        self.assertEqual(covered, set(range(len(tokens))))
        self.assertTrue(chunks[-1]['body'].endswith('最後尾端'))
        for previous, current in zip(chunks, chunks[1:]):
            self.assertGreaterEqual(previous['end_token'] - current['start_token'], 128)
            self.assertLess(previous['end_token'] - current['start_token'], 140)

    def test_native_rrf_limits_chunks_deduplicates_files_and_keeps_failed_grade(self):
        for n in range(5):
            knowledge.index_document(self.document(n), self.client)
        traces = []
        def judge(question, doc, usage_scope=None, judge_provider='jev'):
            self.assertEqual(question, '完整的使用者需求')
            if doc['id'] == 'a0':
                raise ValueError('全文超限')
            return {'judge_status': 'completed', 'score': 2.5, 'grade': 3}
        with patch.object(knowledge, 'api_client', return_value=self.client), patch.object(knowledge, 'judge_document', side_effect=judge) as evaluator:
            output = knowledge.search_files('Qdrant', 100, lambda k, d: traces.append((k, d)), question='完整的使用者需求', library_id='a')
            self.assertEqual(len(output['files']), 5)
            self.assertEqual(evaluator.call_count, 5)
            self.assertIsNone(output['file_limit'])
            failed = next(f for f in output['files'] if f['document_id'] == 'a0')
            self.assertIsNone(failed['score'])
            self.assertIsNone(failed['grade'])
            self.assertEqual(output['failed_evaluations'], 1)
            self.assertEqual(len(failed['links']), 2)
            self.assertEqual(len(traces[1][1]['chunks']), 5)
            limited = knowledge.search_files('Qdrant', 2, question='完整的使用者需求', library_id='a')
            self.assertEqual(limited['recalled_chunks'], 2)
            self.assertLessEqual(len(limited['files']), 2)

    def test_direct_search_api_preserves_query_ranks_unique_files_without_agents(self):
        self.library('b')
        for n in range(3):
            doc = self.document(n, 'Orion 乘員與任務天數。\n' * (150 if n == 0 else 1))
            doc['source'].update(modifiedTime=f'2026-09-{10+n:02d}T12:34:56.123Z', version=str(n+1))
            knowledge.index_document(doc, self.client)
        knowledge.index_document(self.document(1, '另一組 Orion 文件', library_id='b'), self.client)
        self.client.embeddings.create.reset_mock()
        query = 'Orion 乘員與任務天數，請保留原始單位。'

        def judge(question, doc, usage_scope=None, judge_provider='jev'):
            self.assertEqual(question, query)
            self.assertEqual(doc['library_id'], 'a')
            if doc['id'] == 'a1':
                raise ValueError('評分暫時無法完成')
            return {'judge_status': 'completed', 'score': 2.9 if doc['id'] == 'a0' else 0.1,
                    'grade': 3 if doc['id'] == 'a0' else 0, 'label': '測試等級'}

        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(knowledge, 'api_client', return_value=self.client), \
                patch.object(knowledge, 'judge_document', side_effect=judge) as scorer, \
                patch.object(chat, 'create') as create_agent, patch.object(chat, 'send') as send_agent:
            response = client.post('/api/search', headers={'X-Folio-Request': '1'},
                                   json={'library_id': 'a', 'query': f'  {query}\n', 'top_k': 100})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result['mode'], 'search')
        self.assertEqual(result['query'], query)
        self.assertEqual(result['question'], query)
        self.assertEqual(result['library_id'], 'a')
        self.assertEqual([f['document_id'] for f in result['files']], ['a0', 'a2', 'a1'])
        # A newer date must not silently override pure-search relevance ordering.
        for file in result['files']:
            n = int(file['document_id'][1:])
            self.assertEqual(file['drive_modified_at'], f'2026-09-{10+n:02d}T12:34:56.123Z')
            self.assertEqual(file['source_version'], str(n+1))
        self.assertEqual(len(result['files']), 3)
        self.assertGreater(result['recalled_chunks'], len(result['files']))
        self.assertEqual(result['failed_evaluations'], 1)
        self.assertIsNone(result['files'][-1]['score'])
        self.assertEqual(result['files'][-1]['judge_status'], 'failed')
        self.assertEqual(len(result['files'][0]['links']), 2)
        self.assertEqual(scorer.call_count, 3)
        self.client.embeddings.create.assert_called_once_with(model=config.EMBEDDING_MODEL, dimensions=8, input=query)
        self.client.responses.create.assert_not_called()
        self.client.beta.agents.sessions.create.assert_not_called()
        create_agent.assert_not_called()
        send_agent.assert_not_called()

    def test_public_document_uses_source_date_without_fabricating_missing_dates(self):
        doc = self.document(1)
        doc.update(job_created_at='2026-10-01T00:00:00Z', updated_at='2026-10-02T00:00:00Z')
        self.assertIsNone(knowledge.public_document(doc)['drive_modified_at'])
        doc['source'].update(modifiedTime='2026-09-03T01:02:03.456Z', version='7')
        result = knowledge.public_document(doc)
        self.assertEqual(result['drive_modified_at'], '2026-09-03T01:02:03.456Z')
        self.assertEqual(result['source_version'], '7')
        self.assertNotIn('source', result)
        self.assertEqual(doc['source']['modifiedTime'], result['drive_modified_at'])

    def test_direct_search_validates_input_scope_and_local_header_before_api_calls(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(knowledge, 'api_client') as provider, patch.object(knowledge, 'judge_document') as scorer:
            self.assertEqual(client.post('/api/search', json={'library_id': 'a', 'query': 'Orion'}).status_code, 403)
            invalid = [({}, 422), ({'query': 'Orion'}, 422), ({'library_id': 'a'}, 422),
                       ({'library_id': 'a', 'query': ' '}, 400),
                       ({'library_id': 'unknown', 'query': 'Orion'}, 400),
                       ({'library_id': 'a', 'query': 'Orion'}, 400),
                       ({'library_id': 'a', 'query': 'x' * 8001}, 422)]
            invalid += [({'library_id': 'a', 'query': 'Orion', 'top_k': n}, 422)
                        for n in (0, 501, 1.5, True, '100')]
            for body, status in invalid:
                with self.subTest(body_keys=list(body), expected=status):
                    response = client.post('/api/search', headers={'X-Folio-Request': '1'}, json=body)
                    self.assertEqual(response.status_code, status)
            provider.assert_not_called()
            scorer.assert_not_called()

    def test_direct_search_provider_errors_do_not_expose_exception_content(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(knowledge, 'search_files', side_effect=RuntimeError('SECRET-MUST-NOT-LEAK')):
            response = client.post('/api/search', headers={'X-Folio-Request': '1'}, json={'library_id': 'a', 'query': 'Orion'})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('SECRET-MUST-NOT-LEAK', response.text)

    def test_direct_search_returns_empty_results_without_creating_a_chat(self):
        knowledge.index_document(self.document(1), self.client)
        empty = SimpleNamespace(query_points=lambda *args, **kwargs: SimpleNamespace(points=[]))
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(knowledge, 'api_client', return_value=self.client), \
                patch.object(knowledge, 'collection', return_value=empty), \
                patch.object(knowledge, 'judge_document') as scorer, patch.object(chat, 'create') as create_agent:
            response = client.post('/api/search', headers={'X-Folio-Request': '1'}, json={'library_id': 'a', 'query': 'Orion'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['files'], [])
        self.assertEqual(response.json()['recalled_chunks'], 0)
        scorer.assert_not_called()
        create_agent.assert_not_called()

    def test_embedding_failure_does_not_publish_partial_revision(self):
        doc = self.document(1, 'word ' * 26000)
        normal = self.client.embeddings.create.side_effect
        calls = []
        def fail_second(**kwargs):
            calls.append(1)
            if len(calls) > 1:
                raise ValueError('interrupted')
            return normal(**kwargs)
        self.client.embeddings.create.side_effect = fail_second
        with self.assertRaises(ValueError):
            knowledge.index_document(doc, self.client)
        self.assertNotEqual(doc['index_status'], 'ready')
        self.assertNotIn('revision', doc)
        self.assertEqual(self.db.count(knowledge.collection_name('a')).count, 64)
        self.client.embeddings.create.side_effect = normal
        knowledge.index_document(doc, self.client)
        self.assertEqual(doc['index_status'], 'ready')
        self.assertEqual(self.db.count(knowledge.collection_name('a')).count, doc['chunk_count'])

    def test_separate_collections_cannot_recall_or_grade_other_library(self):
        self.library('b')
        first = self.document(1, '第一組 Qdrant Orion', 'a')
        second = self.document(1, '第二組 Qdrant Orion', 'b')
        for doc in [first, second]:
            knowledge.index_document(doc, self.client)
        self.assertNotEqual(knowledge.collection_name('a'), knowledge.collection_name('b'))
        self.assertEqual(self.db.count(knowledge.collection_name('a')).count, 1)
        self.assertEqual(self.db.count(knowledge.collection_name('b')).count, 1)
        with patch.object(knowledge, 'api_client', return_value=self.client), patch.object(knowledge, 'judge_document', return_value={'judge_status':'completed','score':3,'grade':3}) as judge:
            for library_id, expected in [('a', first), ('b', second)]:
                result = knowledge.search_files('Orion Qdrant', library_id=library_id)
                self.assertEqual([f['document_id'] for f in result['files']], [expected['id']])
                self.assertEqual(judge.call_args.args[1]['library_id'], library_id)
                self.assertEqual(result['library_id'], library_id)
        self.assertEqual(len(knowledge.status('a')['documents']), 1)
        with self.assertRaises(ValueError):
            knowledge.get_document(second['id'], library_id='a')
        self.library('empty')
        with self.assertRaisesRegex(ValueError, 'Finish indexing'):
            knowledge.search_files('Orion', library_id='empty')
        with self.assertRaises(ValueError):
            knowledge.search_files('Orion', library_id='unknown')

    def test_same_source_in_different_imports_has_independent_ids_and_rebuilds(self):
        batch_a, batch_b = self.library('a'), self.library('b')
        for batch in (batch_a, batch_b):
            batch['source']['account'] = 'test@example.com'
            batch['files'] = [{'id':'0001','name':'shared.pdf','status':'completed','kind':'document','drive':{'id':'same-file','version':'1'}}]
            batch['files'][0]['drive']['modifiedTime'] = '2026-09-01T00:00:00Z' if batch['id'] == 'a' else '2026-09-02T00:00:00Z'
            root = self.root / batch['id'] / '0001'
            root.mkdir(parents=True)
            (root/'result.md').write_text('Same contents', encoding='utf-8')
        with patch.object(jobs, 'JOBS', {'a':batch_a,'b':batch_b}), patch.object(jobs, 'write_markdown'), patch.object(jobs, 'update', side_effect=lambda job, target, **values: target.update(values)), patch.object(knowledge, 'sync_storage'), patch.object(knowledge, 'api_client', return_value=self.client):
            build = {'job_id':None, 'completed':0, 'errors':[]}
            knowledge.run_build(build)
            self.assertEqual(build['completed'], 2)
            first_id, second_id = batch_a['files'][0]['knowledge_id'], batch_b['files'][0]['knowledge_id']
            self.assertNotEqual(first_id, second_id)
            before = knowledge.get_document(second_id)
            self.assertEqual(knowledge.public_document(knowledge.get_document(first_id))['drive_modified_at'], '2026-09-01T00:00:00Z')
            self.assertEqual(knowledge.public_document(before)['drive_modified_at'], '2026-09-02T00:00:00Z')
            calls = self.client.embeddings.create.call_count
            (self.root/'a'/'0001'/'result.md').write_text('Changed first library only', encoding='utf-8')
            knowledge.run_build({'job_id':'a', 'completed':0, 'errors':[]})
            self.assertEqual(knowledge.get_document(second_id), before)
            self.assertEqual(self.client.embeddings.create.call_count, calls+1)
            knowledge.run_build({'job_id':'a', 'completed':0, 'errors':[]})
            self.assertEqual(self.client.embeddings.create.call_count, calls+1)
            for library_id in ['a','b']:
                self.assertEqual(self.db.count(knowledge.collection_name(library_id)).count, 1)

    def test_legacy_catalog_migration_is_repeatable_and_retains_assets(self):
        batch = self.library('a')
        old = self.document(1)
        old.update(job_id='a', account='test@example.com', index_status='ready', revision='old-revision',
                   assets={'parsed': {'id':'uploaded-md', 'status':'uploaded'}})
        old.pop('library_id')
        old_state = {'documents':{old['id']:old}, 'builds':[]}
        (self.root/'knowledge.json').write_text(json.dumps(old_state), encoding='utf-8')
        with patch.object(jobs, 'JOBS', {'a':batch}):
            self.assertTrue(knowledge.startup())
            migrated = next(iter(knowledge.STATE['documents'].values()))
            self.assertEqual(migrated['library_id'], 'a')
            self.assertEqual(migrated['index_status'], 'pending')
            self.assertNotIn('revision', migrated)
            self.assertEqual(migrated['assets']['parsed']['id'], 'uploaded-md')
            self.assertEqual(json.loads((self.root/'knowledge-before-libraries.json').read_text(encoding='utf-8')), old_state)
            self.assertFalse(knowledge.startup())
            self.assertEqual(len(knowledge.STATE['documents']), 1)

    def test_jev_receives_complete_markdown_and_windows_newlines_are_canonical(self):
        text = '# 表格\n| A | B |\n|---|---|\n|1|2|\n' + '正文\n' * 1800 + '\n最後一行'
        doc = self.document(1, text)
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(body['state']['full_document'], text)
            self.assertEqual(body['state']['question'], '問題')
            self.assertEqual(len(body['questions']['relevance']['criteria']), 4)
            return httpx.Response(200, json={'model': 'jev-1.13.0', 'answers': {'relevance': {
                'type': 'score', 'score': 2.5, 'confidence': .85,
                'probabilities': {'0': 0, '1': .1, '2': .3, '3': .6}}}})
        with patch.dict('os.environ', {'JEV_API_KEY': 'test'}):
            result = knowledge.judge_document('問題', doc, httpx.MockTransport(handle))
        self.assertEqual(result['input_chars'], len(text))
        self.assertEqual(result['grade'], 3)
        self.assertEqual(result['score'], 2.5)

    def test_changed_markdown_and_invalid_probabilities_are_rejected(self):
        doc = self.document(1)
        with patch.dict('os.environ', {'JEV_API_KEY': 'test'}):
            with self.assertRaises(ValueError):
                knowledge.judge_document('question', doc, httpx.MockTransport(lambda r: httpx.Response(200, json={'answers': {'relevance': {'type':'score','score':3,'confidence':1,'probabilities':{'3':1}}}})))
            Path(doc['text_path']).write_text('new content', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'changed'):
                knowledge.judge_document('question', doc)

    def test_decisions_full_document_and_scope_hash_are_preserved(self):
        from test_grading import decisions_response
        text = '全文\n' * 18000 + '完整結尾'
        doc = self.document(1, text)
        def handler(request):
            self.assertEqual(request.url.path, '/v1/decisions')
            self.assertEqual(json.loads(json.loads(request.content)['input'])['full_document'], text)
            return httpx.Response(200, json=decisions_response())
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'fake', 'FOLIO_OPENAI_DIRECT_KEY': '',
                                      'OPENAI_BASE_URL': 'https://api.openai.com/v1'}):
            result = knowledge.judge_document('完整需求', doc, httpx.MockTransport(handler), judge_provider='openai_decisions')
            self.assertEqual(result['judge_provider'], 'openai_decisions')
            self.assertEqual(result['model'], 'gpt-6-luna')
            self.assertEqual(result['read_scope'], 'full_document')
            self.assertEqual(result['content_hash'], doc['content_hash'])
            Path(doc['text_path']).write_text('已變更', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Markdown changed'):
                knowledge.judge_document('完整需求', doc, httpx.MockTransport(handler), judge_provider='openai_decisions')

    def test_direct_search_passes_selected_provider_to_every_deduplicated_file(self):
        for n in range(2):
            knowledge.index_document(self.document(n, 'Orion\n' * 500), self.client)
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(knowledge, 'api_client', return_value=self.client), \
                patch.object(knowledge, 'judge_document', return_value={'judge_status': 'completed', 'score': 2.5, 'grade': 3}) as judge:
            result = client.post('/api/search', headers={'X-Folio-Request': '1'}, json={
                'library_id': 'a', 'query': 'Orion', 'judge_provider': 'openai_decisions'}).json()
        self.assertEqual(result['judge_provider'], 'openai_decisions')
        self.assertEqual(result['judge_model'], 'gpt-6-luna')
        self.assertEqual(len(result['files']), 2)
        self.assertEqual(judge.call_count, 2)
        self.assertTrue(all(c.kwargs['judge_provider'] == 'openai_decisions' for c in judge.call_args_list))
        self.assertTrue(all(f['judge_provider'] == 'openai_decisions' for f in result['files']))


if __name__ == '__main__':
    unittest.main()
