"""Raw HTML generation, mocked provider and temporary SQLite; no paid API calls."""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from agents import interactive_agent, tutor_agent
from agents.interactive_agent import InteractiveGenerationError, parse_interactive_response, validate_interactive_html
from db import database
from services import chat_service


HTML = '''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>欧姆定律参数探索</title>
<style>body{background:#fff;color:#181818}output{color:#8d2727}</style></head>
<body><h1>欧姆定律参数探索</h1><label for="voltage">电压 V</label><input id="voltage" type="range" min="1" max="24" value="12">
<label for="resistance">电阻 Ω</label><input id="resistance" type="number" min="1" value="6">
<p>电流：<output id="current">2 A</output></p><button id="reset" type="button">重置</button>
<p>假设电阻为固定温度下的理想线性电阻，I=U/R。</p>
<script>const u=document.getElementById('voltage');const r=document.getElementById('resistance');
function render(){const voltage=Number(u.value);const resistance=Math.max(1,Number(r.value));
document.getElementById('current').textContent=(voltage/resistance).toFixed(2)+' A';}
u.addEventListener('input',render);r.addEventListener('input',render);
document.getElementById('reset').addEventListener('click',()=>{u.value=12;r.value=6;render();});render();</script>
</body></html>'''

BUTTON_HTML = '''<!DOCTYPE html><html><head><title>步骤探索</title>
<style>body{color:#111;background:white}button{color:#8c2929}</style></head>
<body><button id="next">下一步</button><p id="result">初始状态</p>
<script>let step=0;document.getElementById('next').onclick=()=>{
step++;document.getElementById('result').textContent='步骤 '+step;};</script></body></html>'''


def model_response(html=HTML, finish='stop'):
    return html, {'model': 'mock-provider', 'finish_reason': finish, 'total_tokens': 80}


class InteractiveRawHTMLTests(unittest.TestCase):
    def test_raw_html_css_and_script_are_preserved_exactly(self):
        raw = '\n' + HTML + '\n'
        title, content, report = parse_interactive_response(raw, {'finish_reason': 'stop'})
        self.assertEqual(title, '欧姆定律参数探索')
        self.assertEqual(content, HTML)
        self.assertTrue(report['passed'])
        self.assertFalse(report['runtime_verified'])

    def test_buttons_without_parameter_fields_are_accepted(self):
        self.assertTrue(validate_interactive_html(BUTTON_HTML)['passed'])
        self.assertNotIn('<input', BUTTON_HTML)
        self.assertNotIn('<select', BUTTON_HTML)

    def test_short_complete_document_has_no_feature_or_minimum_size_gate(self):
        html = '<html><head></head><body><button>探索</button></body></html>'
        title, content, report = parse_interactive_response(html)
        self.assertEqual(title, '互动教学')
        self.assertEqual(content, html)
        self.assertTrue(report['passed'])

    def test_only_document_completeness_is_checked_not_script_features(self):
        html = '<html><head></head><body><button onclick="advance()">下一步</button><script>function advance(){}</script></body></html>'
        self.assertTrue(validate_interactive_html(html)['passed'])
        self.assertFalse(interactive_agent.review_interactive_resource({'content': html})['runtime_verified'])

    def test_missing_document_body_empty_and_json_fail(self):
        invalid = [
            '', '   ', None, HTML.replace('</html>', ''), HTML.replace('</body>', ''),
            HTML.replace('</script>', ''), HTML.replace('</style>', ''),
            json.dumps({'html': HTML}),
        ]
        for raw in invalid:
            with self.subTest(str(raw)[:30]), self.assertRaises(InteractiveGenerationError):
                parse_interactive_response(raw)

    def test_fences_bom_comments_and_surrounding_prose_are_cleaned_locally(self):
        cases = [
            ('\ufeff  \n'+HTML+'\n', HTML),
            ('```html\n'+HTML+'\n```', HTML),
            ('\ufeff```HTML\n'+HTML+'\n```', HTML),
            ('<!-- generated document -->\n'+HTML, '<!-- generated document -->\n'+HTML),
            ('下面是完整网页：\n```html\n'+HTML+'\n```\n可直接操作。', HTML),
        ]
        for raw, expected in cases:
            with self.subTest(raw[:35]):
                _, clean, _ = parse_interactive_response(raw)
                self.assertEqual(clean, expected)
                self.assertIn('<style>body{background:#fff;', clean)
                self.assertIn("u.addEventListener('input',render)", clean)

    def test_explicit_head_tag_is_optional(self):
        html = '<html><body><button onclick="next()">下一步</button><script>function next(){}</script></body></html>'
        title, content, report = parse_interactive_response(html)
        self.assertEqual(content, html)
        self.assertEqual(title, '互动教学')
        self.assertTrue(report['passed'])

    def test_rejection_retains_raw_response_outside_user_message(self):
        raw = '```html\n<html><body>unfinished content'
        with self.assertRaises(InteractiveGenerationError) as error:
            parse_interactive_response(raw)
        self.assertEqual(error.exception.raw_response, raw)
        self.assertNotIn('unfinished content', str(error.exception))

    def test_large_complete_html_is_not_rejected_by_a_character_limit(self):
        html = HTML.replace('</body>', '<p>'+'正文'*100000+'</p></body>')
        self.assertGreater(len(html), 180000)
        _, returned, report = parse_interactive_response(html)
        self.assertEqual(returned, html)
        self.assertTrue(report['passed'])

    def test_title_entities_and_fallback(self):
        title, _, _ = parse_interactive_response(HTML.replace('欧姆定律参数探索</title>', '水 &amp; 相变</title>'))
        self.assertEqual(title, '水 & 相变')
        title, _, _ = parse_interactive_response(HTML.replace('<title>欧姆定律参数探索</title>', ''))
        self.assertEqual(title, '互动教学')

    def test_standalone_style_prefixes_and_suffixes_are_removed_only_from_resource_title(self):
        examples = [
            '水的三相变化 · 瑞士国际主义',
            '后现代主义设计审美 / 水的三相变化',
            '水的三相变化 | 构成主义简洁设计审美',
            '水的三相变化 - 简洁美观',
            '瑞士国际主义：水的三相变化',
            '水的三相变化（瑞士国际主义）',
            '(简洁美观) 水的三相变化',
            '水的三相变化 · 【后现代主义设计审美】',
            '[构成主义简洁设计审美] — 水的三相变化',
            '瑞士国际主义 · 水的三相变化（简洁美观）',
        ]
        for original in examples:
            with self.subTest(original):
                html = HTML.replace('欧姆定律参数探索</title>', original + '</title>')
                title, content, _ = parse_interactive_response(html)
                self.assertEqual(title, '水的三相变化')
                self.assertEqual(content, html)

    def test_style_names_that_are_learning_topics_are_preserved(self):
        for original in ['瑞士国际主义的历史', '后现代主义设计审美与建筑', '瑞士国际主义',
                         '如何让版面简洁美观', '水的三相变化：瑞士国际主义的历史',
                         '水的三相变化（瑞士国际主义的历史）']:
            with self.subTest(original):
                html = HTML.replace('欧姆定律参数探索</title>', original + '</title>')
                self.assertEqual(parse_interactive_response(html)[0], original)


class InteractiveModelTests(unittest.TestCase):
    def test_each_generation_selects_one_style_and_sends_it_to_the_model(self):
        styles = ('后现代主义设计审美', '瑞士国际主义', '构成主义简洁设计审美')
        self.assertEqual(interactive_agent.DESIGN_STYLES, styles)
        with patch.object(interactive_agent.random, 'choice', side_effect=styles) as choose, \
                patch.object(interactive_agent, 'chat_with_diagnostics', return_value=model_response()) as provider:
            for style in styles:
                result = interactive_agent.generate_interactive({}, '', '演示单摆运动')
                prompt = provider.call_args.args[0][0]['content']
                self.assertIn(f'本次设计风格：{style}。', prompt)
                self.assertIn('风格只用于视觉设计，标题只写学习主题。', prompt)
                self.assertIn('动画交互为主', prompt)
                self.assertIn('不要使用大范围的圆角和相同的布局', prompt)
                self.assertIn('网页色彩采用白底黑字和暗红点缀。', prompt)
                self.assertEqual(sum(candidate in prompt for candidate in styles), 1)
                self.assertEqual(result['metadata']['design_style'], style)
                self.assertEqual(result['content'], HTML)
        self.assertEqual(choose.call_count, 3)
        self.assertEqual(provider.call_count, 3)
        for call in choose.call_args_list:
            self.assertEqual(call.args, (styles,))

    def test_one_call_plain_question_optional_reference_and_no_json_parameter(self):
        with patch.object(interactive_agent, 'generation_thinking', return_value='enabled'), patch.object(interactive_agent, 'chat_with_diagnostics', return_value=model_response()) as provider:
            result = interactive_agent.generate_interactive({'secret_profile_field': 'do-not-send'}, '线性电阻 I=U/R。', '欧姆定律')
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result['type'], 'interactive')
        self.assertEqual(result['content'], HTML)
        self.assertEqual(result['metadata']['format'], 'html')
        self.assertNotIn('interactive_features', result['metadata'])
        self.assertNotIn('repair_attempted', result['metadata'])
        messages = provider.call_args.args[0]
        self.assertEqual(messages[1]['content'], '欧姆定律\n\n参考资料：\n线性电阻 I=U/R。')
        self.assertNotIn('secret_profile_field', str(messages))
        self.assertNotIn('response_format', provider.call_args.kwargs)
        self.assertEqual(provider.call_args.kwargs['thinking'], 'enabled')
        self.assertEqual(provider.call_args.kwargs['max_tokens'], 393216)

    def test_blank_reference_is_not_attached(self):
        with patch.object(interactive_agent, 'chat_with_diagnostics', return_value=model_response(BUTTON_HTML)) as provider:
            interactive_agent.generate_interactive({}, '   ', '水的三相变化')
        self.assertEqual(provider.call_args.args[0][1]['content'], '水的三相变化')
        self.assertEqual(provider.call_count, 1)

    def test_truncated_output_fails_after_one_call_without_repair(self):
        with patch.object(interactive_agent, 'chat_with_diagnostics', return_value=model_response(finish='length')) as provider:
            with self.assertRaisesRegex(InteractiveGenerationError, '长度上限'):
                interactive_agent.generate_interactive({}, '', '水的三相变化')
        self.assertEqual(provider.call_count, 1)

    def test_fenced_html_uses_one_call_and_is_saved_as_clean_source(self):
        with patch.object(interactive_agent, 'chat_with_diagnostics', return_value=model_response('下面是网页：\n```html\n'+BUTTON_HTML+'\n```')) as provider:
            result = interactive_agent.generate_interactive({}, '', '水的三相变化')
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result['content'], BUTTON_HTML)

    def test_malformed_or_transport_failure_has_no_retry_or_fake_result(self):
        with patch.object(interactive_agent, 'chat_with_diagnostics', return_value=model_response('<html>unfinished')) as provider:
            with self.assertRaises(InteractiveGenerationError):
                interactive_agent.generate_interactive({}, '', '问题')
        self.assertEqual(provider.call_count, 1)
        with patch.object(interactive_agent, 'chat_with_diagnostics', side_effect=RuntimeError('secret-provider-details')) as provider:
            with self.assertRaises(InteractiveGenerationError) as error:
                interactive_agent.generate_interactive({}, '', '问题')
        self.assertEqual(provider.call_count, 1)
        self.assertNotIn('secret-provider-details', str(error.exception))

    def test_review_does_not_require_features_or_claim_runtime_verification(self):
        review = interactive_agent.review_interactive_resource({'content': BUTTON_HTML, 'metadata': {'format': 'html'}})
        self.assertTrue(review['passed'])
        self.assertFalse(review['runtime_verified'])
        self.assertEqual(interactive_agent.review_interactive_resource({'content': '<html>'})['decision'], 'reject')

    def test_no_legacy_fallback_or_review_repair_for_interactive(self):
        with patch.object(chat_service, 'generate_interactive', side_effect=InteractiveGenerationError('failed')) as generator:
            with self.assertRaises(InteractiveGenerationError):
                chat_service._generate_fast_stream_resource_with_fallback('interactive', '问题', {}, '', 'test')
        self.assertEqual(generator.call_count, 1)
        with patch.object(chat_service, 'generate_interactive') as generator:
            self.assertIsNone(chat_service._repair_resource_once('interactive', '问题', {}, '', 'test', ['invalid']))
        generator.assert_not_called()


class InteractivePlannerTests(unittest.TestCase):
    def test_webpage_goal_does_not_add_lecture_or_ppt(self):
        for message in ['生成互动网页，用它解释欧姆定律', '生成可交互 HTML，包含动画和操作参数', '生成交互式网页，加入图解和案例']:
            with self.subTest(message):
                self.assertEqual(tutor_agent._explicit_product_tools(message), ['interactive'])

    def test_explicit_companions_and_opt_in(self):
        examples = {
            '生成互动网页，同时给一份文字讲解': {'interactive', 'lecture'},
            '请先给我一份文字讲解，再制作一个互动网页。': {'interactive', 'lecture'},
            '先给我两道练习题，再制作互动网页': {'interactive', 'quiz'},
            '生成互动网页和思维导图': {'interactive', 'mindmap'},
            '先做一份PPT，再制作互动网页': {'interactive', 'ppt'},
        }
        for message, expected in examples.items():
            with self.subTest(message):
                self.assertEqual(set(tutor_agent._explicit_product_tools(message)), expected)
        plan = tutor_agent.TutorPlan('理解', [], '选择', [{'tool': 'lecture'}, {'tool': 'interactive'}, {'tool': 'ppt'}])
        self.assertEqual(tutor_agent._respect_requested_products(plan, '解释欧姆定律', ['interactive']).intents, ['interactive'])
        self.assertEqual(tutor_agent._respect_requested_products(plan, '欧姆定律是什么？', []).intents, ['lecture'])

    @patch('agents.tutor_agent._recent_records', return_value=[])
    @patch('agents.tutor_agent.get_learning_signal_summary', return_value={})
    @patch('agents.tutor_agent.get_active_memory_text', return_value='')
    @patch('agents.tutor_agent.chat', side_effect=RuntimeError('planner must not run'))
    def test_required_interactive_skips_planning_model(self, provider, *_):
        result = tutor_agent.plan_tutor_actions('test', '水的三相变化', {}, required_tools=['interactive'])
        self.assertEqual(result.intents, ['interactive'])
        provider.assert_not_called()


class InteractiveSQLitePipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = getattr(database._connections, 'connection', None)
        self.connection = sqlite3.connect(str(Path(self.directory.name)/'interactive.sqlite3'), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        database._connections.connection = self.connection
        database.init_db()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(chat_service, 'get_knowledge_base_mode', return_value='blank'))
        self.stack.enter_context(patch.object(chat_service, 'usage_scope', side_effect=lambda *a: nullcontext()))
        self.stack.enter_context(patch.object(chat_service, '_log_timing'))
        self.stack.enter_context(patch.object(chat_service, 'retrieve_resource_context', return_value={
            'context': '参考资料：欧姆定律 I=U/R。', 'references': [{'source_file': '测试资料', 'excerpt': '线性电阻 I=U/R'}],
            'query': '欧姆定律', 'matched_chunks': 1, 'top_k': 1,
        }))
        self.stack.enter_context(patch.object(tutor_agent, 'get_learning_signal_summary', return_value={}))
        self.provider = self.stack.enter_context(patch.object(interactive_agent, 'chat_with_diagnostics', return_value=model_response(BUTTON_HTML)))

    def tearDown(self):
        self.stack.close()
        self.connection.close()
        if self.previous is None:
            del database._connections.connection
        else:
            database._connections.connection = self.previous
        self.directory.cleanup()

    def test_short_question_sse_preserves_raw_html_without_features_or_extra_models(self):
        with patch.object(chat_service, 'extract_profile', side_effect=RuntimeError('profile must not run')) as profile:
            events = list(chat_service.process_message_stream('interactive-test', '水的三相变化', user_id='interactive-user', required_intents=['interactive']))
        profile.assert_not_called()
        self.assertEqual(self.provider.call_count, 1)
        names = [event['event'] for event in events]
        self.assertIn('resource_start', names)
        self.assertNotIn('resource_delta', names)
        self.assertNotIn('resource_error', names)
        resource = json.loads(next(event['data'] for event in events if event['event'] == 'resource'))['resource']
        self.assertEqual(resource['type'], 'interactive')
        self.assertEqual(resource['content'], BUTTON_HTML)
        self.assertNotIn('interactive_features', resource['metadata'])
        saved = self.connection.execute('SELECT type,content,metadata_json FROM resources').fetchall()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['content'], BUTTON_HTML)
        metadata = json.loads(saved[0]['metadata_json'])
        self.assertEqual(metadata['format'], 'html')
        self.assertIn(metadata['design_style'], interactive_agent.DESIGN_STYLES)
        self.assertEqual(metadata['references'][0]['source_file'], '测试资料')
        self.assertFalse(metadata['structure_validation']['runtime_verified'])
        done = json.loads(events[-1]['data'])
        self.assertEqual((done['resource_count'], done['planned_count'], done['failed_count']), (1, 1, 0))
        assistant = self.connection.execute("SELECT content FROM conversations WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(assistant['content'], done['summary'])
        self.assertTrue(self.provider.call_args.args[0][1]['content'].startswith('水的三相变化'))
        self.assertNotIn('Tutor Agent', self.provider.call_args.args[0][1]['content'])

    def test_nonstream_uses_same_raw_generator(self):
        result = chat_service.process_message('interactive-nonstream', '水的三相变化', user_id='interactive-user', required_intents=['interactive'])
        self.assertEqual(result['intents'], ['interactive'])
        self.assertEqual(result['resources'][0]['content'], BUTTON_HTML)
        self.assertIn(result['resources'][0]['metadata']['design_style'], interactive_agent.DESIGN_STYLES)
        self.assertEqual(self.provider.call_count, 1)

    def test_truncation_emits_error_once_and_does_not_save_or_claim_success(self):
        self.provider.return_value = model_response(finish='length')
        events = list(chat_service.process_message_stream('interactive-failure', '水的三相变化', user_id='interactive-user', required_intents=['interactive']))
        names = [event['event'] for event in events]
        self.assertEqual(self.provider.call_count, 1)
        self.assertIn('resource_error', names)
        self.assertNotIn('resource', names)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM resources').fetchone()[0], 0)
        done = json.loads(events[-1]['data'])
        self.assertEqual((done['resource_count'], done['planned_count'], done['failed_count']), (0, 1, 1))
        self.assertIn('未能', done['summary'])
        self.assertNotIn('已完成', done['summary'])
        assistant = self.connection.execute("SELECT content FROM conversations WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(assistant['content'], done['summary'])
        error = json.loads(next(event['data'] for event in events if event['event'] == 'resource_error'))
        self.assertNotIn('生成中', error['title'])
        self.assertIn('长度上限', error['detail'])


if __name__ == '__main__':
    unittest.main()
