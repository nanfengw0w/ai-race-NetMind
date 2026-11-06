"""Understanding checks use isolated storage and deterministic provider evidence."""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_artifact_store import ArtifactStoreTests
from services import understanding_checks as checks


class UnderstandingChecksTests(ArtifactStoreTests):
    def make_check(self, source='水在加热时温度升高。'):
        artifact = store.create_artifact('s1', 'document', '温度', source, user_id='u1')
        question = {'prompt': '解释加热的影响。', 'expected_answer': '加热使水温升高', 'hint': '关注温度变化',
                    'criteria': [{'id': 'c1', 'description': '解释升温', 'source_quote': source}]}
        with patch.object(checks, '_call', return_value=({'questions': [question, dict(question)]}, {'model': 'test'})):
            return checks.create_check('s1', 'u1', artifact, '考考我')

    def grade(self, check, status='met', quote='水温升高'):
        question = check['questions'][0]
        response = {'rationale': '依据答案', 'criteria': [{'id': 'c1', 'status': status,
                    'student_answer_quote': quote, 'rationale': '解释了变化'}]}
        with patch.object(checks, '_call', return_value=(response, {'model': 'test'})) as call:
            result = checks.submit_answer(check['id'], 's1', 'u1', question['id'],
                                         question['latest_attempt']['id'], '水温升高', 'request1')
        return result, call

    def test_no_leak_persistence_hints_retries_and_idempotency(self):
        check = self.make_check()
        self.assertNotIn('expected_answer', json.dumps(check))
        q = check['questions'][0]
        hinted = checks.request_hint(check['id'], 's1', 'u1', q['id'], q['latest_attempt']['id'])
        self.assertEqual(hinted['questions'][0]['latest_attempt']['mode'], 'hinted')
        result, call = self.grade(hinted)
        attempt = result['questions'][0]['latest_attempt']
        self.assertEqual(attempt['result']['overall'], 'demonstrated')
        with patch.object(checks, '_call') as duplicate:
            same = checks.submit_answer(check['id'], 's1', 'u1', q['id'], attempt['id'], '水温升高', 'request1')
            duplicate.assert_not_called()
        self.assertEqual(result, same)
        retry = checks.retry_check(check['id'], 's1', 'u1', q['id'], attempt['id'])
        self.assertEqual(retry['questions'][0]['latest_attempt']['mode'], 'retry')
        self.assertEqual(len(retry['questions'][0]['attempts']), 2)
        self.assertEqual(retry, checks.get_check(check['id'], 's1', 'u1'))
        self.assert_error(403, checks.get_check, check['id'], 's1', 'u2')

    def test_fabricated_answer_quote_fails_without_claiming_correct(self):
        result, _ = self.grade(self.make_check(), quote='答案中不存在')
        attempt = result['questions'][0]['latest_attempt']
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['result']['overall'], 'uncertain')

    def test_inflight_replay_and_model_call_release_database_lock(self):
        check = self.make_check()
        q = check['questions'][0]
        attempt_id = q['latest_attempt']['id']
        def provider(*args):
            connection = sqlite3.connect(database.DB_PATH, timeout=0.1)
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
            connection.close()
            replay = checks.submit_answer(check['id'], 's1', 'u1', q['id'], attempt_id, '水温升高', 'request1')
            self.assertEqual(replay['questions'][0]['latest_attempt']['status'], 'grading')
            self.assert_error(409, checks.submit_answer, check['id'], 's1', 'u1', q['id'], attempt_id, '别的答案', 'request2')
            return {'rationale': '有依据', 'criteria': [{'id': 'c1', 'status': 'met', 'student_answer_quote': '水温升高', 'rationale': '已说明'}]}, {}
        with patch.object(checks, '_call', side_effect=provider) as call:
            result = checks.submit_answer(check['id'], 's1', 'u1', q['id'], attempt_id, '水温升高', 'request1')
            self.assertEqual(call.call_count, 1)
        self.assertEqual(result['questions'][0]['latest_attempt']['status'], 'graded')

    def test_generation_rejects_unsupported_source(self):
        artifact = self.artifact()
        item = {'prompt': '问题', 'expected_answer': '答案', 'hint': '提示',
                'criteria': [{'id': 'c', 'description': '依据', 'source_quote': '编造来源'}]}
        with patch.object(checks, '_call', return_value=({'questions': [item, item]}, {})):
            self.assert_error(502, checks.create_check, 's1', 'u1', artifact, '考考我')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM understanding_checks').fetchone()[0], 0)

    def test_contradiction_not_pass_and_html_omits_scripts(self):
        result, _ = self.grade(self.make_check(), status='contradicted')
        self.assertEqual(result['questions'][0]['latest_attempt']['result']['overall'], 'not_yet')
        parser = checks._Visible()
        parser.feed('<style>答案</style><h1>可见</h1><script>secret()</script>正文')
        self.assertEqual(parser.parts, ['可见', '正文'])

    def test_stale_grading_recovery_is_explicit_and_no_provider_call(self):
        check = self.make_check()
        data = checks._load(self.db, check['id'], 's1', 'u1')
        q = data['questions'][0]
        attempt = q['attempts'][0]
        attempt.update(status='grading', submitted_at=datetime.now(timezone.utc).isoformat(), grading_token='old')
        checks._save(self.db, data)
        self.db.commit()
        self.assertFalse(checks.get_check(check['id'], 's1', 'u1')['questions'][0]['latest_attempt']['recovery_available'])
        self.assert_error(409, checks.retry_check, check['id'], 's1', 'u1', q['id'], attempt['id'])
        attempt['submitted_at'] = (datetime.now(timezone.utc) - timedelta(seconds=checks.GRADING_LEASE_SECONDS + 1)).isoformat()
        attempt['future_private_secret'] = 'never expose'
        checks._save(self.db, data)
        self.db.commit()
        public = checks.get_check(check['id'], 's1', 'u1')
        self.assertTrue(public['questions'][0]['latest_attempt']['recovery_available'])
        self.assertNotIn('never expose', json.dumps(public))
        with patch.object(checks, '_call') as provider:
            recovered = checks.retry_check(check['id'], 's1', 'u1', q['id'], attempt['id'])
            provider.assert_not_called()
        attempts = recovered['questions'][0]['attempts']
        self.assertEqual(attempts[0]['result']['overall'], 'uncertain')
        self.assertEqual(attempts[1]['mode'], 'retry')
        self.assertEqual(attempts[1]['status'], 'pending')

    def test_call_rejects_truncation_missing_model_and_oversized_input(self):
        for diagnostics in ({'finish_reason': 'length', 'model': 'actual'}, {'finish_reason': 'content_filter', 'model': 'actual'}, {'finish_reason': 'stop'}):
            with patch.object(checks.llm_client, 'chat_with_diagnostics', return_value=('{}', diagnostics)):
                with self.assertRaises(ValueError):
                    checks._call('task', {'source': '正文'})
        with patch.object(checks, 'input_budget', return_value=50), patch.object(checks.llm_client, 'chat_with_diagnostics') as provider:
            self.assert_error(413, checks._call, 'task', {'source': '长' * 100})
            provider.assert_not_called()

    def test_repeated_hint_preserves_first_exposure_timestamp(self):
        check = self.make_check()
        q = check['questions'][0]
        args = (check['id'], 's1', 'u1', q['id'], q['latest_attempt']['id'])
        with patch.object(checks, '_now', return_value='first'):
            first = checks.request_hint(*args)
        with patch.object(checks, '_now', return_value='second'):
            second = checks.request_hint(*args)
        self.assertEqual(first, second)
        self.assertEqual(second['questions'][0]['latest_attempt']['hint_requested_at'], 'first')

    def test_failed_model_response_preserves_private_raw_and_diagnostics(self):
        for raw, reason in (('invalid JSON PRIVATE', 'stop'), ('{"incomplete":true}', 'length')):
            check = self.make_check()
            q = check['questions'][0]
            diagnostics = {'model': 'actual-provider-model', 'finish_reason': reason, 'total_tokens': 42}
            with patch.object(checks.llm_client, 'chat_with_diagnostics', return_value=(raw, diagnostics)):
                result = checks.submit_answer(check['id'], 's1', 'u1', q['id'], q['latest_attempt']['id'], '我的答案', 'r1')
            public = result['questions'][0]['latest_attempt']
            self.assertEqual(public['status'], 'failed')
            self.assertEqual(public['result']['overall'], 'uncertain')
            self.assertNotIn('raw_response', json.dumps(public))
            private = checks._load(self.db, check['id'], 's1', 'u1')['questions'][0]['attempts'][0]
            self.assertEqual(private['model'], diagnostics)
            self.assertEqual(private['raw_grade']['raw_response'], raw)

    def test_submit_route_applies_usage_identity(self):
        from api.understanding_checks import SubmitRequest, submit
        body = SubmitRequest(student_id='s1', user_id='u1', question_id='q1', attempt_id='a1', user_answer='答', request_id='r')
        with patch('api.understanding_checks.usage_scope') as scope, patch.object(checks, 'submit_answer', return_value={'id': 'c'}):
            self.assertEqual(submit('c', body), {'check': {'id': 'c'}})
            scope.assert_called_once_with('u1', 's1')
            scope.return_value.__enter__.assert_called_once()

    def test_sibling_feedback_marks_pending_answer_and_hint_keeps_exposure(self):
        check = self.make_check()
        result, _ = self.grade(check)
        sibling = result['questions'][1]
        attempt = sibling['latest_attempt']
        self.assertEqual(attempt['mode'], 'feedback')
        self.assertTrue(attempt['feedback_exposed'])
        self.assertIn('feedback_exposed_at', attempt)
        self.assertNotIn('feedback_source_question_id', attempt)
        private = checks._load(self.db, check['id'], 's1', 'u1')['questions'][1]['attempts'][0]
        self.assertEqual(private['feedback_source_question_id'], 'q1')
        self.assertEqual(private['feedback_source_attempt_id'], check['questions'][0]['latest_attempt']['id'])
        hinted = checks.request_hint(check['id'], 's1', 'u1', sibling['id'], attempt['id'])
        hinted_attempt = hinted['questions'][1]['latest_attempt']
        self.assertEqual(hinted_attempt['mode'], 'hinted')
        self.assertTrue(hinted_attempt['feedback_exposed'])
        self.assertEqual(hinted_attempt['feedback_exposed_at'], attempt['feedback_exposed_at'])

    def test_feedback_does_not_relabel_concurrently_sealed_sibling(self):
        check = self.make_check()
        data = checks._load(self.db, check['id'], 's1', 'u1')
        sibling = data['questions'][1]['attempts'][0]
        sibling.update(status='grading', submitted_at=checks._now(), grading_token='sealed')
        checks._save(self.db, data)
        self.db.commit()
        result, _ = self.grade(check)
        sealed = result['questions'][1]['latest_attempt']
        self.assertEqual(sealed['mode'], 'independent')
        self.assertFalse(sealed['feedback_exposed'])

    def test_failed_grading_feedback_still_marks_siblings(self):
        result, _ = self.grade(self.make_check(), quote='编造答案引用')
        self.assertEqual(result['questions'][0]['latest_attempt']['status'], 'failed')
        self.assertEqual(result['questions'][1]['latest_attempt']['mode'], 'feedback')

    def test_sibling_hint_exposure_is_durable_and_private_sources_hidden(self):
        check = self.make_check()
        first, second = check['questions']
        result = checks.request_hint(check['id'], 's1', 'u1', first['id'], first['latest_attempt']['id'])
        sibling = result['questions'][1]['latest_attempt']
        self.assertEqual(sibling['mode'], 'feedback')
        self.assertTrue(sibling['group_hint_exposed'])
        self.assertFalse(sibling['feedback_exposed'])
        self.assertNotIn('group_hint_source_question_id', sibling)
        self.assertEqual(result, checks.get_check(check['id'], 's1', 'u1'))
        private = checks._load(self.db, check['id'], 's1', 'u1')['questions'][1]['attempts'][0]
        self.assertEqual(private['group_hint_source_question_id'], first['id'])
        graded, _ = self.grade(result)
        after = graded['questions'][1]['latest_attempt']
        self.assertTrue(after['group_hint_exposed'])
        self.assertTrue(after['feedback_exposed'])
        self.assertEqual(after['group_hint_exposed_at'], sibling['group_hint_exposed_at'])


from services import artifact_store as store
from db import database
