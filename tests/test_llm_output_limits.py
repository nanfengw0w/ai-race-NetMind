"""Verify output budgets at the mocked SDK request boundary, without API or DB writes."""
import ast
import io
import json
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from agents import (assess_agent, interactive_agent, lab_agent, lecture_agent,
                    mindmap_agent, path_agent, ppt_agent, profile_agent, quiz_agent, tutor_agent)
from services import chat_service, llm_client, long_context


class OutputBudgetTests(unittest.TestCase):
    def setUp(self):
        self.text = 'model response'
        self.responses = []
        self.create = Mock(side_effect=self.complete)
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(llm_client, 'get_client', return_value=self.client))
        self.stack.enter_context(patch.object(llm_client, 'get_default_model', return_value='mock-provider'))
        self.stack.enter_context(patch.object(llm_client, 'record_token_usage'))

    def tearDown(self):
        self.stack.close()

    def complete(self, **request):
        text = self.responses.pop(0) if self.responses else self.text
        if request.get('stream'):
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])])
        return SimpleNamespace(model='mock-provider',
                               choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason='stop')],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10, total_tokens=20))

    def assert_requests_have_maximum(self, expected_count=1):
        self.assertEqual(self.create.call_count, expected_count)
        for call in self.create.call_args_list:
            self.assertEqual(call.kwargs['max_tokens'], 393216)

    def test_normal_stream_and_diagnostics_defaults_send_explicit_maximum(self):
        message = [{'role': 'user', 'content': 'test'}]
        for function in (llm_client.chat, llm_client.chat_with_diagnostics, llm_client.chat_stream):
            for arguments in ({}, {'max_tokens': None}, {'max_tokens': llm_client.LLM_MAX_OUTPUT_TOKENS}):
                with self.subTest(function=function.__name__, arguments=arguments):
                    self.create.reset_mock()
                    result = function(message, **arguments)
                    if function is llm_client.chat_stream:
                        list(result)
                    self.assert_requests_have_maximum()

    def test_all_resource_generators_reach_sdk_with_maximum(self):
        profile = {'当前课程': '测试课程', '薄弱知识点': []}
        cases = [
            ('profile', '{}', lambda: profile_agent.extract_profile('测试问题')),
            ('mindmap', 'mindmap\n  root((主题))', lambda: mindmap_agent.generate_mindmap(profile, '', '测试问题')),
            ('lab', '实操说明', lambda: lab_agent.generate_lab(profile, '', '测试问题')),
            ('path', '{}', lambda: path_agent.generate_learning_path(profile)),
            ('assessment', '评估说明', lambda: assess_agent.generate_assessment_report(profile, [], '测试问题')),
            ('quiz', '[]', lambda: quiz_agent.generate_quiz(profile, '', '测试问题')),
            ('ppt', '<html data-netmind-deck="v2"><body><section class="slide">测试内容</section></body></html>', lambda: ppt_agent.generate_ppt(profile, '', '测试问题')),
            ('lecture', json.dumps({'markdown': '真实模型正文', 'bundle': None}), lambda: lecture_agent.generate_lecture(profile, '', '测试问题')),
            ('interactive', '<html><body><button>探索</button></body></html>', lambda: interactive_agent.generate_interactive(profile, '', '测试问题')),
        ]
        for name, text, call in cases:
            with self.subTest(name):
                self.text = text
                self.create.reset_mock()
                with redirect_stdout(io.StringIO()):
                    call()
                self.assert_requests_have_maximum()

    def test_lecture_repair_has_the_same_budget(self):
        self.responses = ['unstructured model prose', json.dumps({'markdown': '修复后正文', 'bundle': None})]
        lecture_agent.generate_lecture({}, '', '测试问题')
        self.assert_requests_have_maximum(expected_count=2)

    def test_planner_request_has_maximum_without_touching_student_database(self):
        self.text = json.dumps({'goal': '理解', 'focus_topics': [], 'strategy': '说明', 'actions': [{'tool': 'lecture', 'instruction': '说明'}]})
        with patch.object(tutor_agent, '_recent_records', return_value=[]), \
                patch.object(tutor_agent, 'get_learning_signal_summary', return_value={}), \
                patch.object(tutor_agent, 'get_active_memory_text', return_value=''):
            tutor_agent.plan_tutor_actions('unit-test', '星星为何闪烁？', {})
        self.assert_requests_have_maximum()

    def test_long_context_extract_and_reduce_use_maximum(self):
        long_context._extract_chunk(1, 1, 'source', 'question')
        self.assert_requests_have_maximum()
        self.create.reset_mock()
        with patch.object(long_context, 'estimate_tokens', return_value=long_context.REDUCE_INPUT_TOKENS+1), \
                patch.object(long_context, 'split_text', return_value=['evidence batch']):
            long_context._reduce_summaries(['source evidence'], 'question')
        self.assert_requests_have_maximum()

    def test_sse_lab_uses_maximum_at_actual_stream_request(self):
        self.text = '实操流式正文'
        with patch.object(chat_service, '_default_course', return_value='测试课程'):
            result = chat_service._generate_streaming_resource('lab', '测试问题', {}, '', lambda delta: None)
        self.assertEqual(result['content'], self.text)
        self.assert_requests_have_maximum()
        self.assertTrue(self.create.call_args.kwargs['stream'])

    def test_ordinary_talk_and_sse_talk_inherit_client_maximum(self):
        prepared = ({}, [{'role': 'user', 'content': 'test'}], {})
        with (
            patch.object(chat_service, '_ensure_student'),
            patch.object(chat_service, '_prepare_chat_request', return_value=prepared),
            patch.object(chat_service, '_save_conversation', return_value=1),
        ):
            chat_service.process_chat_message('unit-test', 'test')
            self.assert_requests_have_maximum()
            self.create.reset_mock()
            list(chat_service.process_chat_message_stream('unit-test', 'test'))
            self.assert_requests_have_maximum()
            self.assertTrue(self.create.call_args.kwargs['stream'])

    def test_no_live_callsite_retains_a_small_or_omitted_explicit_budget(self):
        root = Path(__file__).resolve().parents[1] / 'backend'
        found = []
        for path in root.rglob('*.py'):
            if path.name == 'llm_client.py':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg != 'max_tokens':
                            continue
                        found.append((path.name, node.lineno))
                        self.assertTrue(isinstance(keyword.value, ast.Name) and keyword.value.id == 'LLM_MAX_OUTPUT_TOKENS'
                                        or isinstance(keyword.value, ast.Subscript) and isinstance(keyword.value.value, ast.Name) and keyword.value.value.id == 'config',
                                        f'Unexpected explicit output limit at {path}:{node.lineno}')
        self.assertGreaterEqual(len(found), 8)


if __name__ == '__main__':
    unittest.main()
