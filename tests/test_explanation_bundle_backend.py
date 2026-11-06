"""No paid requests or real databases; test the existing lecture/SSE integration."""
import copy
import json
import sys
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from agents import lecture_agent
from services import chat_service
from services.explanation_bundle import (
    ExplanationValidationError, parse_lecture_response, recover_readable_markdown,
    review_lecture_resource, validate_bundle,
)


def bundle_fixture():
    return {
        'schema_version': 1, 'title': '光合作用中的能量转换', 'summary': '解释光能怎样参与有机物合成，而非声称完成生物实验。',
        'nodes': [
            {'id': 'light', 'label': '光能', 'detail': '为光反应提供能量'},
            {'id': 'reaction', 'label': '光反应', 'detail': '将光能转换为可利用的化学能'},
            {'id': 'sugar', 'label': '有机物合成', 'detail': '后续反应使用能量固定碳'},
        ],
        'edges': [{'from': 'light', 'to': 'reaction', 'label': '驱动能量转换'}, {'from': 'reaction', 'to': 'sugar', 'label': '为后续反应供能'}],
        'steps': [
            {'title': '光能被吸收', 'caption': '光能被吸收后，光反应才获得驱动能量转换所需的能量。', 'active_nodes': ['light', 'reaction'], 'active_edges': [0], 'duration': 5},
            {'title': '后续反应利用能量', 'caption': '后续反应利用先前形成的化学能，使碳能够进入有机物合成过程。', 'active_nodes': ['reaction', 'sugar'], 'active_edges': [1], 'duration': 6},
        ],
    }


def envelope(bundle=None, markdown='## 为什么需要光能\n光反应把光能转为后续反应可使用的化学能。'):
    return json.dumps({'markdown': markdown, 'bundle': bundle}, ensure_ascii=False)


def response(raw, finish='stop'):
    return raw, {'model': 'mock-provider', 'finish_reason': finish, 'total_tokens': 100}


class BundleValidationTests(unittest.TestCase):
    def test_valid_plain_data_roundtrip(self):
        source = bundle_fixture()
        self.assertEqual(validate_bundle(source), source)
        text, bundle = parse_lecture_response(envelope(source))
        self.assertTrue(text.startswith('##'))
        self.assertEqual(bundle['edges'][0]['from'], 'light')

    def test_counts_lengths_and_reference_bounds(self):
        cases = [
            ('duplicate id', lambda b: b['nodes'][1].update(id='light')),
            ('edge node', lambda b: b['edges'][0].update(to='absent')),
            ('step node', lambda b: b['steps'][0].update(active_nodes=['absent'])),
            ('negative edge', lambda b: b['steps'][0].update(active_edges=[-1])),
            ('missing edge', lambda b: b['steps'][0].update(active_edges=[2])),
            ('bool index', lambda b: b['steps'][0].update(active_edges=[True])),
            ('short duration', lambda b: b['steps'][0].update(duration=1)),
            ('long duration', lambda b: b['steps'][0].update(duration=13)),
            ('nan duration', lambda b: b['steps'][0].update(duration=float('nan'))),
            ('long label', lambda b: b['nodes'][0].update(label='字'*81)),
            ('empty caption', lambda b: b['steps'][0].update(caption='')),
            ('unknown executable field', lambda b: b.update(javascript='alert(1)')),
            ('excess nodes', lambda b: b.update(nodes=b['nodes']*5)),
            ('excess edges', lambda b: b.update(edges=b['edges']*13)),
            ('excess steps', lambda b: b.update(steps=b['steps']*5)),
            ('version bool', lambda b: b.update(schema_version=True)),
        ]
        for label, mutate in cases:
            with self.subTest(label):
                data = bundle_fixture()
                mutate(data)
                with self.assertRaises(ExplanationValidationError):
                    validate_bundle(data)

    def test_short_caption_is_valid(self):
        data = bundle_fixture()
        data['steps'][0]['caption'] = '光能被吸收'
        self.assertEqual(validate_bundle(data)['steps'][0]['caption'], '光能被吸收')

    def test_all_generic_nodes_are_rejected(self):
        data = bundle_fixture()
        for node, label in zip(data['nodes'], ['核心概念', '原理', '应用']):
            node['label'] = label
        with self.assertRaises(ExplanationValidationError):
            validate_bundle(data)

    def test_null_visual_is_valid_but_not_claimed_verified(self):
        with patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response(envelope(None))) as provider:
            resource = lecture_agent.generate_lecture({}, '', '用一句话解释光合作用')
        self.assertEqual(provider.call_count, 1)
        self.assertIsNone(resource['metadata']['explanation_bundle'])
        self.assertIsNone(resource['metadata']['visualization_validation']['passed'])
        self.assertNotIn('visualization_error', resource['metadata'])

    def test_recovery_never_displays_raw_broken_json(self):
        self.assertEqual(recover_readable_markdown('{"markdown":"已收到的完整正文", "bundle":'), '已收到的完整正文')
        self.assertEqual(recover_readable_markdown('{"markdown":"未结束'), '')
        self.assertEqual(recover_readable_markdown('## 真实文字\n无需图解'), '## 真实文字\n无需图解')


class LectureGenerationTests(unittest.TestCase):
    def test_single_call_produces_both_forms_and_legacy_shape(self):
        with patch.object(lecture_agent, 'generation_thinking', return_value='enabled'), patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response(envelope(bundle_fixture()))) as provider:
            result = lecture_agent.generate_lecture({'当前课程': '生物学'}, '参考段落', '解释光合作用')
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result['type'], 'lecture')
        self.assertIsInstance(result['content'], str)
        self.assertEqual(result['metadata']['explanation_bundle'], bundle_fixture())
        self.assertTrue(result['metadata']['visualization_validation']['passed'])
        self.assertEqual(provider.call_args.kwargs['thinking'], 'enabled')
        self.assertEqual(provider.call_args.kwargs['max_tokens'], 393216)
        self.assertIn('参考段落', provider.call_args.args[0][1]['content'])

    def test_one_repair_can_produce_valid_bundle(self):
        invalid = bundle_fixture()
        invalid['steps'][0]['active_nodes'] = ['missing']
        with patch.object(lecture_agent, 'chat_with_diagnostics', side_effect=[response(envelope(invalid)), response(envelope(bundle_fixture()))]) as provider:
            result = lecture_agent.generate_lecture({}, '', '解释光合作用')
        self.assertEqual(provider.call_count, 2)
        self.assertTrue(result['metadata']['visualization_repair_attempted'])
        self.assertTrue(result['metadata']['visualization_validation']['passed'])

    def test_failed_repair_preserves_real_text_and_failed_status(self):
        invalid = bundle_fixture()
        invalid['steps'][0]['active_edges'] = [99]
        with patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response(envelope(invalid, '这是模型实际返回的可读正文。'))) as provider:
            result = lecture_agent.generate_lecture({}, '', '解释光合作用')
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(result['content'], '这是模型实际返回的可读正文。')
        self.assertIsNone(result['metadata']['explanation_bundle'])
        self.assertFalse(result['metadata']['visualization_validation']['passed'])
        self.assertIn('visualization_error', result['metadata'])
        review = review_lecture_resource(result, {'tool': 'lecture'})
        self.assertFalse(review['passed'])
        self.assertEqual(review['decision'], 'keep_text')

    def test_repair_provider_error_keeps_initial_text(self):
        with patch.object(lecture_agent, 'chat_with_diagnostics', side_effect=[response('## 实际正文\n解释光合作用。'), RuntimeError('sensitive provider internals')]) as provider:
            result = lecture_agent.generate_lecture({}, '', '解释光合作用')
        self.assertEqual(provider.call_count, 2)
        self.assertTrue(result['content'].startswith('## 实际正文'))
        self.assertNotIn('sensitive', result['metadata']['visualization_error'])

    def test_initial_provider_failure_does_not_fabricate_resource(self):
        with patch.object(lecture_agent, 'chat_with_diagnostics', side_effect=RuntimeError('offline')) as provider:
            with self.assertRaises(RuntimeError):
                lecture_agent.generate_lecture({}, '', '解释光合作用')
        self.assertEqual(provider.call_count, 1)

    def test_no_readable_text_after_repair_is_an_error(self):
        with patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response('{broken')) as provider:
            with self.assertRaises(ExplanationValidationError):
                lecture_agent.generate_lecture({}, '', '解释光合作用')
        self.assertEqual(provider.call_count, 2)

    def test_length_finish_is_not_claimed_valid(self):
        with patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response(envelope(bundle_fixture()), finish='length')):
            result = lecture_agent.generate_lecture({}, '', '解释光合作用')
        self.assertFalse(result['metadata']['visualization_validation']['passed'])
        self.assertIsNone(result['metadata']['explanation_bundle'])

    def test_streaming_helper_uses_same_generator_without_partial_json(self):
        emit = Mock()
        with patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response(envelope(bundle_fixture()))) as provider:
            result = chat_service._generate_streaming_resource('lecture', '解释光合作用', {}, '参考资料', emit)
        self.assertEqual(provider.call_count, 1)
        emit.assert_not_called()
        self.assertTrue(result['metadata']['explanation_bundle'])

    def test_lecture_transport_failure_is_not_retried_by_legacy_fallbacks(self):
        with patch.object(chat_service, 'generate_lecture', side_effect=RuntimeError('offline')) as generator:
            with self.assertRaises(RuntimeError):
                chat_service._generate_fast_stream_resource_with_fallback('lecture', '问题', {}, '', 'session')
        self.assertEqual(generator.call_count, 1)


class ExistingPipelineTests(unittest.TestCase):
    def pipeline_patches(self, stack):
        profile = {'当前课程': '生物学', '薄弱知识点': []}
        plan = SimpleNamespace(actions=[{'tool': 'lecture', 'instruction': '解释光合作用', 'reason': '理解能量转换'}],
                               intents=['lecture'], strategy='图示因果与状态变化', to_dict=lambda: {'actions': [{'tool': 'lecture'}]})
        values = {
            '_ensure_student': lambda *a: None, '_prepare_input': lambda message, *a: (message, message, {}),
            '_prepare_resource_context': lambda *a: ('', {'references': []}),
            '_save_conversation': lambda *a: None, '_get_latest_profile': lambda *a: profile,
            '_get_resource_profile': lambda *a, **k: (profile, 'cache'), '_save_profile': lambda *a: None,
            'plan_tutor_actions': lambda *a, **k: plan,
            'retrieve_resource_context': lambda *a, **k: {'context': '光合作用参考资料', 'references': [{'source_file': '课本', 'excerpt': '实际检索原文'}], 'matched_chunks': 1, 'query': '光合作用', 'top_k': 1},
            '_save_resource': lambda *a: 73, 'save_tutor_run': lambda *a: 8,
            'usage_scope': lambda *a: nullcontext(), '_log_timing': lambda *a: None,
        }
        for name, value in values.items():
            stack.enter_context(patch.object(chat_service, name, value))

    def test_actual_sse_events_preserve_contract_and_references(self):
        with ExitStack() as stack:
            self.pipeline_patches(stack)
            provider = stack.enter_context(patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response(envelope(bundle_fixture()))))
            events = list(chat_service.process_message_stream('session', '解释光合作用', user_id='user', required_intents=['lecture']))
        self.assertEqual(provider.call_count, 1)
        names = [item['event'] for item in events]
        self.assertIn('resource_start', names)
        self.assertNotIn('resource_delta', names)
        self.assertEqual(names[-1], 'done')
        payload = json.loads(next(item['data'] for item in events if item['event'] == 'resource'))
        self.assertEqual(payload['resource']['id'], 73)
        self.assertEqual(payload['resource']['status'], 'ready')
        self.assertEqual(payload['resource']['metadata']['references'][0]['source_file'], '课本')
        self.assertTrue(payload['resource']['metadata']['explanation_bundle'])
        self.assertTrue(any('统一生成' in json.loads(item['data']).get('message', '') for item in events if item['event'] == 'status'))

    def test_failed_visual_has_no_extra_outer_repair(self):
        invalid = bundle_fixture()
        invalid['edges'][0]['to'] = 'missing'
        with ExitStack() as stack:
            self.pipeline_patches(stack)
            provider = stack.enter_context(patch.object(lecture_agent, 'chat_with_diagnostics', return_value=response(envelope(invalid))))
            result = chat_service.process_message('session', '解释光合作用', user_id='user', required_intents=['lecture'])
        self.assertEqual(provider.call_count, 2)
        self.assertIn('visualization_error', result['resources'][0]['metadata'])
        self.assertFalse(result['reflections'][0]['passed'])

    def test_normal_question_preserves_existing_profile_without_new_weakness(self):
        existing = {'当前课程': '生物学', '薄弱知识点': ['已有误区']}
        with patch.object(chat_service, 'get_knowledge_base_mode', return_value='blank'), patch.object(chat_service, 'topic_from_query', return_value='光合作用'):
            for text in ['光合作用是什么？', '围绕光合作用生成图解', '我不是不懂光合作用，只想看看图解']:
                with self.subTest(text):
                    result = chat_service._merge_profile_hints(existing, text)
                    self.assertEqual(result['薄弱知识点'], ['已有误区'])
            result = chat_service._merge_profile_hints(existing, '我不懂光合作用，卡住了')
        self.assertEqual(result['薄弱知识点'], ['已有误区', '光合作用'])
        self.assertEqual(existing['薄弱知识点'], ['已有误区'])


if __name__ == '__main__':
    unittest.main()
