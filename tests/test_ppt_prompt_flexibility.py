"""Raw model slides retain their content/design; no API calls or real database writes."""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from agents import ppt_agent, tutor_agent
from db import database
from services import chat_service


HTML = '''<!DOCTYPE html>
<html lang="zh-CN" data-netmind-deck="v2"><head><meta charset="UTF-8"><title>水 &amp; 相变</title>
<style>.overview{display:grid;grid-template-columns:2fr 1fr}.timeline{display:flex}.slide{overflow:auto}svg{max-width:100%}</style></head>
<body><main><section class="slide overview"><h1>从粒子运动理解物态</h1><svg viewBox="0 0 200 100"><circle cx="50" cy="50" r="10"/></svg></section>
<section class="slide timeline"><h2>相变过程</h2><ol><li>加热</li><li>熔化</li><li>汽化</li></ol></section>
<section class="slide"><blockquote>温度和压强共同决定相态。</blockquote><button id="explore">观察</button><output id="result">初始</output></section></main>
<script>document.getElementById('explore').onclick=()=>{document.getElementById('result').textContent='变化';};</script>
</body></html>'''


def model_response(html=HTML, finish='stop'):
    return html, {'model': 'mock-provider', 'finish_reason': finish, 'max_tokens': 393216}


class PptRawHTMLTests(unittest.TestCase):
    def test_distinct_layouts_css_javascript_and_page_order_are_preserved_exactly(self):
        title, content, report = ppt_agent.parse_ppt_response(HTML)
        self.assertEqual(title, '水 & 相变')
        self.assertEqual(content, HTML)
        self.assertEqual(report['slide_count'], 3)
        self.assertTrue(report['passed'])
        self.assertFalse(report['runtime_verified'])

    def test_one_page_or_many_pages_are_not_filled_or_discarded(self):
        for count in (1, 2, 5, 25):
            with self.subTest(count=count):
                html = '<html data-netmind-deck="v2"><body>' + ''.join(
                    f'<section class="custom slide"><h1>第{index}页</h1></section>' for index in range(count)
                ) + '</body></html>'
                _, actual, report = ppt_agent.parse_ppt_response(html)
                self.assertEqual(actual, html)
                self.assertEqual(report['slide_count'], count)

    def test_long_prose_and_every_list_item_are_kept_without_cards_or_text_slicing(self):
        prose = '这段解释应完整保留，不能裁成短句。' * 10000
        items = ''.join(f'<li>原始第{index}项</li>' for index in range(40))
        html = HTML.replace('<blockquote>温度和压强共同决定相态。</blockquote>', f'<p>{prose}</p><ul>{items}</ul>')
        self.assertGreater(len(html), 150000)
        _, actual, report = ppt_agent.parse_ppt_response(html)
        self.assertEqual(actual, html)
        self.assertIn(prose, actual)
        self.assertIn('原始第39项', actual)
        self.assertEqual(actual.count('<li>'), 43)
        self.assertEqual(report['slide_count'], 3)
        self.assertNotIn('机制卡片', actual)

    def test_bom_whitespace_and_optional_markdown_fence_are_removed_locally(self):
        for raw in ('\n' + HTML + '\n', '\ufeff  ' + HTML, '```html\n' + HTML + '\n```'):
            with self.subTest(raw[:20]):
                self.assertEqual(ppt_agent.parse_ppt_response(raw)[1], HTML)

    def test_nested_content_sections_do_not_add_slides(self):
        html = '<html data-netmind-deck="v2"><body><section class="slide"><section><p>内部内容</p></section></section></body></html>'
        self.assertEqual(ppt_agent.parse_ppt_response(html)[2]['slide_count'], 1)

    def test_plain_model_document_gets_program_marker_without_changing_pages(self):
        original = HTML.replace(' data-netmind-deck="v2"', '')
        raw = '以下是完整课件：\n```html\n' + original + '\n```\n保存后可以演示。'
        title, actual, report = ppt_agent.parse_ppt_response(raw)
        self.assertEqual(actual, HTML)
        self.assertEqual(actual.split('<body>', 1)[1], original.split('<body>', 1)[1])
        self.assertEqual(title, '水 & 相变')
        self.assertEqual(report['slide_count'], 3)

    def test_marked_document_legal_comments_and_source_stay_unchanged(self):
        html = '<!-- source -->\n' + HTML + '\n<!-- end -->'
        self.assertEqual(ppt_agent.parse_ppt_response(html)[1], html)

    def test_marker_changes_real_root_instead_of_identical_tag_inside_comment(self):
        original = '<!-- <html lang="zh-CN"> -->\n<html lang="zh-CN"><body><section class="slide">内容</section></body></html>'
        _, actual, _ = ppt_agent.parse_ppt_response(original)
        self.assertTrue(actual.startswith('<!-- <html lang="zh-CN"> -->'))
        self.assertIn('\n<html lang="zh-CN" data-netmind-deck="v2">', actual)

    def test_empty_json_missing_pages_and_incomplete_documents_fail(self):
        invalid = [
            '', None, json.dumps({'title': '旧 JSON 方案', 'slides': []}),
            HTML.replace('data-netmind-deck="v2"', 'data-netmind-deck="unsupported"'),
            HTML.replace('class="slide', 'class="page'),
            HTML.replace('</html>', ''), HTML.replace('</body>', ''),
            HTML.replace('</script>', ''), HTML.replace('</style>', ''),
            HTML.replace('</section>', '', 1),
            HTML.replace('</body>', '<html></html></body>'),
        ]
        for raw in invalid:
            with self.subTest(str(raw)[:40]), self.assertRaises(ppt_agent.PptGenerationError):
                ppt_agent.parse_ppt_response(raw)

    def test_finish_length_is_rejected_even_when_output_looks_complete(self):
        with self.assertRaisesRegex(ppt_agent.PptGenerationError, '长度上限') as failure:
            ppt_agent.parse_ppt_response(HTML, {'finish_reason': 'length'})
        self.assertEqual(failure.exception.raw_response, HTML)
        self.assertNotIn('<script>', str(failure.exception))

    def test_prompt_has_no_json_layout_list_theme_presets_or_page_quota(self):
        prompt = ppt_agent.SYSTEM_PROMPT
        self.assertIn('自主安排内容、页数和视觉设计', prompt)
        self.assertIn('完整保留', prompt)
        self.assertIn('保留翻页代码、页码和索引', prompt)
        self.assertIn('以动画演示为主', prompt)
        self.assertIn('不要使用大范围的圆角和相同的布局', prompt)
        for old_template in ('只输出 JSON', 'feature(', 'pipeline(', '墨水经典', '至少三', '三张卡'):
            self.assertNotIn(old_template, prompt)

    def test_style_labels_are_removed_only_from_resource_title_not_html(self):
        for style in ppt_agent.DESIGN_STYLES:
            html = HTML.replace('水 &amp; 相变</title>', f'水 &amp; 相变 · {style}</title>')
            title, source, _ = ppt_agent.parse_ppt_response(html)
            self.assertEqual(title, '水 & 相变')
            self.assertEqual(source, html)
        for topic in ('瑞士国际主义', '后现代主义设计审美的历史'):
            html = HTML.replace('水 &amp; 相变</title>', topic + '</title>')
            self.assertEqual(ppt_agent.parse_ppt_response(html)[0], topic)


class PptModelTests(unittest.TestCase):
    def test_complete_navigation_template_and_one_random_style_reach_model(self):
        styles = ('后现代主义设计审美', '瑞士国际主义', '构成主义简洁设计审美')
        self.assertEqual(ppt_agent.DESIGN_STYLES, styles)
        template = ppt_agent.NAVIGATION_TEMPLATE_PATH.read_text(encoding='utf-8')
        self.assertIn('data-netmind-navigation="legacy-dots"', template)
        self.assertIn('id="netmind-ppt-navigation"', template)
        self.assertIn('<!-- SLIDES_HERE -->', template)
        with patch.object(ppt_agent.random, 'choice', side_effect=styles) as choose, \
                patch.object(ppt_agent, 'chat_with_diagnostics', return_value=model_response()) as provider:
            for style in styles:
                resource = ppt_agent.generate_ppt({}, '相变资料', '水的三相变化')
                prompt = provider.call_args.args[0][0]['content']
                self.assertTrue(prompt.endswith(template))
                self.assertIn(f'本次设计风格：{style}。', prompt)
                self.assertEqual(sum(candidate in prompt for candidate in styles), 1)
                self.assertEqual(resource['metadata']['design_style'], style)
                self.assertEqual(resource['content'], HTML)
                self.assertEqual(provider.call_args.args[0][1]['content'], '水的三相变化\n\n参考资料：\n相变资料')
                self.assertEqual(provider.call_args.kwargs['max_tokens'], 393216)
        self.assertEqual(choose.call_count, 3)
        self.assertEqual(provider.call_count, 3)
        for call in choose.call_args_list:
            self.assertEqual(call.args, (styles,))

    @patch('agents.tutor_agent._recent_records', return_value=[])
    @patch('agents.tutor_agent.get_learning_signal_summary', return_value={})
    @patch('agents.tutor_agent.get_active_memory_text', return_value='')
    @patch('agents.tutor_agent.chat', side_effect=AssertionError('selected PPT must not call the planning model'))
    def test_selected_ppt_skips_planning_model_and_preserves_explicit_companions(self, provider, *_):
        examples = [
            ('水的三相变化', ['ppt'], {'ppt'}),
            ('水的三相变化', ['ppt', 'mindmap'], {'ppt', 'mindmap'}),
            ('先写一份文字讲解，再做PPT课件', ['ppt'], {'lecture', 'ppt'}),
            ('先给两道练习题，再做PPT', ['ppt'], {'quiz', 'ppt'}),
            ('先做PPT，再制作互动教学', ['ppt'], {'ppt', 'interactive'}),
        ]
        for question, required, expected in examples:
            with self.subTest(question=question, required=required):
                plan = tutor_agent.plan_tutor_actions('test', question, {}, required_tools=required)
                self.assertEqual(set(plan.intents), expected)
        provider.assert_not_called()

    def test_one_call_full_budget_and_only_question_optional_reference(self):
        with patch.object(ppt_agent, 'generation_thinking', return_value='enabled'), patch.object(ppt_agent, 'chat_with_diagnostics', return_value=model_response()) as provider:
            resource = ppt_agent.generate_ppt({'private_profile': 'not sent'}, '固液气三种物态。', '水的三相变化')
        provider.assert_called_once()
        messages = provider.call_args.args[0]
        self.assertEqual(messages[1]['content'], '水的三相变化\n\n参考资料：\n固液气三种物态。')
        self.assertNotIn('private_profile', str(messages))
        self.assertNotIn('response_format', provider.call_args.kwargs)
        self.assertEqual(provider.call_args.kwargs['max_tokens'], 393216)
        self.assertEqual(provider.call_args.kwargs['thinking'], 'enabled')
        self.assertEqual(resource['content'], HTML)
        self.assertEqual(resource['metadata']['format'], 'netmind-html-deck-v2')
        self.assertEqual(resource['metadata']['slide_count'], 3)
        self.assertEqual(resource['metadata']['generation_diagnostics']['finish_reason'], 'stop')

    def test_bad_html_and_provider_failure_have_no_retry_or_fake_fallback(self):
        with patch.object(ppt_agent, 'chat_with_diagnostics', return_value=model_response('{}')) as provider:
            with self.assertRaises(ppt_agent.PptGenerationError):
                ppt_agent.generate_ppt({}, '', '问题')
        provider.assert_called_once()
        with patch.object(ppt_agent, 'chat_with_diagnostics', side_effect=RuntimeError('private provider details')) as provider:
            with self.assertRaises(ppt_agent.PptGenerationError) as failure:
                ppt_agent.generate_ppt({}, '', '问题')
        provider.assert_called_once()
        self.assertNotIn('private provider details', str(failure.exception))

    def test_service_fallback_and_review_paths_never_regenerate_ppt(self):
        for entry in (chat_service._generate_resource_with_fallback, chat_service._generate_fast_stream_resource_with_fallback):
            with self.subTest(entry.__name__), patch.object(chat_service, 'generate_ppt', side_effect=ppt_agent.PptGenerationError('failed')) as provider:
                with self.assertRaises(ppt_agent.PptGenerationError):
                    entry('ppt', '问题', {}, '', 'test')
                provider.assert_called_once()
        with patch.object(chat_service, 'generate_ppt') as provider:
            self.assertIsNone(chat_service._repair_resource_once('ppt', '问题', {}, '', 'test', ['invalid']))
        provider.assert_not_called()
        review = chat_service._review_resource({'type': 'ppt', 'content': HTML, 'metadata': {'format': 'netmind-html-deck-v2'}}, {})
        self.assertTrue(review['passed'])
        self.assertFalse(review['runtime_verified'])


class PptSQLitePipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = getattr(database._connections, 'connection', None)
        self.connection = sqlite3.connect(str(Path(self.directory.name) / 'ppt.sqlite3'), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        database._connections.connection = self.connection
        database.init_db()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(chat_service, 'get_knowledge_base_mode', return_value='blank'))
        self.stack.enter_context(patch.object(chat_service, 'usage_scope', side_effect=lambda *a: nullcontext()))
        self.stack.enter_context(patch.object(chat_service, '_log_timing'))
        self.stack.enter_context(patch.object(chat_service, 'retrieve_resource_context', return_value={
            'context': '水的固液气。', 'references': [], 'query': '相变', 'matched_chunks': 1, 'top_k': 1,
        }))
        self.stack.enter_context(patch.object(tutor_agent, 'get_learning_signal_summary', return_value={}))
        self.provider = self.stack.enter_context(patch.object(ppt_agent, 'chat_with_diagnostics', return_value=model_response()))

    def tearDown(self):
        self.stack.close()
        self.connection.close()
        if self.previous is None:
            del database._connections.connection
        else:
            database._connections.connection = self.previous
        self.directory.cleanup()

    def test_sse_saves_full_html_without_partial_json_or_extra_generation(self):
        with patch.object(chat_service, 'extract_profile', side_effect=AssertionError('selected PPT must not infer a profile')) as profile, \
                patch.object(tutor_agent, 'chat', side_effect=AssertionError('selected PPT must not call the planning model')) as planner:
            events = list(chat_service.process_message_stream('ppt-test', '水的相变', user_id='ppt-user', required_intents=['ppt']))
        profile.assert_not_called()
        planner.assert_not_called()
        names = [event['event'] for event in events]
        self.assertEqual(self.provider.call_count, 1)
        self.assertNotIn('resource_delta', names)
        self.assertNotIn('resource_error', names)
        saved = self.connection.execute('SELECT type,content,metadata_json FROM resources').fetchall()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['content'], HTML)
        self.assertEqual(json.loads(saved[0]['metadata_json'])['slide_count'], 3)
        done = json.loads(events[-1]['data'])
        self.assertEqual((done['resource_count'], done['failed_count']), (1, 0))
        self.assertEqual(self.provider.call_args.args[0][1]['content'], '水的相变\n\n参考资料：\n水的固液气。')

    def test_nonstream_selected_ppt_uses_same_single_raw_generator(self):
        with patch.object(chat_service, 'extract_profile', side_effect=AssertionError('selected PPT must not infer a profile')), \
                patch.object(tutor_agent, 'chat', side_effect=AssertionError('selected PPT must not call the planning model')):
            result = chat_service.process_message('ppt-nonstream', '水的相变', user_id='ppt-user', required_intents=['ppt'])
        self.provider.assert_called_once()
        self.assertEqual(result['intents'], ['ppt'])
        self.assertEqual(result['resources'][0]['content'], HTML)

    def test_truncation_reports_failure_once_without_saving_a_template(self):
        self.provider.return_value = model_response(finish='length')
        events = list(chat_service.process_message_stream('ppt-fail', '制作水的相变PPT课件', user_id='ppt-user', required_intents=['ppt']))
        self.assertEqual(self.provider.call_count, 1)
        names = [event['event'] for event in events]
        self.assertIn('resource_error', names)
        self.assertNotIn('resource', names)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM resources').fetchone()[0], 0)
        done = json.loads(events[-1]['data'])
        self.assertEqual((done['resource_count'], done['failed_count']), (0, 1))
        self.assertNotIn('已完成', done['summary'])


if __name__ == '__main__':
    unittest.main()
