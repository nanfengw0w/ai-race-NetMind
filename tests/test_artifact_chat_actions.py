"""Version mutations through chat use isolated storage and fully fake models."""
import json
from unittest.mock import patch
import unittest
import test_artifact_chat_integration as fixtures
from services import artifact_chat, artifact_revision, artifact_store as store, chat_service


class ArtifactChatActionTests(unittest.TestCase):
    setUp = fixtures.ArtifactChatIntegrationTests.setUp
    tearDown = fixtures.ArtifactChatIntegrationTests.tearDown
    save_message = fixtures.ArtifactChatIntegrationTests.save_message
    def set_plan(self, action, evidence='', target=''):
        self.planner.return_value = (json.dumps(dict(action=action, evidence=evidence, target=target), ensure_ascii=False), {'finish_reason': 'stop'})

    def talk(self, message, artifact=None, stream=False, **reference):
        body = dict(student_id='session', user_id='local_user', message=message)
        if artifact:
            body['resource_ref'] = dict(resource_id=artifact['id'], version=artifact['version'], **reference)
        return self.client.post('/api/chat/talk' + ('/stream' if stream else ''), json=body)

    def current(self, artifact):
        return store.read_artifact(artifact['id'], student_id='session')

    def test_answer_intents_preserve_content_and_versions(self):
        old = self.save_message()
        for message in ['你好', '如何修改结尾？', '不要修改结尾', '他说“修改结尾”，这是什么意思？']:
            with self.subTest(message=message), patch.object(chat_service, 'llm_chat', return_value='讨论内容'), patch.object(artifact_revision, 'chat_with_diagnostics') as generation:
                self.set_plan('answer')
                response = self.talk(message, old)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn('artifact', response.json())
                generation.assert_not_called()
                self.assertEqual(self.current(old)['version'], 1)
                self.assertEqual(self.current(old)['content_hash'], old['content_hash'])
        submitted = self.planner.call_args.args[0]
        self.assertEqual(json.loads(submitted[1]['content']), {'current_user_message': message})
        self.assertNotIn(old['content'], str(submitted))

    def test_revision_stream_commits_before_artifact_and_persists_receipt(self):
        old = self.save_message()
        message = '把结尾改成开放式'
        self.set_plan('revise', message)
        committed = []
        real_event = chat_service._event
        def event(name, data):
            if name == 'artifact':
                current = self.current(old)
                self.assertEqual(current['version'], 2)
                self.assertEqual(current['content'], '完整的新正文')
                committed.append(current['content_hash'])
            return real_event(name, data)
        with patch.object(artifact_revision, 'chat_with_diagnostics', return_value=('完整的新正文', {'finish_reason': 'stop'})) as generation, patch.object(chat_service, '_event', side_effect=event):
            response = self.talk(message, old, stream=True)
        self.assertEqual(response.status_code, 200, response.text)
        generation.assert_called_once()
        self.assertEqual(len(committed), 1)
        events = [block.split('\n', 1)[0] for block in response.text.replace('\r\n', '\n').split('\n\n') if block]
        self.assertLess(events.index('event: artifact'), events.index('event: done'))
        done = next(block for block in response.text.replace('\r\n', '\n').split('\n\n') if block.startswith('event: done\n'))
        receipt = json.loads(done.split('data: ', 1)[1])
        history = self.client.get('/api/conversations/session').json()
        saved = next(row for row in history if row['id'] == receipt['conversation_id'])
        self.assertEqual(saved['artifact_action']['action'], 'revise')
        self.assertEqual(saved['artifact_action']['version'], 2)
        self.assertEqual(saved['content'], receipt['reply'])

    def test_restore_previous_and_first_copy_hash_without_generation(self):
        old = self.save_message()
        second = store.append_revision(old['id'], '第二版完整正文', base_version=1, expected_version=1, student_id='session')
        third = store.append_revision(old['id'], '第三版完整正文', base_version=2, expected_version=2, student_id='session')
        for message, target, selected, expected in [('恢复上一版', '上一版', third, second), ('恢复第一版', '第一版', None, old)]:
            selected = selected or self.current(old)
            self.set_plan('restore', message, target)
            with patch.object(artifact_revision, 'chat_with_diagnostics') as generation:
                response = self.talk(message, selected)
            self.assertEqual(response.status_code, 200, response.text)
            generation.assert_not_called()
            self.assertEqual(response.json()['artifact']['base_version'], selected['version'])
            self.assertEqual(self.current(old)['content_hash'], expected['content_hash'])
            self.assertEqual(self.current(old)['content'], expected['content'])

    def test_stale_ui_rejected_but_old_preview_with_current_version_can_revise(self):
        old = self.save_message()
        store.append_revision(old['id'], '第二版正文', base_version=1, expected_version=1, student_id='session')
        self.set_plan('revise', '修改结尾')
        with patch.object(artifact_revision, 'chat_with_diagnostics', return_value=('从旧版修订后的正文', {'finish_reason': 'stop'})) as generation:
            stale = self.talk('修改结尾', old, current_version=1)
            self.assertEqual(stale.status_code, 409, stale.text)
            generation.assert_not_called()
            fresh = self.talk('修改结尾', old, current_version=2)
            self.assertEqual(fresh.status_code, 200, fresh.text)
            generation.assert_called_once()
            source = json.loads(generation.call_args.args[0][1]['content'])['original']
            self.assertEqual(source['content'], old['content'])
            self.assertEqual(self.current(old)['version'], 3)

    def test_invalid_plans_and_ungrounded_restore_target_never_write(self):
        old = self.save_message()
        for raw in ['not JSON', '[]', '{"action":"delete"}', '{"action":"revise","evidence":"未出现的指令"}', '{"action":"restore","evidence":"恢复","target":"第三版"}']:
            with self.subTest(raw=raw), patch.object(artifact_revision, 'chat_with_diagnostics') as generation:
                self.planner.return_value = (raw, {'finish_reason': 'stop'})
                response = self.talk('恢复第一版', old)
                self.assertIn(response.status_code, [200, 502], response.text)
                generation.assert_not_called()
                self.assertEqual(self.current(old)['version'], 1)

    def test_generation_failure_preserves_original_and_has_no_receipt(self):
        old = self.save_message()
        self.set_plan('revise', '修改结尾')
        with patch.object(artifact_revision, 'chat_with_diagnostics', side_effect=RuntimeError('fake unavailable')):
            response = self.talk('修改结尾', old)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.current(old)['content_hash'], old['content_hash'])
        self.assertEqual(self.current(old)['version'], 1)
        history = self.client.get('/api/conversations/session').json()
        self.assertFalse(any(row.get('artifact_action') for row in history))

    def test_missing_and_ambiguous_targets_do_not_generate(self):
        self.set_plan('revise', '修改结尾')
        with patch.object(artifact_revision, 'chat_with_diagnostics') as generation:
            missing = self.talk('修改结尾')
            self.assertEqual(missing.status_code, 200, missing.text)
            old = self.save_message()
            self.client.post('/api/artifacts/from-message', json=dict(student_id='session', user_id='local_user', conversation_id=self.source_id, title='另一篇'))
            # A distinct saved source is required because saving the same message is idempotent.
            self.db.execute("INSERT INTO conversations(student_id,role,content) VALUES('session','assistant','另一篇完整故事')")
            source_id = self.db.execute('SELECT MAX(id) FROM conversations').fetchone()[0]
            self.db.commit()
            self.client.post('/api/artifacts/from-message', json=dict(student_id='session', user_id='local_user', conversation_id=source_id, title='另一个故事'))
            ambiguous = self.talk('修改结尾')
            self.assertEqual(ambiguous.status_code, 409, ambiguous.text)
            generation.assert_not_called()
            self.assertEqual(self.current(old)['version'], 1)

    def test_regular_revision_generates_once_and_records_action(self):
        old = self.save_message()
        self.set_plan('revise', '修改结尾')
        with patch.object(artifact_revision, 'chat_with_diagnostics', return_value=('完整修订正文', {'finish_reason': 'stop'})) as generation, patch.object(chat_service, 'llm_chat') as answer:
            response = self.talk('修改结尾', old)
        self.assertEqual(response.status_code, 200, response.text)
        generation.assert_called_once()
        answer.assert_not_called()
        self.assertEqual(response.json()['artifact']['resource']['version'], 2)
        history = self.client.get('/api/conversations/session').json()
        saved = next(row for row in history if row['id'] == response.json()['conversation_id'])
        self.assertEqual(saved['artifact_action']['version'], 2)

    def test_cross_session_mutation_refused_before_generation(self):
        old = self.save_message()
        self.set_plan('revise', '修改结尾')
        with patch.object(artifact_revision, 'chat_with_diagnostics') as generation:
            response = self.client.post('/api/chat/talk', json=dict(student_id='other', user_id='local_user', message='修改结尾', resource_ref=dict(resource_id=old['id'], version=1, current_version=1)))
        self.assertEqual(response.status_code, 404, response.text)
        generation.assert_not_called()
        self.assertEqual(self.current(old)['version'], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM conversations WHERE student_id='other'").fetchone()[0], 0)
