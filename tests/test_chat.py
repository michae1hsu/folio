import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend import chat, config, knowledge


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(config, 'DATA', Path(self.temp.name)), patch.object(chat, 'CHATS', {}),
                        patch.object(knowledge, 'STATE', {'libraries':{'a':{'id':'a','name':'A'}, 'b':{'id':'b','name':'B'}}, 'documents':{}, 'builds':[]})]
        for p in self.patches:
            p.start()
        value = chat.create('a')
        self.chat = chat.CHATS[value['id']]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_stream_replay_is_idempotent_and_only_public_reasoning_is_saved(self):
        c = self.chat
        event = {'type':'agent.session.turn.output_text.delta','event_id':'e1','session_id':'s1','turn_id':'t1','item_id':'m1','content_index':0,'delta':'完整回答'}
        chat.apply_event(c, event)
        chat.apply_event(c, event)
        self.assertEqual(c['messages'][0]['text'], '完整回答')
        chat.apply_event(c, {'type':'agent.session.turn.item.done','item':{'id':'r1','type':'reasoning','summary':[{'text':'正在查找來源'}],'encrypted_content':'hidden-secret'}})
        self.assertNotIn('hidden-secret', (chat.folder(c)/'chat.json').read_text(encoding='utf-8'))
        self.assertEqual(c['trace'][0]['data']['text'], '正在查找來源')
        chat.apply_event(c, {'type':'agent.session.turn.item.done','item':{'id':'m1','type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'完整回答'}]}})
        self.assertEqual(len(c['messages']), 1)
        self.assertEqual(c['messages'][0]['phase'], 'final_answer')

    def test_tool_retry_reuses_durable_result_but_redelivers(self):
        c = self.chat
        c['session_id'] = 's1'
        action = {'type':'function_call','name':'search_file','call_id':'call1','turn_id':'t1','arguments':json.dumps({'query':'RAG','question':'完整需求'})}
        client = MagicMock()
        with patch.object(chat.knowledge, 'search_files', return_value={'files':[{'document_id':'doc1','content_hash':'sha',
                'drive_modified_at':'2026-09-03T01:02:03Z','source_version':'7'}]}) as search:
            chat.respond(c, client, [action])
            chat.respond(c, client, [action])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.kwargs['library_id'], 'a')
        self.assertEqual(client.beta.agents.sessions.events.create.call_count, 2)
        self.assertEqual(c['authorized_files']['doc1'], 'sha')
        self.assertNotIn('authorized_files', chat.snapshot(c['id']))
        output = json.loads(c['tool_results']['t1:call1']['output'])
        self.assertEqual(output['files'][0]['drive_modified_at'], '2026-09-03T01:02:03Z')
        self.assertEqual(output['files'][0]['source_version'], '7')

    def test_selected_provider_updates_next_turn_and_is_kept_in_submission(self):
        c = self.chat
        chat.update_settings(c['id'], system_prompt=c['system_prompt'], top_k=100, judge_provider='openai_decisions')
        self.assertEqual(c['prompt_revision'], 1)
        # Older clients that omit the field must not reset an existing selection.
        chat.update_settings(c['id'], system_prompt=c['system_prompt'], top_k=80)
        self.assertEqual(c['judge_provider'], 'openai_decisions')
        with patch.object(chat.WORKERS, 'submit'):
            chat.send(c['id'], '請找完整流程')
        submission = c['submissions'][c['active_submission_id']]
        self.assertEqual(submission['judge_provider'], 'openai_decisions')
        with self.assertRaises(ValueError):
            chat.update_settings(c['id'], system_prompt=c['system_prompt'], top_k=80, judge_provider='jev')
        client = MagicMock()
        action = {'type':'function_call','name':'search_file','call_id':'c1','turn_id':'t1',
                  'arguments':json.dumps({'query':'流程','question':'完整流程'})}
        with patch.object(knowledge, 'search_files', return_value={'files': []}) as search:
            chat.respond(c, client, [action])
        self.assertEqual(search.call_args.kwargs['judge_provider'], 'openai_decisions')
        self.assertEqual(search.call_args.kwargs['library_id'], 'a')

    def test_fetched_markdown_and_reused_cache_return_indexed_drive_metadata(self):
        doc = {'id':'doc1','library_id':'a','name':'文件.pdf','media':False,'content_hash':'hash',
               'source':{'id':'drive1','modifiedTime':'2026-09-03T01:02:03Z','version':'7'},
               'assets':{'parsed':{'provider':'gcs','status':'uploaded','sha256':'md-sha','uri':'gs://test/md',
                                   'updated_at':'2026-10-02T00:00:00Z'}}}
        knowledge.STATE['documents']['doc1'] = doc
        self.chat['authorized_files']['doc1'] = 'hash'
        self.chat['environment_id'] = 'env1'
        with patch.object(chat.gcs, 'read', return_value=b'full document') as read, \
                patch.object(chat.gcs, 'links', return_value={'url':'https://signed.example/md'}), \
                patch.object(chat, 'wait_environment'):
            first = chat.fetch_file(self.chat, MagicMock(), 'doc1', 'parsed')
            self.assertEqual(first['drive_modified_at'], '2026-09-03T01:02:03Z')
            self.assertEqual(first['source_version'], '7')
            # Older caches get the same pinned source metadata without downloading again.
            first.pop('drive_modified_at')
            again = chat.fetch_file(self.chat, MagicMock(), 'doc1', 'parsed')
            self.assertEqual(again['drive_modified_at'], '2026-09-03T01:02:03Z')
            self.assertEqual(read.call_count, 1)

    def test_builtin_prompt_upgrade_preserves_custom_prompts_and_historical_submissions(self):
        self.chat.update(system_prompt=chat.LEGACY_INSTRUCTIONS, session_id='old-session', session_prompt_revision=1,
                         session_effective_prompt=chat.LEGACY_INSTRUCTIONS)
        self.chat['submissions']['old-message'] = {'effective_prompt':chat.LEGACY_INSTRUCTIONS}
        chat.save(self.chat)
        customized = chat.create('a', system_prompt=chat.LEGACY_INSTRUCTIONS+'\n使用者自訂規則。')
        chat.CHATS.clear()
        chat.startup()
        upgraded = chat.CHATS[self.chat['id']]
        self.assertEqual(upgraded['system_prompt'], chat.INSTRUCTIONS)
        self.assertEqual(upgraded['prompt_revision'], 2)
        self.assertEqual(upgraded['session_prompt_revision'], 1)
        self.assertEqual(upgraded['submissions']['old-message']['effective_prompt'], chat.LEGACY_INSTRUCTIONS)
        self.assertEqual(chat.CHATS[customized['id']]['system_prompt'], customized['system_prompt'])
        chat.CHATS.clear()
        chat.startup()
        self.assertEqual(chat.CHATS[self.chat['id']]['prompt_revision'], 2)
        chat.set_default_prompt(chat.LEGACY_INSTRUCTIONS)
        self.assertEqual(chat.create('a')['system_prompt'], chat.INSTRUCTIONS)
        chat.set_default_prompt('自訂預設')
        self.assertEqual(chat.create('a')['system_prompt'], '自訂預設')

    def test_download_rejects_unsearched_or_changed_file_before_drive_access(self):
        with patch.object(chat.knowledge, 'get_document', return_value={'content_hash':'new'}), patch.object(chat.drive, 'DriveClient') as drive:
            for authorized in ({}, {'doc':'old'}):
                self.chat['authorized_files'] = authorized
                with self.assertRaises(ValueError):
                    chat.fetch_file(self.chat, MagicMock(), 'doc', 'original')
            drive.assert_not_called()

    def test_continuation_reuses_session_and_input_idempotency_key(self):
        client = MagicMock()
        self.chat['session_id'] = 's1'
        self.chat['session_prompt_revision'] = self.chat['prompt_revision']
        with patch.object(chat, 'api_client', return_value=client), patch.object(chat, 'consume'), patch.object(chat, 'reconcile'):
            chat.run(self.chat, '繼續', 'msg2')
        client.__enter__.return_value.beta.agents.sessions.stream.assert_called_once_with('s1', input='繼續', idempotency_key='msg2')
        client.__enter__.return_value.beta.agents.sessions.create.assert_not_called()
        self.assertEqual(self.chat['library_id'], 'a')

    def test_cross_library_download_is_blocked_even_with_a_cached_authorization(self):
        knowledge.STATE['documents']['foreign'] = {'id':'foreign','library_id':'b','content_hash':'sha'}
        self.chat['authorized_files']['foreign'] = 'sha'
        self.chat['fetched']['foreign:original:sha'] = {'path':'old-cache'}
        with patch.object(chat.drive, 'DriveClient') as drive:
            with self.assertRaisesRegex(ValueError, 'does not belong'):
                chat.fetch_file(self.chat, MagicMock(), 'foreign', 'original')
            drive.assert_not_called()

    def test_legacy_chat_is_read_only_and_new_chat_requires_a_known_library(self):
        self.chat.pop('library_id')
        self.chat.update(session_id='legacy-session', status='disconnected')
        chat.save(self.chat)
        chat.CHATS.clear()
        chat.startup()
        saved = chat.snapshot(self.chat['id'])
        self.assertTrue(saved['read_only'])
        with patch.object(chat.WORKERS, 'submit') as run:
            with self.assertRaisesRegex(ValueError, 'legacy'):
                chat.send(self.chat['id'], 'continue')
            with self.assertRaisesRegex(ValueError, 'legacy'):
                chat.resume(self.chat['id'])
            run.assert_not_called()
        with self.assertRaises(ValueError):
            chat.create('missing')
        second = chat.create('b')
        self.assertEqual(second['library_id'], 'b')
        self.assertIsNone(second['session_id'])

    def test_restart_requires_observing_existing_session(self):
        self.chat.update(status='running', session_id='existing')
        chat.save(self.chat)
        chat.CHATS.clear()
        chat.startup()
        restored = next(iter(chat.CHATS.values()))
        self.assertEqual(restored['status'], 'disconnected')
        with self.assertRaises(ValueError):
            chat.send(restored['id'], 'duplicate')

    def test_file_upload_waits_for_connected_hosted_environment(self):
        self.chat['environment_id'] = 'env1'
        client = MagicMock()
        client.beta.agents.environments.retrieve.side_effect = [SimpleNamespace(status='pending'), SimpleNamespace(status='connected')]
        with patch.object(chat.time, 'sleep') as sleep:
            chat.wait_environment(self.chat, client)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(self.chat['trace'][0]['kind'], 'environment_wait')
        self.assertEqual(self.chat['trace'][0]['data']['status'], 'completed')
        self.assertEqual(len(self.chat['trace']), 1)


if __name__ == '__main__':
    unittest.main()
