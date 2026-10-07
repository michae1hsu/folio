import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend import chat, config, jobs, knowledge, models, usage


def tokens(inp=1000, out=100, cached=200, writes=100, reasoning=30):
    return {'input_tokens': inp, 'input_tokens_details': {'cached_tokens': cached, 'cache_write_tokens': writes},
            'output_tokens': out, 'output_tokens_details': {'reasoning_tokens': reasoning}, 'total_tokens': inp + out}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, 'DATA', Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, op='document', model='gpt-6.1-sol', value=None, **kwargs):
        with usage.request(op, model, **kwargs) as record:
            return record(tokens() if value is None else value)

    def test_cache_reads_writes_and_reasoning_are_not_double_charged(self):
        entry = self.add()
        # 700 ordinary + 200 cached + 100 written; reasoning already in 100 output.
        self.assertAlmostEqual(entry['cost_usd'], (700 * 2 + 200 * .1 + 100 * 2.5 + 100 * 10) / 1e6)
        self.assertEqual(entry['total_tokens'], 1100)
        self.assertEqual(entry['reasoning_tokens'], 30)

    def test_embedding_and_jev_rates_and_tiny_costs(self):
        entry = self.add('query_embedding', config.EMBEDDING_MODEL, {'prompt_tokens': 12, 'total_tokens': 12})
        self.assertEqual(entry['output_tokens'], 0)
        self.assertAlmostEqual(entry['cost_usd'], .00000156)
        entry = self.add('jev', config.JEV_MODEL, {'input_tokens': 5000, 'output_tokens': 19})
        self.assertEqual(entry['total_tokens'], 5019)
        self.assertAlmostEqual(entry['cost_usd'], .00021)

    def test_unknown_usage_unknown_model_and_zero_are_distinct(self):
        unknown = self.add(value={})
        self.assertIsNone(unknown['total_tokens'])
        self.assertIsNone(unknown['cost_usd'])
        unknown_model = self.add(model='not-a-priced-model')
        self.assertEqual(unknown_model['total_tokens'], 1100)
        self.assertIsNone(unknown_model['cost_usd'])
        zero = self.add(value=tokens(0, 0, 0, 0, 0))
        self.assertEqual(zero['cost_usd'], 0)
        self.assertEqual(zero['total_tokens'], 0)
        self.assertEqual(usage.report()['summary']['unknown_costs'], 2)

    def test_pending_interruption_and_retry_have_separate_records(self):
        with self.assertRaises(TimeoutError):
            with usage.request('document', 'gpt-6-luna', job_id='a'):
                summary = usage.report(job_id='a')['summary']
                self.assertEqual(summary['pending'], 1)
                self.assertIsNone(summary['cost_usd'])
                raise TimeoutError()
        self.add(model='gpt-6-luna', job_id='a')
        result = usage.report(job_id='a')['summary']
        self.assertEqual(result['entries'], 2)
        self.assertEqual(result['unknown_costs'], 1)
        self.assertGreater(result['cost_usd'], 0)

    def test_incomplete_output_is_metered_before_content_validation(self):
        response = SimpleNamespace(id='resp1', status='incomplete', output_text='', usage=tokens(), model='gpt-6-luna')
        client = MagicMock()
        client.responses.create.return_value = response
        with self.assertRaises(ValueError):
            models.parse_document(client, b'pdf', 1,
                                  usage_scope={'job_id': 'batch'})
        result = usage.report(job_id='batch')['summary']
        self.assertEqual(result['total_tokens'], 1100)
        self.assertGreater(result['cost_usd'], 0)
        with self.assertRaises(ValueError):
            models.parse_document(client, b'x' * 50_000_000, 1, usage_scope={'job_id': 'batch'})
        self.assertEqual(usage.report(job_id='batch')['summary']['entries'], 1)

    def test_whole_response_id_is_deduplicated_but_real_retry_costs_remain(self):
        for response_id in ('a', 'a', 'b'):
            with usage.request('document', 'gpt-6-luna') as record:
                record(tokens(), response_id=response_id)
        self.assertEqual(usage.report()['summary']['entries'], 2)
        self.assertEqual(usage.report()['summary']['total_tokens'], 2200)

    def test_scope_isolation_concurrent_writes_and_durable_totals(self):
        def write(n):
            self.add(job_id='batch' + str(n % 2), file_id=str(n), label='測試檔案.pdf')
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(write, range(30)))
        self.assertEqual(usage.report()['summary']['total_tokens'], 33000)
        self.assertEqual(usage.report(job_id='batch0')['summary']['total_tokens'], 16500)
        self.assertEqual(usage.report(job_id='batch0', file_id='0')['summary']['total_tokens'], 1100)
        usage.startup({}, {}, {})
        usage.startup({}, {}, {})
        self.assertEqual(usage.report()['summary']['entries'], 30)

    def test_long_context_and_service_tiers_use_request_not_turn_totals(self):
        large = tokens(300000, 1000, 20000, 100000)
        with usage.request('document', 'gpt-6.1-sol') as record:
            result = record(large, service_tier='priority')
        self.assertEqual(result['rates'], {'input': 8, 'cached': .4, 'cache_write': 10, 'output': 30})
        result = self.add('agent', value=large)
        self.assertEqual(result['rates']['input'], 2)
        self.assertIn('context lengths are unknown', result['price_note'])

    def test_transcription_minutes_and_tokens_are_independent(self):
        with usage.request('transcription', 'gpt-transcribe') as record:
            entry = record({'type': 'duration', 'seconds': 120}, duration_seconds=999)
        self.assertAlmostEqual(entry['cost_usd'], .009)
        self.assertIsNone(entry['total_tokens'])
        with usage.request('transcription', 'gpt-transcribe') as record:
            entry = record(None, duration_seconds=60)
        self.assertAlmostEqual(entry['cost_usd'], .0045)

    def test_agent_turn_replay_late_accounting_and_subagents(self):
        c = {'id': 'chat', 'session_id': 'session', 'model': 'gpt-6.1-sol'}
        for _ in range(2):
            usage.agent_turn(c, {'id': 'turn1', 'status': 'completed', 'usage': None})
        usage.agent_turn(c, {'id': 'turn1', 'status': 'completed', 'usage': tokens()})
        usage.agent_turn(c, {'id': 'turn1', 'status': 'completed', 'usage': None})
        self.assertEqual(usage.report(chat_id='chat')['summary']['total_tokens'], 1100)
        usage.agent_turn(c, {'id': 'turn1', 'status': 'completed', 'usage': tokens(out=200)})
        usage.agent_turn(c, {'id': 'turn2', 'status': 'cancelled', 'usage': tokens()})
        child = usage.agent_turn(c, {'id': 'child', 'subagent_id': 'sub', 'status': 'completed', 'usage': tokens()})
        self.assertIsNone(child['cost_usd'])  # A subagent's model must not be guessed.
        self.assertEqual(usage.report(chat_id='chat')['summary']['total_tokens'], 3400)
        self.assertEqual(usage.report(chat_id='chat', turn_id='turn1')['summary']['total_tokens'], 1200)

    def test_agent_sync_reads_all_turn_pages_without_adding_session_total(self):
        c = {'id': 'chat', 'session_id': 'session', 'model': 'gpt-6.1-sol'}
        client = MagicMock()
        turns = [{'id': f'turn{n}', 'status': 'completed', 'usage': tokens()} for n in range(3)]
        client.beta.agents.sessions.turns.list.return_value = [SimpleNamespace(model_dump=lambda mode, t=t: t) for t in turns]
        with patch.object(chat, 'save'):
            chat.sync_usage(c, client)
            chat.sync_usage(c, client)
        self.assertEqual(usage.report(chat_id='chat')['summary']['total_tokens'], 3300)
        client.beta.agents.sessions.create.assert_not_called()

    def test_unacknowledged_agent_request_is_unknown_then_replaced_by_its_turn(self):
        c = {'id': 'chat', 'session_id': 'session', 'model': 'gpt-6.1-sol',
             'pending_usage_id': 'agent-request:message', 'turn_id': None}
        usage.put({'id': c['pending_usage_id'], 'operation': 'agent', 'model': c['model'],
                   'chat_id': c['id'], 'status': 'pending', 'usage': {}})
        usage.finish_agent_submission(c)
        summary = usage.report(chat_id='chat')['summary']
        self.assertEqual(summary['pending'], 0)
        self.assertEqual(summary['unknown_costs'], 1)
        c.update(turn_id='previous', usage_previous_turn_id='previous')
        usage.agent_turn(c, {'id': 'previous', 'usage': tokens(), 'status': 'completed'})
        self.assertEqual(usage.report(chat_id='chat')['summary']['unknown_costs'], 1)
        c['turn_id'] = 'turn1'
        usage.agent_turn(c, {'id': 'turn1', 'usage': tokens(), 'status': 'completed'})
        self.assertEqual(usage.report(chat_id='chat')['summary']['entries'], 2)
        self.assertEqual(usage.report(chat_id='chat')['summary']['total_tokens'], 2200)

    def test_restored_grading_usage_keeps_provider_and_does_not_duplicate(self):
        traces = []
        for trace_id, provider, model, reported in [
            ('decisions', 'openai_decisions', 'gpt-6-luna', {'input_tokens': 1000, 'output_tokens': 0, 'total_tokens': 1000}),
            ('unknown', 'openai_decisions', 'gpt-6-luna', {}),
            ('old-jev', None, 'jev-1.13.0', tokens()),
        ]:
            data = {'model': model, 'usage': reported, 'name': 'Synthetic source'}
            if provider:
                data['judge_provider'] = provider
            traces.append({'id': trace_id, 'kind': 'file_scored', 'at': '2026-10-07T00:00:00Z', 'data': data})
        chats = {'chat': {'id': 'chat', 'session_id': None, 'trace': traces}}
        usage.startup({}, {}, chats)
        usage.startup({}, {}, chats)
        entries = usage.report(chat_id='chat')['entries']
        self.assertEqual(len(entries), 3)
        by_id = {entry['id'].rsplit(':', 1)[1]: entry for entry in entries}
        self.assertEqual(by_id['decisions']['provider'], 'openai_decisions')
        self.assertEqual(by_id['decisions']['operation'], 'openai_decisions')
        self.assertAlmostEqual(by_id['decisions']['cost_usd'], .0001)
        self.assertIsNone(by_id['unknown']['cost_usd'])
        self.assertEqual(by_id['old-jev']['provider'], 'jev')
        self.assertEqual(by_id['old-jev']['operation'], 'jev')

    def test_legacy_import_only_once_and_keeps_missing_embedding_explicit(self):
        root = config.DATA / 'batch' / '0001'
        root.mkdir(parents=True)
        (root / 'response.json').write_text(json.dumps({'response_id': 'old', 'usage': tokens()}), encoding='utf-8')
        job = {'id': 'batch', 'updated_at': '2026-09-30T00:00:00Z', 'document_model': 'gpt-6-luna',
               'files': [{'id': '0001', 'name': '原文.pdf', 'kind': 'document'}]}
        doc = {'id': 'doc', 'job_id': 'batch', 'item_id': '0001', 'name': '原文.pdf', 'index_status': 'ready'}
        usage.startup({'batch': job}, {'doc': doc}, {})
        usage.startup({'batch': job}, {'doc': doc}, {})
        result = usage.report(job_id='batch')
        self.assertEqual(result['summary']['entries'], 2)
        self.assertEqual(result['summary']['total_tokens'], 1100)
        self.assertEqual(result['summary']['unknown_costs'], 1)


if __name__ == '__main__':
    unittest.main()
