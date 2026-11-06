"""Chat/SSE integration: opt-in, exact page/version and persisted check pointers."""
import json
import unittest
from unittest.mock import patch

import test_artifact_chat_integration as fixtures
from services import artifact_store as store, chat_service, understanding_checks as checks
from services.understanding_chat import is_check_request
from services.ppt_slides import list_slides


class UnderstandingChatTests(unittest.TestCase):
    setUp = fixtures.ArtifactChatIntegrationTests.setUp
    tearDown = fixtures.ArtifactChatIntegrationTests.tearDown

    def deck(self):
        return store.create_artifact('session', 'ppt', '检查测试课件',
            '<!DOCTYPE html><html><head></head><body><main id="deck">'
            '<section class="slide"><h1>首页</h1><p>首页只介绍 DNS。</p></section>'
            '<section class="slide"><h1>缓存</h1><p>缓存过期后需要重新查询。</p></section>'
            '</main></body></html>', user_id='local_user')

    def reference(self, artifact):
        return dict(resource_id=artifact['id'], version=artifact['version'],
                    selection=list_slides(artifact['content'])[1]['selection'], selection_source='preview')

    def generate(self, task, data):
        quote = '缓存过期后需要重新查询。'
        self.assertIn(quote, data['source_text'])
        self.assertNotIn('首页只介绍', data['source_text'])
        question = dict(prompt='过期后怎么办？', expected_answer='重新查询。', hint='留意过期。',
                        criteria=[dict(id='c1', description='解释过期', source_quote=quote)])
        return {'questions': [question.copy(), question.copy()]}, {'model': 'fake'}

    def test_explicit_check_stream_persists_and_never_mutates_artifact(self):
        artifact = self.deck()
        with patch.object(checks, '_call', side_effect=self.generate) as generate, patch.object(chat_service, 'llm_chat') as chat:
            response = self.client.post('/api/chat/talk/stream', json=dict(
                student_id='session', user_id='local_user', message='考考我这一页', resource_ref=self.reference(artifact)))
        self.assertEqual(response.status_code, 200, response.text)
        done = next(block for block in response.text.replace('\r\n', '\n').split('\n\n') if block.startswith('event: done'))
        result = json.loads(done.split('data: ', 1)[1])
        check = result['understanding_check']
        self.assertEqual(check['source']['slide_number'], 2)
        self.assertNotIn('expected_answer', json.dumps(check))
        generate.assert_called_once()
        chat.assert_not_called()
        self.planner.assert_not_called()
        history = self.client.get('/api/conversations/session').json()
        saved = next(row for row in history if row['id'] == result['conversation_id'])
        self.assertEqual(saved['understanding_check_id'], check['id'])
        self.assertEqual(saved['resource_reference']['selection']['slide_number'], 2)
        self.assertEqual(store.read_artifact(artifact['id'], student_id='session')['version'], 1)

    def test_explicit_page_overrides_preview_and_old_version_stays_frozen(self):
        artifact = self.deck()
        current = store.append_revision(artifact['id'], artifact['content'].replace('需要重新查询', '必须标记失效并重新查询'),
                                        base_version=1, expected_version=1, student_id='session')
        ref = dict(resource_id=current['id'], version=2, selection_source='preview',
                   selection=list_slides(current['content'])[0]['selection'])
        with patch.object(checks, '_call', side_effect=self.generate):
            response = self.client.post('/api/chat/talk', json=dict(student_id='session', user_id='local_user',
                message='考考我第一版第二页', resource_ref=ref))
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()['understanding_check']['source']
        self.assertEqual((source['version'], source['slide_number']), (1, 2))
        self.assertNotIn('必须标记', source['text'])

    def test_missing_invalid_or_forged_selection_cannot_downgrade_to_whole(self):
        artifact = self.deck()
        for message, ref in [
            ('考考我这一页', dict(resource_id=artifact['id'], version=1)),
            ('考考我第999页', self.reference(artifact)),
            ('考考我第一到二页', self.reference(artifact)),
            ('考考我这一页', {**self.reference(artifact), 'selection': {**self.reference(artifact)['selection'], 'text': '伪造'}}),
        ]:
            with self.subTest(message=message), patch.object(checks, '_call') as generate:
                response = self.client.post('/api/chat/talk', json=dict(student_id='session', user_id='local_user', message=message, resource_ref=ref))
                self.assertIn(response.status_code, (400, 409), response.text)
                generate.assert_not_called()

    def test_ordinary_question_and_quoted_or_negated_check_are_not_tests(self):
        for text in ['不要考考我这一页', '“考考我这一页”是什么意思？', '考考我这一页这个功能怎么用？', '如何检查我对这一页的理解？', '这一页我看了五分钟', '如果说考考我这一页会怎样？']:
            self.assertFalse(is_check_request(text), text)
        for text in ['考考我这一页', '再考考我这一页', '请考考我第三页', '根据当前作品给我出两道题', '检查一下我对这一页的理解']:
            self.assertTrue(is_check_request(text), text)
        with patch.object(checks, '_call') as generate, patch.object(chat_service, 'llm_chat', return_value='解释功能'):
            response = self.client.post('/api/chat/talk', json=dict(student_id='session', user_id='local_user', message='“考考我这一页”是什么意思？'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('understanding_check', response.json())
            generate.assert_not_called()

    def test_generation_mode_can_explicitly_check_selected_page(self):
        artifact = self.deck()
        with patch.object(checks, '_call', side_effect=self.generate):
            response = self.client.post('/api/chat', json=dict(student_id='session', user_id='local_user', message='考考我这一页', resource_ref=self.reference(artifact)))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['understanding_check']['source']['slide_number'], 2)

    def test_deleting_test_session_removes_checks_with_its_history(self):
        artifact = self.deck()
        with patch.object(checks, '_call', side_effect=self.generate):
            response = self.client.post('/api/chat/talk', json=dict(student_id='session', user_id='local_user', message='考考我这一页', resource_ref=self.reference(artifact)))
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.delete('/api/sessions/session', params={'user_id': 'local_user'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM understanding_checks').fetchone()[0], 0)
