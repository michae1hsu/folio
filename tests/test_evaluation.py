import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from backend import chat, config, evaluation, gcs, knowledge, usage
from backend.app import app


def scores(value=2.5):
    return {key: {'score': value, 'confidence': .8,
                  'probabilities': {'0': 0, '1': 0, '2': .5, '3': .5}, 'grade': 2}
            for key in evaluation.CRITERIA}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for item in (patch.object(config, 'DATA', self.root), patch.object(chat, 'CHATS', {}),
                     patch.object(knowledge, 'STATE', {'libraries': {'a': {'id': 'a', 'name': 'A'},
                         'b': {'id': 'b', 'name': 'B'}}, 'documents': {}, 'builds': []}),
                     patch.object(evaluation.WORKERS, 'submit'), patch.object(chat.WORKERS, 'submit')):
            item.start()
            self.addCleanup(item.stop)
        self.c = chat.CHATS[chat.create('a', system_prompt='請用繁體中文，核對計算。')['id']]
        self.c.update(session_id='s1', turn_id='t1', status='completed', session_prompt_revision=1)
        self.c['messages'] = [
            {'id': 'u1', 'role': 'user', 'text': '2 加 2 是多少？', 'turn_id': 't1'},
            {'id': 'm1', 'role': 'assistant', 'phase': 'final_answer', 'text': '4。', 'turn_id': 't1'},
        ]
        self.c['submissions']['u1'] = {'query': self.c['messages'][0]['text'], 'turn_id': 't1',
            'session_id': 's1', 'system_prompt': self.c['system_prompt'], 'prompt_revision': 1,
            'effective_prompt': chat.effective_prompt(self.c, knowledge.get_library('a')), 'top_k': 100}
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'X-Folio-Request': '1'}

    def capture(self, **kwargs):
        with chat.LOCK:
            return evaluation.capture(self.c, **kwargs)

    def state(self, record):
        return json.loads((evaluation.folder(record['id']) / 'snapshot.json').read_text(encoding='utf-8'))

    def post(self, path, value=None):
        return self.client.post(path, json=value or {}, headers=self.headers)

    def test_whole_run_for_general_question_with_no_document(self):
        record = self.capture()
        snapshot = self.state(record)
        sources, limits = evaluation.source_material(snapshot)
        state = {**snapshot, 'source_material': sources, 'verification_limits': limits}
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'model': config.JEV_MODEL, 'answers': {
                k: {'type': 'score', **v} for k, v in scores().items()},
                'usage': {'input_tokens': 100, 'output_tokens': 4}})
        with patch.dict(os.environ, {'JEV_API_KEY': 'fake-test-key'}):
            result = evaluation.judge(record, state, httpx.MockTransport(handle))
        self.assertEqual(result['overall']['score'], 2.5)
        self.assertEqual(requests[0]['state']['current_user_query'], '2 加 2 是多少？')
        self.assertEqual(len(requests[0]['state']['conversation_context']), 2)
        self.assertIn('核對計算', requests[0]['state']['agent_system_prompt'])
        self.assertEqual(requests[0]['state']['source_material'], [])
        self.assertIn('General questions', requests[0]['questions']['overall']['instructions'])
        self.assertTrue(all(len(q['criteria']) == 4 for q in requests[0]['questions'].values()))
        report = usage.report(chat_id=self.c['id'], turn_id='t1')
        self.assertEqual(report['groups'][0]['operation'], 'answer_eval')
        self.assertEqual(report['summary']['total_tokens'], 104)

    def test_decisions_eval_snapshots_provider_runs_all_dimensions_and_logs_actual_json(self):
        from test_grading import decisions_response
        evaluation.configure({'judge_provider': 'openai_decisions'})
        record = self.capture()
        evaluation.configure({'judge_provider': 'jev'})
        self.assertEqual(record['config']['judge_provider'], 'openai_decisions')
        self.assertEqual(record['config']['judge_model'], 'gpt-6-luna')
        state = self.state(record)
        names = list(evaluation.CRITERIA)
        raw = decisions_response(names)
        requests = []
        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertEqual(request.url.path, '/v1/decisions')
            self.assertEqual(json.loads(body['input']), state)
            self.assertEqual([q['name'] for q in body['questions']], names)
            self.assertTrue(all(len(q['levels']) == 4 for q in body['questions']))
            return httpx.Response(200, json=raw)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake', 'FOLIO_OPENAI_DIRECT_KEY': '',
                                     'OPENAI_BASE_URL': 'https://api.openai.com/v1'}):
            result = evaluation.judge(record, state, httpx.MockTransport(handler))
        self.assertEqual(set(result), set(names))
        root = evaluation.folder(record['id'])
        self.assertEqual(json.loads((root / 'decisions-request.json').read_text()), requests[0])
        self.assertEqual(json.loads((root / 'decisions-response.json').read_text()), raw)
        self.assertFalse((root / 'jev-request.json').exists())
        report = usage.report(chat_id=self.c['id'], turn_id='t1')
        self.assertAlmostEqual(report['summary']['cost_usd'], .0001)
        self.assertEqual(report['entries'][0]['provider'], 'openai_decisions')

    def test_selected_decisions_low_score_triggers_optimization_and_retry_keeps_judgment(self):
        from test_grading import decisions_response
        evaluation.configure({'judge_provider': 'openai_decisions'})
        record = self.capture()
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=decisions_response(
            tuple(evaluation.CRITERIA), probabilities=(0, .8, .2, 0))))
        actual_judge = evaluation.judge
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake', 'FOLIO_OPENAI_DIRECT_KEY': '',
                                     'OPENAI_BASE_URL': 'https://api.openai.com/v1'}), \
                patch.object(evaluation, 'judge', side_effect=lambda r, s: actual_judge(r, s, transport)) as judge, \
                patch.object(evaluation, 'optimize', side_effect=ValueError('建議失敗')):
            evaluation.run(record['id'])
        result = evaluation.get(record['id'])
        self.assertAlmostEqual(result['score'], 1.2)
        self.assertFalse(result['passed'])
        self.assertEqual(result['optimization']['status'], 'failed')
        self.assertEqual(judge.call_count, 1)
        evaluation.retry(record['id'])
        with patch.object(evaluation, 'judge') as judge, \
                patch.object(evaluation, 'optimize', return_value={'summary': '建議完成'}) as optimizer:
            evaluation.run(record['id'])
        judge.assert_not_called()
        optimizer.assert_called_once()
        self.assertEqual(evaluation.get(record['id'])['config']['judge_provider'], 'openai_decisions')

    def test_settings_select_provider_validate_and_preserve_omitted_fields(self):
        body = {**evaluation.DEFAULTS, 'judge_provider': 'openai_decisions'}
        response = self.client.patch('/api/evaluation-settings', headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['judge_model'], 'gpt-6-luna')
        body.pop('judge_provider')
        self.client.patch('/api/evaluation-settings', headers=self.headers, json=body)
        self.assertEqual(evaluation.settings()['judge_provider'], 'openai_decisions')
        for path, body in [('/api/evaluation-settings', {**body, 'judge_provider':'invalid'}),
                           ('/api/search', {'library_id':'a','query':'q','judge_provider':'invalid'}),
                           ('/api/chats', {'library_id':'a','judge_provider':'invalid'})]:
            with self.subTest(path=path):
                response = (self.client.patch(path, headers=self.headers, json=body)
                            if path == '/api/evaluation-settings' else self.post(path, body))
                self.assertEqual(response.status_code, 422)
        created = self.post('/api/chats', {'library_id':'a','judge_provider':'openai_decisions'})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()['judge_provider'], 'openai_decisions')

    def test_snapshots_are_immutable_and_replays_do_not_queue_twice(self):
        record = self.capture()
        self.c['submissions']['u1']['effective_prompt'] = 'changed'
        self.c['messages'][0]['text'] = 'changed query'
        self.c['top_k'] = 5
        evaluation.configure({'threshold': 3})
        repeated = self.capture()
        self.assertEqual(repeated['id'], record['id'])
        evaluation.WORKERS.submit.assert_called_once()
        snapshot = self.state(record)
        self.assertNotEqual(snapshot['agent_system_prompt'], 'changed')
        self.assertEqual(snapshot['current_user_query'], '2 加 2 是多少？')
        self.assertEqual(snapshot['retrieval_settings']['top_k'], 100)
        self.assertEqual(repeated['config']['threshold'], 2)

    def test_source_versions_are_pinned_and_full_text_includes_tail(self):
        text = '# 全文\n' + '資料' * 12000 + '\n最後證據'
        digest = hashlib.sha256(text.encode()).hexdigest()
        asset = {'provider': 'gcs', 'status': 'uploaded', 'generation': '42', 'sha256': digest}
        doc = {'id': 'd1', 'library_id': 'a', 'name': '完整文件.pdf', 'media': False,
               'content_hash': digest, 'assets': {'parsed': asset}, 'source_version': 'v1',
               'source': {'modifiedTime': '2026-09-03T01:02:03Z'}}
        chat.remember_evidence(self.c, doc)
        record = self.capture()
        doc['assets']['parsed']['generation'] = '99'
        doc['source']['modifiedTime'] = '2026-10-02T00:00:00Z'
        with patch.object(gcs, 'read', return_value=text.encode()) as read:
            sources, _ = evaluation.source_material(self.state(record))
        self.assertEqual(read.call_args.args[0]['generation'], '42')
        self.assertEqual(sources[0]['full_text'], text)
        self.assertEqual(sources[0]['drive_modified_at'], '2026-09-03T01:02:03Z')
        self.assertEqual(sources[0]['source_version'], 'v1')
        self.assertTrue(sources[0]['full_text'].endswith('最後證據'))
        with patch.object(gcs, 'read', return_value=b'changed'):
            with self.assertRaises(ValueError):
                evaluation.source_material(self.state(record))

    def test_foreign_library_and_changed_original_never_reach_judge(self):
        snapshot = self.state(self.capture())
        snapshot['source_refs'] = {'d': {'document': {'library_id': 'b'}, 'variants': ['parsed']}}
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            evaluation.source_material(snapshot)
        path = chat.folder(self.c) / 'original.txt'
        path.write_text('changed', encoding='utf-8')
        snapshot.update(source_refs={}, original_files=[{'variant': 'original', 'path': '/workspace/inputs/original.txt',
            'sha256': 'wrong', 'name': '原始文字.txt'}])
        with self.assertRaisesRegex(ValueError, 'checksum'):
            evaluation.source_material(snapshot)

    def test_original_pdf_text_preserves_every_page_and_reports_image_limit(self):
        import pymupdf
        path = chat.folder(self.c) / 'original.pdf'
        with pymupdf.open() as pdf:
            for text in ('First page', 'Final page'):
                pdf.new_page().insert_text((50, 50), text)
            pdf.save(path)
        snapshot = self.state(self.capture())
        snapshot['original_files'] = [{'document_id': 'd', 'variant': 'original', 'path': '/workspace/inputs/original.pdf',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'name': '原始文件.pdf'}]
        sources, limits = evaluation.source_material(snapshot)
        self.assertIn('First page', sources[0]['full_text'])
        self.assertIn('Final page', sources[0]['full_text'])
        self.assertTrue(any('without direct visual verification' in s for s in limits))

    def test_historical_manual_eval_excludes_future_context_sources_and_logs(self):
        self.c['submissions'] = {}
        self.c['messages'] += [{'id': 'u2', 'role': 'user', 'text': '未來問題', 'turn_id': 't2'},
                              {'id': 'm2', 'role': 'assistant', 'phase': 'final_answer', 'text': '未來答案', 'turn_id': 't2'}]
        self.c['turn_id'] = 't2'
        self.c['trace'] = [{'turn_id': 't1', 'context_index': 1, 'data': {'output': 'old'}},
                           {'turn_id': 't2', 'context_index': 3, 'data': {'output': 'future'}}]
        self.c['evidence_refs'] = {'future': {'context_index': 2, 'variants': ['parsed'], 'document': {'id': 'future'}},
                                  'old': {'context_index': 0, 'variants': ['parsed', 'original'],
                                      'variant_indices': {'parsed': 0, 'original': 3}, 'document': {'id': 'old'}}}
        snapshot = self.state(self.capture(turn_id='t1', automatic=False))
        self.assertEqual(len(snapshot['conversation_context']), 2)
        self.assertEqual(snapshot['current_user_query'], '2 加 2 是多少？')
        self.assertIsNone(snapshot['agent_system_prompt'])
        self.assertEqual(snapshot['prompt_origin'], 'historical_unrecorded')
        self.assertEqual(list(snapshot['source_refs']), ['old'])
        self.assertEqual(snapshot['source_refs']['old']['variants'], ['parsed'])
        self.assertEqual(len(snapshot['execution_log']), 1)

    def test_bad_score_response_still_records_actual_usage(self):
        record = self.capture()
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={
            'answers': {}, 'usage': {'input_tokens': 900, 'output_tokens': 10}}))
        with patch.dict(os.environ, {'JEV_API_KEY': 'fake'}), self.assertRaises(ValueError):
            evaluation.judge(record, self.state(record), transport)
        report = usage.report(chat_id=self.c['id'], turn_id='t1')
        self.assertEqual(report['summary']['total_tokens'], 910)
        self.assertIsNotNone(report['summary']['cost_usd'])

    def test_invalid_probabilities_and_nonfinite_scores_are_rejected(self):
        valid = {'answers': {'overall': {'type': 'score', **scores()['overall']}}}
        for field, value in [('score', float('nan')), ('score', True), ('confidence', 2),
                             ('probabilities', {'0': 0, '1': 0, '2': 0, '3': .2})]:
            with self.subTest(field=field, value=value):
                bad = copy.deepcopy(valid)
                bad['answers']['overall'][field] = value
                with self.assertRaises(ValueError):
                    evaluation.parse_scores(bad, ['overall'])

    def test_http_error_is_private_and_unknown_cost_remains_unknown(self):
        record = self.capture()
        transport = httpx.MockTransport(lambda r: httpx.Response(413, text='secret provider payload'))
        with patch.dict(os.environ, {'JEV_API_KEY': 'fake'}), self.assertRaisesRegex(ValueError, 'No truncation') as error:
            evaluation.judge(record, self.state(record), transport)
        self.assertNotIn('secret', str(error.exception))
        cost = evaluation.costs(record)['evaluation']
        self.assertIsNone(cost['cost_usd'])
        self.assertEqual(cost['unknown_costs'], 1)

    def test_low_score_calls_optimizer_once_boundary_two_does_not(self):
        for value in (1.99, 2, 3):
            with self.subTest(score=value):
                self.c['turn_id'] = f't{value}'
                self.c['messages'][-1]['turn_id'] = self.c['turn_id']
                record = self.capture()
                with patch.object(evaluation, 'judge', return_value=scores(value)) as judge, \
                     patch.object(evaluation, 'optimize', return_value={'summary': '建議', 'recommendations': [],
                         'proposed_system_prompt': '新建議', 'proposed_top_k': None}) as optimize:
                    evaluation.run(record['id'])
                    evaluation.run(record['id'])
                self.assertEqual(judge.call_count, 1)
                self.assertEqual(optimize.call_count, 1 if value < 2 else 0)
                saved = evaluation.get(record['id'])
                self.assertEqual(saved['passed'], value >= 2)
                self.assertEqual(self.c['system_prompt'], '請用繁體中文，核對計算。')

    def test_optimizer_failure_keeps_score_retry_does_not_repeat_jev(self):
        record = self.capture()
        with patch.object(evaluation, 'judge', return_value=scores(1)) as judge, \
             patch.object(evaluation, 'optimize', side_effect=ValueError('建議失敗')):
            evaluation.run(record['id'])
        saved = evaluation.get(record['id'])
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['score'], 1)
        self.assertEqual(saved['optimization']['status'], 'failed')
        evaluation.retry(record['id'])
        with patch.object(evaluation, 'judge') as second_judge, patch.object(evaluation, 'optimize', return_value={'summary': '完成'}) as optimizer:
            evaluation.run(record['id'])
        second_judge.assert_not_called()
        optimizer.assert_called_once()
        with self.assertRaises(ValueError):
            evaluation.retry(record['id'])

    def test_optimizer_uses_configured_model_full_state_and_structured_output(self):
        record = self.capture()
        record['dimensions'] = scores(1)
        result = {'summary': '計算未驗證', 'recommendations': [{'category': 'answering', 'observation': '算錯',
                  'change': '核對算式', 'validation': '以算術題對照'}],
                  'proposed_system_prompt': '先核對計算，再回覆。', 'proposed_top_k': None}
        response = SimpleNamespace(id='r1', model=config.REPORT_MODEL, status='completed', output_text=json.dumps(result),
                                   usage={'input_tokens': 100, 'output_tokens': 30}, service_tier='default')
        client = MagicMock()
        client.__enter__.return_value.responses.create.return_value = response
        with patch.object(evaluation, 'api_client', return_value=client):
            value = evaluation.optimize(record, self.state(record))
        call = client.__enter__.return_value.responses.create.call_args.kwargs
        self.assertEqual(call['model'], config.REPORT_MODEL)
        self.assertFalse(call['store'])
        self.assertEqual(json.loads(call['input'])['run']['final_answer'], '4。')
        self.assertTrue(call['text']['format']['strict'])
        self.assertEqual(value['proposed_system_prompt'], result['proposed_system_prompt'])
        self.assertEqual(usage.report()['groups'][0]['operation'], 'optimization')
        self.assertTrue((evaluation.folder(record['id']) / 'optimization-response.json').exists())

    def test_failed_evidence_does_not_call_provider(self):
        record = self.capture()
        with patch.object(evaluation, 'source_material', side_effect=ValueError('版本不符')), patch.object(evaluation, 'judge') as judge:
            evaluation.run(record['id'])
        judge.assert_not_called()
        self.assertEqual(evaluation.get(record['id'])['status'], 'failed')
        self.assertIsNone(evaluation.get(record['id'])['score'])

    def test_incomplete_optimizer_response_is_logged_and_billed_before_validation(self):
        record = self.capture()
        response = SimpleNamespace(id='incomplete1', model=config.REPORT_MODEL, status='incomplete',
                                   output_text='', usage={'input_tokens': 500, 'output_tokens': 30}, service_tier='default')
        client = MagicMock()
        client.__enter__.return_value.responses.create.return_value = response
        with patch.object(evaluation, 'api_client', return_value=client), self.assertRaises(ValueError):
            evaluation.optimize(record, self.state(record))
        self.assertEqual(usage.report()['summary']['total_tokens'], 530)
        logged = json.loads((evaluation.folder(record['id']) / 'optimization-response.json').read_text())
        self.assertEqual(logged['status'], 'incomplete')

    def test_restart_and_history_never_schedule_paid_backfill(self):
        evaluation.startup()
        history = evaluation.history()
        self.assertEqual(history['records'][0]['status'], 'not_evaluated')
        evaluation.WORKERS.submit.assert_not_called()
        record = self.capture()
        evaluation.WORKERS.submit.reset_mock()
        evaluation.startup()
        self.assertEqual(evaluation.get(record['id'])['status'], 'interrupted')
        evaluation.WORKERS.submit.assert_not_called()
        evaluation.retry(record['id'])
        evaluation.WORKERS.submit.assert_called_once()
        with self.assertRaises(ValueError):
            evaluation.retry(record['id'])

    def test_disabled_auto_eval_is_manual_and_setting_validation_is_strict(self):
        evaluation.configure({'enabled': False})
        record = self.capture()
        self.assertEqual(record['status'], 'disabled')
        evaluation.WORKERS.submit.assert_not_called()
        evaluation.retry(record['id'])
        evaluation.WORKERS.submit.assert_called_once()
        for value in (float('nan'), -1, 4, True):
            with self.assertRaises(ValueError):
                evaluation.configure({'threshold': value})

    def test_history_metrics_keep_ungraded_out_of_average_and_preserve_threshold(self):
        record = self.capture()
        with patch.object(evaluation, 'judge', return_value=scores(2)):
            evaluation.run(record['id'])
        evaluation.configure({'threshold': 3})
        self.c['messages'] += [{'id': 'u2', 'role': 'user', 'text': '新問題', 'turn_id': 't2'},
                              {'id': 'm2', 'role': 'assistant', 'phase': 'final_answer', 'text': '答案', 'turn_id': 't2'}]
        report = evaluation.history(library_id='a')
        self.assertEqual(report['summary']['total'], 2)
        self.assertEqual(report['summary']['evaluated'], 1)
        self.assertEqual(report['summary']['average_score'], 2)
        self.assertEqual(report['summary']['pass_rate'], 1)
        self.assertEqual(evaluation.history(library_id='b')['total'], 0)

    def test_api_scope_validation_and_mutation_header(self):
        self.assertEqual(self.client.get('/api/evaluations?library_id=b&chat_id=' + self.c['id']).status_code, 400)
        self.assertEqual(self.client.get('/api/evaluations?limit=101').status_code, 400)
        self.assertEqual(self.client.get('/api/evaluations/not-an-id').status_code, 404)
        self.assertEqual(self.post('/api/evaluations/' + 'a' * 32 + '/retry').status_code, 404)
        self.assertEqual(self.client.patch('/api/evaluation-settings', json=evaluation.DEFAULTS).status_code, 403)
        for changes in ({'threshold': 4}, {'threshold': 'NaN'}, {'enabled': 1}):
            self.assertEqual(self.client.patch('/api/evaluation-settings', headers=self.headers,
                json={**evaluation.DEFAULTS, **changes}).status_code, 422)
        self.assertEqual(self.post('/api/chats/' + self.c['id'] + '/evaluations', {'turn_id': 'unknown'}).status_code, 404)

    def test_suggested_prompt_requires_explicit_apply_and_stale_revision_is_rejected(self):
        record = self.capture()
        record.update(status='completed', score=1, passed=False,
            optimization={'status': 'completed', 'proposed_system_prompt': '先驗證再回答。'})
        evaluation.save(record)
        self.assertEqual(self.c['prompt_revision'], 1)
        response = self.post('/api/evaluations/' + record['id'] + '/apply-prompt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['prompt_revision'], 2)
        self.assertEqual(self.c['top_k'], 100)
        self.assertEqual(self.post('/api/evaluations/' + record['id'] + '/apply-prompt').status_code, 409)
        self.assertEqual(evaluation.get(record['id'])['prompt_revision'], 1)


    def test_prompt_change_rotates_session_with_complete_history_and_archives_cost_scope(self):
        self.c['previous_sessions'] = [{'session_id': 'older', 'turn_ids': ['old-turn']}]
        self.c['messages'].insert(0, {'id': 'older-message', 'role': 'assistant', 'phase': 'final_answer',
                                    'turn_id': 'old-turn', 'text': '舊上下文'})
        self.c['fetched'] = {'cached': {'path': 'old-sandbox'}}
        chat.update_settings(self.c['id'], system_prompt='新的系統指令', top_k=50, expected_revision=1)
        chat.send(self.c['id'], '下一個問題')
        message_id = self.c['active_submission_id']
        client = MagicMock()
        with patch.object(chat, 'api_client', return_value=client), patch.object(chat, 'consume'), patch.object(chat, 'reconcile'):
            chat.run(self.c, '下一個問題', message_id)
        call = client.__enter__.return_value.beta.agents.sessions.create.call_args.kwargs
        self.assertIn('新的系統指令', call['agent']['instructions'])
        self.assertIn('Fixed source boundary', call['agent']['instructions'])
        history = json.loads(call['input'])['conversation_history']
        self.assertEqual([m['text'] for m in history], ['舊上下文', '2 加 2 是多少？', '4。'])
        self.assertEqual(self.c['previous_sessions'][-1]['session_id'], 's1')
        self.assertEqual(self.c['previous_sessions'][-1]['turn_ids'], ['t1'])
        self.assertEqual(self.c['fetched'], {})
        client.__enter__.return_value.beta.agents.sessions.update.assert_not_called()
        client.__enter__.return_value.beta.agents.sessions.stream.assert_not_called()

    def test_unmodified_prompt_uses_existing_session_and_current_turn_snapshot(self):
        client = MagicMock()
        self.c['session_effective_prompt'] = '實際遠端 prompt'
        chat.send(self.c['id'], '繼續')
        message_id = self.c['active_submission_id']
        self.assertEqual(self.c['submissions'][message_id]['effective_prompt'], '實際遠端 prompt')
        with patch.object(chat, 'api_client', return_value=client), patch.object(chat, 'consume'), patch.object(chat, 'reconcile'):
            chat.run(self.c, '繼續', message_id)
        client.__enter__.return_value.beta.agents.sessions.stream.assert_called_once_with('s1', input='繼續', idempotency_key=message_id)
        client.__enter__.return_value.beta.agents.sessions.create.assert_not_called()

    def test_prompt_busy_and_stale_updates_are_blocked_defaults_only_affect_new_chats(self):
        for status in ('running', 'queued', 'disconnected'):
            self.c['status'] = status
            with self.assertRaises(ValueError):
                chat.update_settings(self.c['id'], system_prompt='new', top_k=100)
        self.c['status'] = 'completed'
        self.c['response_active'] = True
        with self.assertRaises(ValueError):
            chat.update_settings(self.c['id'], system_prompt='new', top_k=100)
        self.c['response_active'] = False
        with self.assertRaises(ValueError):
            chat.update_settings(self.c['id'], system_prompt='new', top_k=100, expected_revision=7)
        chat.set_default_prompt('新的預設')
        self.assertEqual(chat.create('a')['system_prompt'], '新的預設')
        self.assertEqual(self.c['system_prompt'], '請用繁體中文，核對計算。')

    def test_reconciliation_preserves_original_message_turn_for_history(self):
        self.c['turn_id'] = 't2'
        chat.apply_item(self.c, {'id': 'm1', 'type': 'message', 'role': 'assistant', 'phase': 'final_answer',
                                'content': [{'type': 'output_text', 'text': '4。'}]})
        self.assertEqual(self.c['messages'][1]['turn_id'], 't1')

    def test_turn_completion_evaluates_once_and_records_actual_prompt(self):
        self.c['messages'] = []
        chat.send(self.c['id'], '一般問題')
        message_id = self.c['active_submission_id']
        def consume(value, client, stream):
            chat.apply_event(value, {'type': 'agent.session.turn.created', 'session_id': 's1', 'turn_id': 't3'})
            chat.apply_event(value, {'type': 'agent.session.turn.item.done', 'session_id': 's1', 'turn_id': 't3',
                'item': {'type': 'message', 'id': 'm3', 'role': 'assistant', 'phase': 'final_answer',
                         'content': [{'type': 'output_text', 'text': '完整答案'}]}})
            chat.apply_event(value, {'type': 'agent.session.turn.completed', 'session_id': 's1', 'turn_id': 't3',
                                    'usage': {'input_tokens': 10, 'output_tokens': 10}})
        with patch.object(chat, 'api_client', return_value=MagicMock()), patch.object(chat, 'consume', side_effect=consume), \
             patch.object(chat, 'reconcile'):
            chat.run(self.c, '一般問題', message_id)
        evaluation.WORKERS.submit.assert_called_once()
        record = evaluation.history()['records'][0]
        self.assertEqual(record['question'], '一般問題')
        self.assertEqual(record['answer'], '完整答案')
        self.assertEqual(record['prompt_revision'], 1)
        self.assertFalse(self.c['response_active'])
        with chat.LOCK:
            evaluation.capture(self.c)
        evaluation.WORKERS.submit.assert_called_once()

    def test_resume_reconciliation_binds_unobserved_turn_to_original_submission(self):
        chat.send(self.c['id'], '中斷前送出的問題')
        message_id = self.c['active_submission_id']
        client = MagicMock()
        client.beta.agents.sessions.items.list.return_value = []
        client.beta.agents.sessions.retrieve.return_value.required_actions = []
        client.beta.agents.sessions.retrieve.return_value.environment = SimpleNamespace(id='env1')
        with patch.object(chat, 'sync_usage', return_value=[{'id': 't4', 'status': 'completed'}]):
            chat.reconcile(self.c, client)
        submission = self.c['submissions'][message_id]
        self.assertEqual(submission['turn_id'], 't4')
        self.assertEqual(submission['session_id'], 's1')
        self.assertIn('核對計算', submission['effective_prompt'])


if __name__ == '__main__':
    unittest.main()
