"""Single-page history restoration over real isolated storage and HTTP/SSE."""
import json
import unittest
from unittest.mock import patch

import test_artifact_revision as revision_fixtures
import test_artifact_chat_integration as chat_fixtures
from test_ppt_slide_revision import DECK, SLIDES
from services import artifact_store as store, artifact_revision as revision, artifact_chat
from services.ppt_slides import list_slides


class SlideRestoreStorageTests(unittest.TestCase):
    setUp = revision_fixtures.ArtifactRevisionTests.setUp
    create = revision_fixtures.ArtifactRevisionTests.create
    originals = revision_fixtures.ArtifactRevisionTests.originals

    def restore(self, artifact, **kwargs):
        options = dict(version=1, expected_version=2, slide_number=3, student_id='s1', user_id='u1')
        options.update(kwargs)
        return store.restore_slide_version(artifact['id'], **options)

    def test_restores_only_historical_page_onto_latest_and_keeps_history_immutable(self):
        artifact = self.create('ppt', DECK, {'custom': 'keep'})
        newer = DECK.replace('旧内容', '第二页新内容').replace('结论', '第三页新内容')
        store.append_revision(artifact['id'], newer, expected_version=1)
        before = self.originals()['resource_versions']
        result = self.restore(artifact)
        self.assertEqual(result['content'], DECK.replace('旧内容', '第二页新内容'))
        self.assertEqual((result['version'], result['parent_version']), (3, 2))
        self.assertEqual(result['metadata']['restore_scope'], {'type': 'slide', 'slide_number': 3, 'source_version': 1, 'base_version': 2})
        self.assertEqual(self.originals()['resource_versions'][:2], before)
        self.llm.assert_not_called()

    def test_explicit_page_index_restores_deck_without_section_ids(self):
        original = DECK
        for number in range(1, 4):
            original = original.replace(f' id="slide-{number}"', '')
        artifact = self.create('ppt', original)
        store.append_revision(artifact['id'], original.replace('旧内容', '第二页新内容').replace('结论', '第三页新内容'), expected_version=1)
        result = self.restore(artifact)
        self.assertEqual(result['content'], original.replace('旧内容', '第二页新内容'))
        self.assertEqual(result['parent_version'], 2)
        self.llm.assert_not_called()

    def test_already_equal_target_is_noop_without_version(self):
        artifact = self.create('ppt', DECK)
        store.append_revision(artifact['id'], DECK.replace('旧内容', '第二页新内容'), expected_version=1)
        before = self.originals()
        result = self.restore(artifact)
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['version'], 2)
        self.assertEqual(self.originals(), before)
        self.llm.assert_not_called()

    def test_added_ordinary_target_element_can_be_removed_by_restore(self):
        artifact = self.create('ppt', DECK)
        newer = DECK.replace(SLIDES[2], SLIDES[2].replace('</section>', '<p id="later">后来新增</p></section>'))
        store.append_revision(artifact['id'], newer, expected_version=1)
        self.assertEqual(self.restore(artifact)['content'], DECK)

    def test_incompatible_shell_order_ids_scripts_styles_events_never_write(self):
        variants = [DECK.replace('color:black', 'color:red'), DECK.replace(SLIDES[0], '__SWAP__').replace(SLIDES[1], SLIDES[0]).replace('__SWAP__', SLIDES[1]),
                    DECK.replace('id="slide-3"', 'id="renamed"'), DECK.replace('结论', '<p id="key">重复ID</p>'),
                    DECK.replace('结论', '<script>alert(1)</script>结论'), DECK.replace('结论', '<style>p{color:red}</style>结论'),
                    DECK.replace('<h2>第三页', '<h2 onclick="alert(1)">第三页'), DECK.replace(SLIDES[2], ''),
                    DECK.replace(SLIDES[2], '<div>' + SLIDES[2] + '</div>')]
        for newer in variants:
            with self.subTest(newer=newer):
                artifact = self.create('ppt', DECK)
                store.append_revision(artifact['id'], newer, expected_version=1)
                before = self.originals()
                with self.assertRaises(store.ArtifactError):
                    self.restore(artifact)
                self.assertEqual(self.originals(), before)

    def test_scope_permissions_source_and_concurrency_rejections_never_write(self):
        artifact = self.create('ppt', DECK)
        store.append_revision(artifact['id'], DECK.replace('结论', '新版'), expected_version=1)
        for kwargs in [{'slide_number': 0}, {'slide_number': -1}, {'slide_number': 4}, {'slide_number': True},
                       {'slide_number': 1.5}, {'version': 99}, {'expected_version': 1}, {'student_id': 's2'}, {'user_id': 'stranger'}]:
            with self.subTest(kwargs=kwargs):
                before = self.originals()
                with self.assertRaises(store.ArtifactError):
                    self.restore(artifact, **kwargs)
                self.assertEqual(self.originals(), before)
        document = self.create('document', '正文')
        with self.assertRaises(store.ArtifactError):
            self.restore(document, expected_version=1)


class SlideRestoreHttpTests(unittest.TestCase):
    setUp = chat_fixtures.ArtifactChatIntegrationTests.setUp
    tearDown = chat_fixtures.ArtifactChatIntegrationTests.tearDown

    def create(self):
        artifact = store.create_artifact('session', 'ppt', '课件', DECK, user_id='local_user')
        store.append_revision(artifact['id'], DECK.replace('旧内容', '新内容').replace('结论', '新结论'), expected_version=1)
        return artifact

    def body(self, artifact, **extra):
        return dict(student_id='session', user_id='local_user', version=1, expected_version=2, slide_number=3, **extra)

    def planner_restore(self, message):
        self.planner.return_value = json.dumps({'action':'restore', 'evidence':message, 'target':'第一版'}, ensure_ascii=False), {'finish_reason':'stop'}

    def test_http_scope_and_conflict_permission_validation(self):
        artifact = self.create()
        url = f"/api/artifacts/{artifact['id']}/restore"
        for override, status in [({'user_id':'stranger'},403), ({'expected_version':1},409), ({'slide_number':0},422)]:
            before = len(store.list_versions(artifact['id']))
            response = self.client.post(url, json={**self.body(artifact), **override})
            self.assertEqual(response.status_code, status, response.text)
            self.assertEqual(len(store.list_versions(artifact['id'])), before)
        with patch.object(revision, 'chat_with_diagnostics') as generation:
            response = self.client.post(url, json=self.body(artifact))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['content'], DECK.replace('旧内容', '新内容'))
        generation.assert_not_called()

    def test_sse_explicit_page_restore_uses_latest_even_when_viewing_v1(self):
        artifact = self.create()
        message = '只把第三页恢复到第一版，其他页保持当前'
        self.planner_restore(message)
        with patch.object(revision, 'chat_with_diagnostics') as generation:
            response = self.client.post('/api/chat/talk/stream', json={'student_id':'session', 'user_id':'local_user', 'message':message,
                'resource_ref':{'resource_id':artifact['id'], 'version':1, 'current_version':2}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('event: artifact', response.text)
        self.assertNotIn('event: error', response.text)
        current = store.read_artifact(artifact['id'])
        self.assertEqual(current['content'], DECK.replace('旧内容', '新内容'))
        self.assertEqual(current['parent_version'], 2)
        generation.assert_not_called()

    def test_selected_page_restore_and_whole_restore_ignore_selection_unless_requested(self):
        for message, page_only in [('把这一页恢复到第一版', True), ('恢复第一版', False)]:
            with self.subTest(message=message):
                artifact = self.create()
                newer = store.read_artifact(artifact['id'])
                self.planner_restore(message)
                response = self.client.post('/api/chat/talk', json={'student_id':'session','user_id':'local_user','message':message,
                    'resource_ref':{'resource_id':artifact['id'],'version':2,'current_version':2,'selection':list_slides(newer['content'])[2]['selection']}})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(store.read_artifact(artifact['id'])['content'], DECK.replace('旧内容','新内容') if page_only else DECK)

    def test_chat_noop_and_ordinary_question_create_no_version(self):
        artifact = self.create()
        store.restore_slide_version(artifact['id'], 1, 2, 3, student_id='session', user_id='local_user')
        before = len(store.list_versions(artifact['id']))
        message = '只把第三页恢复到第一版，其他页保持当前'
        self.planner_restore(message)
        response = self.client.post('/api/chat/talk/stream', json={'student_id':'session','user_id':'local_user','message':message,
            'resource_ref':{'resource_id':artifact['id'],'version':3,'current_version':3}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('event: artifact', response.text)
        self.assertEqual(len(store.list_versions(artifact['id'])), before)
        self.planner.return_value = '{"action":"answer","evidence":"","target":""}', {'finish_reason':'stop'}
        self.assertIsNone(artifact_chat.prepare_action('session', 'local_user', '第三页恢复到第一版会怎样？只解释，不操作。',
            {'resource_id':artifact['id'], 'version':3, 'current_version':3}))
        self.assertEqual(len(store.list_versions(artifact['id'])), before)

    def test_ambiguous_multi_page_or_missing_selection_does_not_append(self):
        artifact = self.create()
        for message in ['把这一页恢复到第一版', '把第二页和第三页恢复到第一版', '把前三页恢复到第一版']:
            self.planner_restore(message)
            response = self.client.post('/api/chat/talk', json={'student_id':'session','user_id':'local_user','message':message,
                'resource_ref':{'resource_id':artifact['id'],'version':2,'current_version':2}})
            self.assertEqual(len(store.list_versions(artifact['id'])), 2, response.text)

    def test_current_source_version_still_validates_the_page_scope(self):
        artifact = self.create()
        for message in ['把第99页恢复到第二版', '把这一页恢复到第二版']:
            with self.subTest(message=message):
                self.planner.return_value = json.dumps({'action':'restore', 'evidence':message, 'target':'第二版'}, ensure_ascii=False), {'finish_reason':'stop'}
                with self.assertRaises(store.ArtifactError):
                    artifact_chat.prepare_action('session', 'local_user', message,
                        {'resource_id':artifact['id'], 'version':2, 'current_version':2})
                self.assertEqual(len(store.list_versions(artifact['id'])), 2)


if __name__ == '__main__':
    unittest.main()
